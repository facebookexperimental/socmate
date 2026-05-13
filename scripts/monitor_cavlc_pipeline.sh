#!/usr/bin/env bash
set -u

ROOT=${SOCMATE_PROJECT_ROOT:-/home/ubuntu/socmate}
RUN_LOG=${SOCMATE_RUN_LOG:-$ROOT/.socmate/run-20260513-top-codex-gpt55-cavlc.log}
MONITOR_LOG=${SOCMATE_MONITOR_LOG:-$ROOT/.socmate/cavlc_monitor.log}
INTERVAL=${SOCMATE_MONITOR_INTERVAL:-600}
POSTPROCESS_PY=${SOCMATE_POSTPROCESS_PY:-$ROOT/scripts/postprocess_cavlc_run.py}
PYTHON_BIN=${SOCMATE_POSTPROCESS_PYTHON:-/home/ubuntu/socmate-codec/venv/bin/python}

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$ROOT/venv/bin/python"
fi

mkdir -p "$ROOT/.socmate"

log() {
  printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$MONITOR_LOG"
}

pipeline_running() {
  tmux has-session -t socmate-top 2>/dev/null && return 0
  pgrep -f 'run_top_headless.py --poll-s 30' >/dev/null 2>&1 && return 0
  return 1
}

pipeline_succeeded() {
  rg -q '\[top\] pipeline finished|pipeline_finished|Pipeline finished|pipeline result.*PASS|completed": "12/12"' \
    "$RUN_LOG" "$ROOT/.socmate/pipeline_events.jsonl" 2>/dev/null
}

pipeline_failed() {
  rg -q 'Traceback|RuntimeError|Exception|FAILED|failed": true|pipeline failed|interrupted' \
    "$RUN_LOG" "$ROOT/.socmate/pipeline_events.jsonl" 2>/dev/null
}

log "starting CAVLC SocMate monitor; interval=${INTERVAL}s run_log=$RUN_LOG"

while true; do
  log "poll: checking SocMate top run"

  if [[ -f "$ROOT/.socmate/postprocess_cavlc.done" ]]; then
    log "done marker exists; monitor exiting"
    exit 0
  fi

  if pipeline_running; then
    active=$(ps -eo pid,etime,pcpu,pmem,cmd | rg 'run_top_headless.py|codex exec' | rg -v rg | tr '\n' '; ' || true)
    progress=$(tail -300 "$RUN_LOG" 2>/dev/null | rg '\[pipeline\]|completed|Block:|INTEGRATION|FAILED|Traceback' | tail -8 | tr '\n' ' ' || true)
    log "pipeline alive; active=[$active]"
    [[ -n "$progress" ]] && log "recent progress: $progress"
    sleep "$INTERVAL"
    continue
  fi

  if pipeline_succeeded; then
    log "pipeline appears complete; running CAVLC postprocess hook"
    "$PYTHON_BIN" "$POSTPROCESS_PY" >> "$MONITOR_LOG" 2>&1
    rc=$?
    if [[ "$rc" -eq 0 ]]; then
      log "postprocess completed; monitor exiting"
      exit 0
    fi
    log "postprocess exited rc=$rc; will retry after interval"
    sleep "$INTERVAL"
    continue
  fi

  if pipeline_failed; then
    log "pipeline not running and failure markers are present; leaving monitor active for inspection/retry"
  else
    log "pipeline not running, but no success marker was found yet"
  fi

  tail -80 "$RUN_LOG" 2>/dev/null >> "$MONITOR_LOG" || true
  sleep "$INTERVAL"
done
