#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
run_pipeline.py -- CLI pipeline runner using LangGraph.

Drives the full ASIC pipeline via the LangGraph pipeline graph:
  1. Load blocks from config.yaml, sorted by tier
  2. Per-block: Generate RTL -> Lint -> Generate Testbench -> Simulate -> Synthesize
  3. On failure: DebugAgent diagnoses, LLM classifier decides retry/escalate
  4. Report results

In headless/CI mode, interrupts (ask_human) are auto-resolved:
- retry until max_attempts exhausted, then skip.

For interactive use via Claude Code, use the MCP tools instead:
  start_pipeline() -> get_pipeline_state() -> resume_pipeline() / pause_pipeline()

Usage:
    source venv/bin/activate
    python run_pipeline.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time

from orchestrator.langgraph.pipeline_helpers import (
    PROJECT_ROOT,
    LIBERTY_FILE,
    load_config,
    get_sorted_block_queue,
    log,
    CYAN,
    GREEN,
    RED,
    YELLOW,
)
from orchestrator.telemetry import init_telemetry

# Bootstrap OTel before any graph code so spans land in .socmate/traces.db.
# (Previously only mcp_server.py initialised telemetry; CLI runs produced
# zero spans.)
init_telemetry(str(PROJECT_ROOT))


MAX_ATTEMPTS = 5
TARGET_CLOCK_MHZ = 50.0


async def main():
    start_time = time.time()

    log(f"\n{'#'*60}", CYAN)
    log("  socmate -- AI-Orchestrated ASIC Pipeline", CYAN)
    log(f"  Target: Sky130 130nm @ {TARGET_CLOCK_MHZ} MHz", CYAN)
    log(f"{'#'*60}\n", CYAN)

    # Check prerequisites
    if not LIBERTY_FILE.exists():
        log("ERROR: Sky130 PDK not found at .pdk/sky130A/", RED)
        log("Run: pip install volare && volare enable --pdk sky130 --pdk-root .pdk", RED)
        sys.exit(1)

    if not shutil.which("verilator"):
        log("ERROR: Verilator not found", RED)
        sys.exit(1)

    if not shutil.which("yosys"):
        log("ERROR: Yosys not found", RED)
        sys.exit(1)

    # Load config and build block queue
    config = load_config()
    block_queue = get_sorted_block_queue(config)

    if not block_queue:
        log("ERROR: No blocks found in config.yaml", RED)
        sys.exit(1)

    total_blocks = len(block_queue)
    log(f"Pipeline: {total_blocks} blocks\n")

    # Clear event log
    events_path = PROJECT_ROOT / ".socmate" / "pipeline_events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("")

    # Build the pipeline graph with SQLite checkpointing
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.types import Command
    from langgraph.errors import GraphInterrupt

    from orchestrator.langgraph.pipeline_graph import build_pipeline_graph

    checkpoint_db = str(PROJECT_ROOT / ".socmate" / "pipeline_checkpoint.db")

    async with AsyncSqliteSaver.from_conn_string(checkpoint_db) as checkpointer:
        graph = build_pipeline_graph(checkpointer=checkpointer)

        thread_id = f"cli-{int(time.time())}"
        graph_config = {"configurable": {"thread_id": thread_id}}

        initial_state = {
            "project_root": str(PROJECT_ROOT),
            "target_clock_mhz": TARGET_CLOCK_MHZ,
            "max_attempts": MAX_ATTEMPTS,
            "block_queue": block_queue,
            "tier_list": [],
            "current_tier_index": 0,
            "completed_blocks": [],
            "pipeline_done": False,
        }

        # Run graph, auto-resuming on interrupts (headless CI mode).
        #
        # LangGraph 1.x changed the dynamic-interrupt API: a node that
        # calls ``interrupt()`` no longer reliably raises
        # ``GraphInterrupt`` out of ``ainvoke()``. The new contract is
        # that ``ainvoke()`` returns normally and the caller inspects
        # ``aget_state()`` for pending interrupts. The previous loop
        # only handled the raised case, so any node that hit
        # ``interrupt()`` (e.g. ``integration_review`` for chip-level
        # spec approval after each tier) would silently park the graph
        # and ``ainvoke()`` would return without advancing. The result
        # was: tier-N completes, integration review fires, the pipeline
        # prints "PIPELINE COMPLETE" while tier-(N+1) blocks sit
        # waiting forever in the queue.
        #
        # Fix: after each ainvoke, check the state. If interrupts are
        # pending, resume; otherwise we're truly done. Preserves the
        # GraphInterrupt path for any LangGraph version that still
        # raises.
        current_input = initial_state
        while True:
            try:
                await graph.ainvoke(current_input, graph_config)
                raised_interrupt = False
            except GraphInterrupt:
                raised_interrupt = True

            state = await graph.aget_state(graph_config)

            # Collect all pending interrupts (parallel blocks may have multiple)
            interrupts = []
            if state and state.tasks:
                for task in state.tasks:
                    for intr in task.interrupts:
                        interrupts.append((intr.id, intr.value))

            if not interrupts:
                if raised_interrupt:
                    # Stale raise but no actual pending interrupt left;
                    # treat as a tick and re-enter with no input.
                    log("  [AUTO] No pending interrupts found, continuing", YELLOW)
                    current_input = None
                    continue
                break  # Graph genuinely completed

            # Check the first interrupt to determine action type
            first_payload = interrupts[0][1]

            if first_payload.get("type") == "uarch_spec_review":
                names = [p.get("block_name", "?") for _, p in interrupts]
                log(f"  [AUTO] Auto-approving uarch specs for {', '.join(names)}", YELLOW)
                resume_value = {"action": "approve"}
            elif first_payload.get("type") == "uarch_integration_review":
                tier = first_payload.get("tier", "?")
                issues = first_payload.get("issues_found", 0)
                names = first_payload.get("block_names", [])
                log(f"  [AUTO] Auto-approving integration review for tier {tier} "
                    f"({len(names)} blocks, {issues} issues)", YELLOW)
                resume_value = {"action": "approve"}
            elif first_payload.get("type") == "pipeline_incomplete":
                # Gate failure: pipeline_complete_node fired because one or more
                # blocks did not pass.  Earlier behavior was to fall through to
                # the catch-all block-retry handler, which sent ``{"action":
                # "retry"}`` with attempt=1 -- meaningless at this node, and the
                # graph then sailed through to integration_check with a
                # partial-datapath design.  Fail loudly and stop the run instead.
                passed = first_payload.get("passed", 0)
                expected = first_payload.get("expected", 0)
                failed_names = [b.get("name", "?") for b in
                                first_payload.get("failed_blocks", [])]
                missing = first_payload.get("missing_blocks", [])
                log(f"\n  [PIPELINE GATE FAILED] "
                    f"{passed}/{expected} blocks passed.", RED)
                if failed_names:
                    log(f"  [PIPELINE GATE FAILED] Failed: {failed_names}", RED)
                if missing:
                    log(f"  [PIPELINE GATE FAILED] Missing: {missing}", RED)
                log(f"  [PIPELINE GATE FAILED] NOT proceeding to integration. "
                    f"Diagnose the failing blocks and re-run.", RED)
                raise SystemExit(2)
            else:
                block_name = first_payload.get("block_name", "?")
                attempt = first_payload.get("attempt", 1)
                if attempt >= MAX_ATTEMPTS:
                    log(f"  [AUTO] Skipping {block_name} (exhausted attempts)", YELLOW)
                    resume_value = {"action": "skip"}
                else:
                    log(f"  [AUTO] Retrying {block_name} (attempt {attempt})", YELLOW)
                    resume_value = {"action": "retry"}

            # Build resume map for multiple interrupts, or plain Command for single
            if len(interrupts) > 1:
                current_input = Command(
                    resume={iid: resume_value for iid, _ in interrupts}
                )
            else:
                current_input = Command(resume=resume_value)

        # Extract results from final state
        final_state = await graph.aget_state(graph_config)
        values = final_state.values if final_state else {}
        completed = values.get("completed_blocks", [])

    # Final report
    elapsed = time.time() - start_time
    elapsed_min = elapsed / 60.0

    passed = [r for r in completed if r.get("success")]
    failed = [r for r in completed if not r.get("success")]

    # Only claim "COMPLETE" if every block actually passed.  Earlier code
    # printed PIPELINE COMPLETE unconditionally, which made partial-success
    # runs read as full successes in logs and dashboards.
    log(f"\n{'#'*60}", CYAN)
    if failed:
        log(f"  PIPELINE PARTIAL: {len(passed)}/{len(completed)} blocks passed", YELLOW)
    else:
        log("  PIPELINE COMPLETE", CYAN)
    log(f"{'#'*60}\n", CYAN)

    log(f"  Results: {len(passed)}/{len(completed)} blocks passed")
    log(f"  Elapsed: {elapsed_min:.1f} minutes\n")

    if passed:
        log("  PASSED:", GREEN)
        for r in passed:
            gc = r.get("gate_count", 0)
            att = r.get("attempts", 1)
            synth = "synthesized" if r.get("synth_success") else "synth failed"
            retry = f" (attempt {att})" if att > 1 else ""
            log(f"    {r['name']}: {gc:,} cells, {synth}{retry}", GREEN)

    if failed:
        log("\n  FAILED:", RED)
        for r in failed:
            reason = "skipped" if r.get("skipped") else "escalated" if r.get("escalated") else "failed"
            log(f"    {r['name']}: {reason} -- {r.get('error', 'unknown')[:100]}", RED)

    # Write results
    results_path = PROJECT_ROOT / ".socmate" / "pipeline_results.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(completed, indent=2, default=str))
    log(f"\n  Results written to {results_path}")


if __name__ == "__main__":
    asyncio.run(main())
