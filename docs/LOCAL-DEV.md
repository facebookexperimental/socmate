# Local development

This guide is the no-container path: how to run socmate end-to-end on a plain Linux or macOS box and iterate on the orchestrator code. If you just want to try the pipeline, [GitHub Codespaces](https://codespaces.new/facebookexperimental/socmate) or `docker build .` are faster — see [the README](../README.md).

The Linux path is also covered in `README.md` "Option C". This doc adds the **iteration** story — running tests, debugging a single agent, attaching a profiler — that's missing from the install-focused README.

---

## Quick install (Linux)

The five steps are scripted; `scripts/install_toolchain.sh` runs them sequentially and verifies versions at the end.

```bash
git clone https://github.com/facebookexperimental/socmate.git
cd socmate
./scripts/install_toolchain.sh
```

Then activate the venv and check the toolchain:

```bash
source orchestrator/.venv/bin/activate
make preflight    # should print {"ok": true}
```

If preflight is unhappy, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

For the manual step-by-step (OSS-CAD-Suite tarball, Node, venv, volare), see README "Option C".

---

## First end-to-end run

```bash
export CLAUDE_CODE_OAUTH_TOKEN=...   # see AUTHENTICATION.md
make demo                            # 8-bit adder, ~5-10 min
```

`make demo` runs the pipeline on `examples/adder8/blocks.yaml` — RTL gen, lint, testbench, simulation, synthesis. The full run lands artifacts in:

- `rtl/adder8/adder8.v` — the synthesized-quality Verilog
- `tb/cocotb/test_adder8.py` — the generated testbench
- `syn/output/adder8/` — Yosys synthesis output (netlist, area report)
- `.socmate/pipeline_results.json` — final status
- `.socmate/traces.db` — OpenTelemetry spans (`make traces` to inspect)

For your own design, drop a `blocks:` stanza into `orchestrator/config.yaml` and run `make pipeline`.

---

## Iterating on a single agent

Most orchestrator bugs are in one of `orchestrator/langchain/agents/*.py`. The fast loop:

```bash
# 1. Run the unit tests for that agent
pytest orchestrator/tests/test_<agent>.py -v

# 2. Hit the live agent with a tiny input
python -c "
import asyncio
from orchestrator.langchain.agents.rtl_agent import RtlAgent
asyncio.run(RtlAgent().generate(...))
"
```

Live agent calls cost real Claude credits; keep the input small.

For changes to the LangGraph state machines (`orchestrator/langgraph/*.py`), the pipeline checkpointing means you can interrupt mid-run, edit code, and resume — the graph picks up from the last completed node:

```bash
make pipeline
# Ctrl-C after a few blocks
# edit orchestrator/langgraph/pipeline_graph.py
make pipeline    # resumes from .socmate/pipeline_checkpoint.db
```

---

## Tests

```bash
make test              # skips live_llm, requires_nix, e2e -- fast (~30 s)
make test-all          # everything, including live LLM tests (costs credits)
ruff check orchestrator/   # lint
```

Conventions:

- Tests live alongside the module: `orchestrator/tests/test_<module>.py`
- Mark live-LLM tests with `@pytest.mark.live_llm`
- Mark tests that need nix-shell EDA tools with `@pytest.mark.requires_nix`
- Mark full-pipeline tests with `@pytest.mark.e2e`

CI runs the fast subset on every PR; the [nightly e2e workflow](../.github/workflows/nightly-e2e.yml) runs `make demo` end-to-end with the Docker image.

---

## Debugging a stuck block

```bash
make traces                                          # span overview
sqlite3 .socmate/traces.db                           # SQL prompt
ls .socmate/step_logs/<block>/                       # verbatim LLM I/O per step
tail -F .socmate/pipeline_events.jsonl | jq -c .     # live state transitions
```

The pipeline is intentionally observable: every LLM call writes to `.socmate/llm_calls.jsonl` (with token counts and cost), every state transition writes to `.socmate/pipeline_events.jsonl`, and every span writes to `.socmate/traces.db` via the SqliteSpanExporter.

---

## Repo layout

```
orchestrator/
  langchain/agents/      # LLM agents (RTL, testbench, debug, integration, ...)
  langchain/prompts/     # System prompts loaded by agents at startup
  langgraph/             # State machines (architecture, pipeline, backend, tapeout)
  pdk/                   # PDK config + tech-file resolution
  pdk_templates/         # Yosys/OpenROAD/Magic/netgen Tcl templates
  telemetry/             # OpenTelemetry init + SQLite span exporter
  tests/                 # pytest suite
  config.yaml            # block registry, model defaults, PDK paths
  mcp_server.py          # MCP server (interactive use with Claude Code)
scripts/                 # install_toolchain.sh, *-nix.sh wrappers, runpod entrypoint
examples/                # Reference designs (adder8, ...)
docs/                    # This directory
run_pipeline.py          # CLI entrypoint (headless mode)
```

---

## Submitting changes

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the PR workflow. In short: fork, branch, run `ruff check` + `make test`, open a PR. The nightly e2e workflow will exercise your changes against the adder8 reference design.
