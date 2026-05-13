#!/usr/bin/env python3
"""Run SocMate from architecture through RTL pipeline in headless mode."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_requirements(path: str) -> str:
    return Path(path).read_text()


def _json_loads(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}
    return value if isinstance(value, dict) else {"value": value}


def _answer_prd_questions(state: dict) -> dict:
    answers: dict[str, str] = {}
    ask = state.get("ask_question") or {}

    for item in ask.get("auto_answerable", []):
        qid = item.get("id")
        if qid:
            answers[qid] = item.get("suggested_answer", "")

    for item in ask.get("remaining_choice_questions", []):
        qid = item.get("id")
        opts = item.get("options") or []
        if qid and opts:
            answers[qid] = opts[0]

    for item in ask.get("questions", []):
        qid = item.get("id")
        opts = item.get("options") or []
        if qid and opts:
            answers[qid] = opts[0].get("label", "") if isinstance(opts[0], dict) else str(opts[0])

    defaults = {
        "target_technology": "SkyWater Sky130, sky130_fd_sc_hd, Verilog-2005",
        "target_clock": "50 MHz",
        "bus_protocol": "AXI-Stream data interfaces with simple sideband mode pins",
        "data_width": "8-bit grayscale pixels, signed residuals, fp16 transform coefficients, int16 levels",
        "input_data_rate": "128x72 Mort GIF frames for evaluation; scalable streaming raster input",
        "latency_budget": "No hard latency budget; prioritize lint/sim clean RTL and tractable area",
        "area_budget": "Fit a small soft-IP codec in Sky130; avoid SRAM-heavy or CPU-style designs",
        "power_budget": "No explicit power budget; use synchronous single-clock RTL",
    }
    for key, value in defaults.items():
        answers.setdefault(key, value)

    return answers


async def _wait_task(task) -> None:
    if task is not None and not task.done():
        await asyncio.sleep(0)


async def run(args: argparse.Namespace) -> int:
    os.environ.setdefault("SOCMATE_PROJECT_ROOT", str(Path.cwd()))
    os.environ.setdefault("SOCMATE_LLM_PROVIDER", "codex")
    os.environ.setdefault("SOCMATE_CODEX_MODEL", "gpt-5.5")
    os.environ.setdefault("SOCMATE_MODEL", "gpt-5.5")
    os.environ.setdefault("SOCMATE_BLOCK_MODEL", "gpt-5.5")
    os.environ.setdefault("SOCMATE_CODEX_SANDBOX", "danger-full-access")
    os.environ.setdefault("SOCMATE_SKIP_SYNTH", "1")

    # These stages are bypassed by default in architecture_graph.py. Leave the
    # enable env vars unset unless the caller explicitly exports them.
    os.environ.setdefault("SOCMATE_ENABLE_MEMORY_MAP", "0")
    os.environ.setdefault("SOCMATE_ENABLE_CLOCK_TREE", "0")
    os.environ.setdefault("SOCMATE_ENABLE_REGISTER_SPEC", "0")

    from orchestrator import mcp_server as mcp

    requirements = _load_requirements(args.requirements)

    print("[top] starting architecture", flush=True)
    result = _json_loads(await mcp.start_architecture(
        requirements=requirements,
        target_clock_mhz=args.target_clock_mhz,
        pdk_config_path=args.pdk_config,
        max_rounds=args.max_rounds,
    ))
    print(json.dumps(result, indent=2), flush=True)
    if "error" in result:
        return 1

    last_arch_phase = ""
    while True:
        await asyncio.sleep(args.poll_s)
        await _wait_task(mcp._architecture.task)
        state = _json_loads(await mcp.get_architecture_state())
        phase = state.get("phase", "")
        if phase != last_arch_phase or state.get("human_input_needed"):
            print("[arch]", json.dumps({
                "status": state.get("status"),
                "phase": phase,
                "blocks": state.get("block_names"),
                "interrupt": state.get("interrupt_type"),
                "summary": state.get("interrupt_summary"),
            }, indent=2), flush=True)
            last_arch_phase = phase

        if state.get("status") == "error":
            print(json.dumps(state, indent=2), flush=True)
            return 1
        if state.get("success") and state.get("status") == "done":
            break
        if state.get("human_input_needed"):
            itype = state.get("interrupt_type", "")
            if itype in ("prd_questions", "ers_questions"):
                answers = _answer_prd_questions(state)
                print("[arch] auto-answering PRD questions", json.dumps(answers, indent=2), flush=True)
                print(await mcp.resume_architecture("continue", json.dumps(answers)), flush=True)
            elif itype == "final_review":
                print("[arch] auto-approving final review", flush=True)
                print(await mcp.resume_architecture("accept"), flush=True)
            elif itype in (
                "architecture_review_diagram",
                "architecture_review_constraints",
                "architecture_review_exhausted",
                "architecture_review_needed",
            ):
                print("[arch] accepting architecture interrupt", flush=True)
                print(await mcp.resume_architecture("accept"), flush=True)
            else:
                print("[arch] continuing unknown interrupt", itype, flush=True)
                print(await mcp.resume_architecture("continue"), flush=True)

    print("[top] architecture complete; starting frontend pipeline from block_specs.json", flush=True)
    result = _json_loads(await mcp.start_pipeline(
        max_attempts=args.max_attempts,
        target_clock_mhz=args.target_clock_mhz,
        blocks_file="",
    ))
    print(json.dumps(result, indent=2), flush=True)
    if "error" in result:
        return 1

    last_pipeline = None
    retry_counts: dict[str, int] = {}
    while True:
        await asyncio.sleep(args.poll_s)
        await _wait_task(mcp._pipeline.task)
        state = _json_loads(await mcp.get_pipeline_state())
        digest = (
            state.get("status"),
            state.get("completed_count"),
            state.get("total_blocks"),
            state.get("current_tier"),
            state.get("interrupt_type"),
            state.get("pending_interrupt_count"),
        )
        if digest != last_pipeline:
            print("[pipeline]", json.dumps({
                "status": state.get("status"),
                "completed": f"{state.get('completed_count')}/{state.get('total_blocks')}",
                "tier": state.get("current_tier"),
                "interrupt": state.get("interrupt_type"),
                "pending_interrupts": state.get("pending_interrupt_count"),
            }, indent=2), flush=True)
            last_pipeline = digest

        if state.get("status") == "error":
            print(json.dumps(state, indent=2), flush=True)
            return 1
        if state.get("pipeline_done") or state.get("status") == "done":
            print("[top] pipeline finished", flush=True)
            print(json.dumps(state, indent=2), flush=True)
            return 0
        if state.get("status") == "interrupted":
            actions = state.get("interrupt_actions") or []
            interrupted = state.get("interrupted_blocks") or []
            block_actions = {}
            for item in interrupted:
                block = item.get("block_name", "")
                retry_counts[block] = retry_counts.get(block, 0) + 1
                if "retry" in actions and retry_counts[block] <= 3:
                    block_actions[block] = "retry"
                elif "skip" in actions:
                    block_actions[block] = "skip"
            if "approve" in actions:
                action = "approve"
            elif "retry" in actions:
                action = "retry"
            elif "skip" in actions:
                action = "skip"
            else:
                action = actions[0] if actions else "retry"
            print("[pipeline] auto-resuming", action, block_actions, flush=True)
            print(await mcp.resume_pipeline(
                action=action,
                block_actions=json.dumps(block_actions) if block_actions else "",
            ), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", default="examples/multiframe_codec_v2/requirements.md")
    parser.add_argument("--target-clock-mhz", type=float, default=50.0)
    parser.add_argument("--pdk-config", default="pdk/configs/sky130.yaml")
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--poll-s", type=float, default=30.0)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
