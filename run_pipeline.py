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
    BOLD,
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
    log(f"  socmate -- AI-Orchestrated ASIC Pipeline", CYAN)
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

        # Run graph, auto-resuming on interrupts (headless CI mode)
        current_input = initial_state
        while True:
            try:
                result = await graph.ainvoke(current_input, graph_config)
                break  # Graph completed normally
            except GraphInterrupt:
                state = await graph.aget_state(graph_config)

                # Collect all pending interrupts (parallel blocks may have multiple)
                interrupts = []
                if state and state.tasks:
                    for task in state.tasks:
                        for intr in task.interrupts:
                            interrupts.append((intr.id, intr.value))

                if not interrupts:
                    log("  [AUTO] No pending interrupts found, continuing", YELLOW)
                    current_input = None
                    continue

                # Check the first interrupt to determine action type
                first_payload = interrupts[0][1]

                if first_payload.get("type") == "uarch_spec_review":
                    names = [p.get("block_name", "?") for _, p in interrupts]
                    log(f"  [AUTO] Auto-approving uarch specs for {', '.join(names)}", YELLOW)
                    resume_value = {"action": "approve"}
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

    log(f"\n{'#'*60}", CYAN)
    log(f"  PIPELINE COMPLETE", CYAN)
    log(f"{'#'*60}\n", CYAN)

    passed = [r for r in completed if r.get("success")]
    failed = [r for r in completed if not r.get("success")]

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
