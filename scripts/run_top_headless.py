#!/usr/bin/env python3
"""Run SocMate from architecture through RTL pipeline in headless mode."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
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


def _write_question_escalation(kind: str, state: dict) -> Path:
    esc_dir = PROJECT_ROOT / ".socmate" / "escalations"
    esc_dir.mkdir(parents=True, exist_ok=True)
    path = esc_dir / f"{kind}.json"
    answers_path = esc_dir / f"{kind}.answers.json"
    payload = {
        "type": kind,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": state.get("phase"),
        "interrupt_type": state.get("interrupt_type"),
        "summary": state.get("interrupt_summary"),
        "ask_question": state.get("ask_question") or {},
        "answer_file": str(answers_path),
        "resume_action": "continue",
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


async def _wait_for_question_answers(kind: str, poll_s: float) -> dict:
    esc_dir = PROJECT_ROOT / ".socmate" / "escalations"
    answers_path = esc_dir / f"{kind}.answers.json"
    print(f"[arch] escalation pending: write answers JSON to {answers_path}", flush=True)
    while True:
        if answers_path.exists():
            answers = json.loads(answers_path.read_text())
            if isinstance(answers, dict):
                print(f"[arch] loaded escalation answers from {answers_path}", flush=True)
                return answers
            raise RuntimeError(f"Escalation answers at {answers_path} must be a JSON object")
        await asyncio.sleep(max(5.0, poll_s))


def _answer_prd_questions(state: dict, requirements: str = "") -> dict:
    answers: dict[str, str] = {}
    ask = state.get("ask_question") or {}

    requirements_text = str(requirements or state.get("requirements", "") or "").lower()
    is_codec = any(token in requirements_text for token in ("h.264", "codec", "mort gif", "psnr", "bpp"))
    is_transformer = any(token in requirements_text for token in ("transformer", "llama2", "tinystories", "int4", "qspi"))

    rd_kpi_answer = (
        "Validation DV must preserve the Mort GIF RD targets from the prompt: "
        "software golden QP24 about 2.8674 bpp at PSNR >= 49.0 dB, QP36 "
        "about 1.0233 bpp at PSNR >= 38.0 dB, and QP48 about 0.2161 bpp at "
        "PSNR >= 34.0 dB. The hardware validation run should measure emitted "
        "bitstream size and reconstructed-frame PSNR against the golden model "
        "and fail if PSNR/bpp materially misses these targets."
    )
    transformer_kpi_answer = (
        "Validation DV must preserve the transformer accelerator KPIs from "
        "the prompt: bit-exact INT4/INT8 matmul over randomized tiles, "
        "bit-exact single-block checkpoint tensors within documented "
        "fixed-point tolerance, QSPI bandwidth/token-latency accounting, "
        "default on-chip SRAM budget no greater than 64 KB, and no on-chip "
        "weight storage beyond a prefetched tile plus metadata."
    )

    def _maybe_answer_freeform_kpi(item: dict) -> None:
        qid = item.get("id")
        if not qid or qid in answers:
            return
        category = str(item.get("category", "")).lower()
        text = f"{item.get('question', '')} {item.get('context', '')}".lower()
        if is_codec and (
            "validation_kpi" in category
            or "kpi" in qid
            or "psnr" in text
            or "bpp" in text
            or "rate" in text and "distortion" in text
        ):
            answers[qid] = rd_kpi_answer
        elif is_transformer and (
            "validation_kpi" in category
            or "kpi" in qid
            or "matmul" in text
            or "checkpoint" in text
            or "flash" in text
            or "memory" in text
        ):
            answers[qid] = transformer_kpi_answer

    for item in ask.get("auto_answerable", []):
        qid = item.get("id")
        if qid:
            answers[qid] = item.get("suggested_answer", "")
        _maybe_answer_freeform_kpi(item)

    for item in ask.get("remaining_choice_questions", []):
        qid = item.get("id")
        opts = item.get("options") or []
        if qid and opts:
            answers[qid] = opts[0]
        _maybe_answer_freeform_kpi(item)

    for item in ask.get("questions", []):
        qid = item.get("id")
        opts = item.get("options") or []
        if qid and opts:
            answers[qid] = opts[0].get("label", "") if isinstance(opts[0], dict) else str(opts[0])
        _maybe_answer_freeform_kpi(item)

    common_defaults = {
        "target_technology": "SkyWater Sky130, sky130_fd_sc_hd, Verilog-2005",
        "target_clock": "50 MHz",
        "latency_budget": "No hard latency budget; prioritize lint/sim clean RTL and tractable area",
        "power_budget": "No explicit power budget; use synchronous single-clock RTL",
    }
    codec_defaults = {
        "bus_protocol": "AXI-Stream data interfaces with simple sideband mode pins",
        "data_width": "8-bit grayscale pixels, signed residuals, fp16 transform coefficients, int16 levels",
        "input_data_rate": "Mort GIF frames at the requirements resolution; scalable streaming raster input",
        "area_budget": "Fit a small soft-IP codec in Sky130; avoid SRAM-heavy or CPU-style designs",
        "validation_kpi": rd_kpi_answer,
        "rd_validation_kpi": rd_kpi_answer,
        "quality_kpi": rd_kpi_answer,
        "psnr_bpp_kpi": rd_kpi_answer,
    }
    transformer_defaults = {
        "bus_protocol": "Wishbone-compatible host control plus valid/ready tensor streams",
        "data_width": "INT4 weights, INT8 activations, INT32 accumulators, documented fixed-point tensor formats",
        "input_data_rate": "Host-sequenced token decode with external flash weight streaming",
        "area_budget": "Fit a Caravel-class Sky130 soft IP with <=64 KB default activation/KV SRAM and no bulk on-chip weights",
        "validation_kpi": transformer_kpi_answer,
        "matmul_bit_exact_kpi": "At least 64 randomized matrix tiles must match the fixed-point golden model exactly.",
        "single_block_bit_exact_kpi": "Golden checkpoint tensors for one transformer block must match within documented fixed-point tolerance.",
        "flash_bandwidth_model_kpi": "Bandwidth accounting must prove token-latency lower bounds from model size and flash throughput.",
        "onchip_memory_limit_kpi": "Default generated architecture must budget no more than 64 KB activation plus KV SRAM.",
        "no_onchip_weight_storage_kpi": "RTL must not allocate storage for more than one prefetched weight tile plus metadata.",
    }
    defaults = dict(common_defaults)
    if is_transformer:
        defaults.update(transformer_defaults)
    elif is_codec:
        defaults.update(codec_defaults)
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
                if args.auto_answer_questions:
                    answers = _answer_prd_questions(state, requirements)
                    print("[arch] auto-answering PRD questions", json.dumps(answers, indent=2), flush=True)
                else:
                    path = _write_question_escalation(itype, state)
                    print(f"[arch] wrote question escalation to {path}", flush=True)
                    answers = await _wait_for_question_answers(itype, args.poll_s)
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
            tuple(state.get("next_nodes") or []),
        )
        if digest != last_pipeline:
            print("[pipeline]", json.dumps({
                "status": state.get("status"),
                "completed": f"{state.get('completed_count')}/{state.get('total_blocks')}",
                "tier": state.get("current_tier"),
                "interrupt": state.get("interrupt_type"),
                "pending_interrupts": state.get("pending_interrupt_count"),
                "next_nodes": state.get("next_nodes") or [],
            }, indent=2), flush=True)
            last_pipeline = digest

        if state.get("status") == "error":
            print(json.dumps(state, indent=2), flush=True)
            return 1
        next_nodes = state.get("next_nodes") or []
        if state.get("status") == "done" and next_nodes:
            next_node = next_nodes[0]
            print(f"[pipeline] continuing pending graph node: {next_node}", flush=True)
            result = _json_loads(await mcp.restart_node(next_node))
            print(json.dumps(result, indent=2), flush=True)
            if "error" in result:
                return 1
            continue
        if (state.get("pipeline_done") or state.get("status") == "done") and not next_nodes:
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
    parser.add_argument(
        "--auto-answer-questions",
        action="store_true",
        default=os.environ.get("SOCMATE_HEADLESS_AUTO_ANSWER_QUESTIONS", "").strip().lower()
        in {"1", "true", "yes", "on"},
        help="Use canned PRD/ERS answers. Default is to escalate questions and wait for an answers file.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
