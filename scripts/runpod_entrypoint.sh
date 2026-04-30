#!/usr/bin/env bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
#
# runpod_entrypoint.sh -- container entrypoint for the socmate Docker image.
#
# Picks one of three modes based on the SOCMATE_MODE env var:
#
#   SOCMATE_MODE=pipeline   Run `make pipeline` headlessly (default for CI / batch)
#   SOCMATE_MODE=mcp        Start the MCP server on stdio (for `claude --mcp ...`)
#   SOCMATE_MODE=mcp-http   Start the MCP server behind mcp-proxy on $MCP_PORT (default 8765)
#   SOCMATE_MODE=shell      Drop into an interactive bash (default for `docker run -it`)
#
# Authentication:
#   - If $CLAUDE_CODE_OAUTH_TOKEN is set, it is exported untouched -- the
#     Claude CLI picks it up via its standard env lookup.
#   - Else if $ANTHROPIC_API_KEY is set, it is exported as well.
#   - Else, if no token is found, the entrypoint prints a clear error and
#     exits non-zero before starting any pipeline work, so users don't burn
#     a RunPod hour on a misconfigured pod.
#
# Optional overrides:
#   SOCMATE_MODEL=sonnet-4.6   Pin a specific model (short name or full ID)
#   SOCMATE_REQUIREMENTS_FILE  Path to a text file with architecture requirements
#                              (used in mcp / pipeline modes that need a starter prompt)

set -euo pipefail

# --- Project root -----------------------------------------------------------
PROJECT_ROOT="${SOCMATE_PROJECT_ROOT:-/socmate}"
cd "${PROJECT_ROOT}"

mode="${SOCMATE_MODE:-shell}"
echo "[socmate] entrypoint: mode=${mode} project_root=${PROJECT_ROOT}"

# --- Auth check (skipped in shell mode so users can poke around) ------------
require_auth() {
    if [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
        echo "[socmate] auth: using CLAUDE_CODE_OAUTH_TOKEN"
        export CLAUDE_CODE_OAUTH_TOKEN
        return 0
    fi
    if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
        echo "[socmate] auth: using ANTHROPIC_API_KEY"
        export ANTHROPIC_API_KEY
        return 0
    fi
    cat <<'MSG' >&2

[socmate] ERROR: no Claude credentials found.

Set one of:

  ANTHROPIC_API_KEY        -- API key from https://console.anthropic.com
  CLAUDE_CODE_OAUTH_TOKEN  -- OAuth token from `claude setup-token`

For RunPod, add the variable in the pod template's "Environment Variables"
section. For docker run, pass it via -e:

  docker run -e ANTHROPIC_API_KEY=sk-ant-... socmate:latest

Set SOCMATE_MODE=shell to drop into a debug shell without auth.

MSG
    exit 2
}

# --- Optional model override echo -------------------------------------------
if [[ -n "${SOCMATE_MODEL:-}" ]]; then
    echo "[socmate] model override: SOCMATE_MODEL=${SOCMATE_MODEL}"
fi

# --- Preflight: bail early if the toolchain is broken (skipped in shell) ---
run_preflight() {
    echo "[socmate] running preflight..."
    if ! python3 -c "
from orchestrator.langgraph.pipeline_helpers import preflight_check
import json, sys
r = preflight_check(['pipeline', 'backend'])
print(json.dumps(r, indent=2))
sys.exit(0 if r['ok'] else 1)
"; then
        echo "[socmate] preflight failed; aborting." >&2
        exit 3
    fi
}

case "${mode}" in
    shell)
        echo "[socmate] dropping into bash. Try: make help"
        exec /bin/bash "$@"
        ;;

    pipeline)
        require_auth
        run_preflight
        echo "[socmate] starting frontend pipeline (run_pipeline.py)"
        exec python3 run_pipeline.py "$@"
        ;;

    mcp)
        require_auth
        run_preflight
        echo "[socmate] starting MCP server on stdio"
        exec python3 -m orchestrator.mcp_server "$@"
        ;;

    mcp-http)
        require_auth
        run_preflight
        port="${MCP_PORT:-8765}"
        if ! command -v mcp-proxy >/dev/null 2>&1; then
            echo "[socmate] mcp-proxy not installed; installing now (npm)"
            npm install -g @modelcontextprotocol/proxy >/dev/null 2>&1 \
                || pip install mcp-proxy >/dev/null 2>&1 \
                || { echo "[socmate] cannot install mcp-proxy" >&2; exit 4; }
        fi
        echo "[socmate] starting MCP server behind HTTP proxy on :${port}"
        exec mcp-proxy --port "${port}" -- python3 -m orchestrator.mcp_server "$@"
        ;;

    test)
        run_preflight || true
        echo "[socmate] running test suite (excluding live_llm and e2e)"
        exec python3 -m pytest orchestrator/tests/ -v \
            -m "not live_llm and not requires_nix and not e2e" "$@"
        ;;

    *)
        echo "[socmate] unknown SOCMATE_MODE=${mode}" >&2
        echo "  valid: shell | pipeline | mcp | mcp-http | test" >&2
        exit 1
        ;;
esac
