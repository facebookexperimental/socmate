# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
Constraint Checker -- validates cross-cutting architectural constraints.

Combines deterministic pre-LLM checks (shuttle fit, GPIO pad budget) with
LLM-based holistic review.  The deterministic checks run first and are
always accurate; the LLM checks catch higher-level architectural issues
that rule-based checks would miss.

Each violation dict has:
  - violation: human-readable description string
  - category: "structural" (requires human input) or "auto_fixable" (LLM can iterate)
  - check: which constraint check flagged it (e.g. "peripheral_count")
  - severity: "error" or "warning"
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from pathlib import Path

_PROMPT_FILE = Path(__file__).resolve().parents[1] / "langchain" / "prompts" / "constraint_check.md"
SYSTEM_PROMPT = _PROMPT_FILE.read_text()


def _get_shuttle_limits() -> dict:
    """Load shuttle physical limits from config.yaml tapeout section."""
    from orchestrator.langgraph.pipeline_helpers import load_config

    cfg = load_config()
    tapeout = cfg.get("tapeout", {})
    die_w = tapeout.get("die_width_um", 3520.0)
    die_h = tapeout.get("die_height_um", 5188.0)
    margin = tapeout.get("core_margin_um", 100.0)
    io_pads = tapeout.get("io_pads", 44)
    reserved_pads = 2  # GPIO[0]=clk, GPIO[1]=rst

    user_w = die_w - 2 * margin
    user_h = die_h - 2 * margin
    return {
        "target": tapeout.get("target", "openframe"),
        "die_width_um": die_w,
        "die_height_um": die_h,
        "die_area_mm2": (die_w * die_h) / 1e6,
        "user_width_um": user_w,
        "user_height_um": user_h,
        "user_area_mm2": (user_w * user_h) / 1e6,
        "core_margin_um": margin,
        "total_io_pads": io_pads,
        "reserved_pads": reserved_pads,
        "usable_io_pads": io_pads - reserved_pads,
    }


def _count_block_io_pads(block_diagram: dict) -> tuple[int, list[dict]]:
    """Count total GPIO pads needed by all blocks in the block diagram.

    Cross-references the ``connections`` array so that inter-block ports
    (which become internal wires, not chip-boundary pads) are excluded.

    Returns (total_pads_needed, per_block_details).
    """
    blocks = block_diagram.get("blocks", [])
    connections = block_diagram.get("connections", [])

    # Build set of (block_name, port_name) pairs that are internal wires
    connected_ports: set[tuple[str, str]] = set()
    for conn in connections:
        from_block = conn.get("from", conn.get("from_block", ""))
        to_block = conn.get("to", conn.get("to_block", ""))
        from_port = conn.get("from_port", conn.get("interface", ""))
        to_port = conn.get("to_port", conn.get("interface", ""))
        if from_block and from_port:
            connected_ports.add((from_block, from_port))
        if to_block and to_port:
            connected_ports.add((to_block, to_port))

    total = 0
    details: list[dict] = []

    for block in blocks:
        name = block.get("name", "unknown")
        interfaces = block.get("interfaces", {})
        block_pads = 0

        for port_name, port_info in interfaces.items():
            if port_name in ("clk", "rst", "rst_n"):
                continue
            if (name, port_name) in connected_ports:
                continue
            if isinstance(port_info, dict):
                width = port_info.get("width", 1)
            elif isinstance(port_info, int):
                width = port_info
            else:
                width = 1
            block_pads += width

        total += block_pads
        details.append({"block": name, "pads_needed": block_pads})

    return total, details


def _check_shuttle_constraints(
    block_diagram: dict,
    ers_spec: dict | None,
) -> list[dict]:
    """Deterministic shuttle constraint checks (GPIO pad budget + area fit).

    These run before the LLM and produce reliable, precise violations.
    """
    violations: list[dict] = []
    shuttle = _get_shuttle_limits()

    # --- Check #11: GPIO pad budget ---
    total_pads, pad_details = _count_block_io_pads(block_diagram)
    usable = shuttle["usable_io_pads"]

    if total_pads > usable:
        overflow = total_pads - usable
        detail_str = ", ".join(
            f"{d['block']}={d['pads_needed']}" for d in pad_details if d["pads_needed"] > 0
        )
        violations.append({
            "violation": (
                f"GPIO pad budget exceeded: design needs {total_pads} I/O pads "
                f"but {shuttle['target'].upper()} shuttle provides only {usable} "
                f"usable pads ({shuttle['total_io_pads']} total minus "
                f"{shuttle['reserved_pads']} reserved for clk/rst). "
                f"Overflow: {overflow} pads. "
                f"Per-block: {detail_str}. "
                f"Consider pin-muxing, serialization, or reducing interface widths."
            ),
            "category": "structural",
            "check": "gpio_pad_budget",
            "severity": "error",
        })
    elif total_pads > usable * 0.9:
        violations.append({
            "violation": (
                f"GPIO pad budget tight: design uses {total_pads}/{usable} "
                f"available pads ({total_pads * 100 / usable:.0f}% utilization). "
                f"Less than 10% headroom for debug/test pins."
            ),
            "category": "auto_fixable",
            "check": "gpio_pad_budget",
            "severity": "warning",
        })

    # --- Check #12: Shuttle area fit ---
    blocks = block_diagram.get("blocks", [])
    total_gates = sum(b.get("estimated_gates", 0) for b in blocks)

    ers = (ers_spec.get("prd", ers_spec.get("ers", {})) if isinstance(ers_spec, dict) else {}) or {}
    area_budget = ers.get("area_budget", {}) or {}
    prd_die_area = area_budget.get("max_die_area_mm2")

    if prd_die_area and prd_die_area > shuttle["user_area_mm2"]:
        violations.append({
            "violation": (
                f"PRD die area budget ({prd_die_area:.3f} mm²) exceeds "
                f"{shuttle['target'].upper()} shuttle user area "
                f"({shuttle['user_area_mm2']:.3f} mm²). "
                f"Die dimensions: {shuttle['user_width_um']:.0f} x "
                f"{shuttle['user_height_um']:.0f} um. "
                f"The design will not fit in the shuttle frame. "
                f"Reduce area budget or choose a larger shuttle."
            ),
            "category": "structural",
            "check": "shuttle_area_fit",
            "severity": "error",
        })

    if total_gates > 0:
        gate_density_per_mm2 = 200_000  # ~200K gates/mm² for Sky130 HD at 60% util
        estimated_area_mm2 = total_gates / gate_density_per_mm2
        if estimated_area_mm2 > shuttle["user_area_mm2"]:
            violations.append({
                "violation": (
                    f"Estimated design area ({estimated_area_mm2:.3f} mm² from "
                    f"{total_gates:,} gates at ~200K gates/mm²) exceeds "
                    f"{shuttle['target'].upper()} shuttle user area "
                    f"({shuttle['user_area_mm2']:.3f} mm²). "
                    f"Reduce block count, gate estimates, or functionality."
                ),
                "category": "structural",
                "check": "shuttle_area_fit",
                "severity": "error",
            })
        elif estimated_area_mm2 > shuttle["user_area_mm2"] * 0.8:
            violations.append({
                "violation": (
                    f"Estimated design area ({estimated_area_mm2:.3f} mm²) uses "
                    f"{estimated_area_mm2 * 100 / shuttle['user_area_mm2']:.0f}% of "
                    f"shuttle user area ({shuttle['user_area_mm2']:.3f} mm²). "
                    f"Limited room for routing, power grid, and fill. "
                    f"Consider reducing gate count."
                ),
                "category": "auto_fixable",
                "check": "shuttle_area_fit",
                "severity": "warning",
            })

    return violations


def _shuttle_constraints_enabled(requirements: str = "", ers_spec: dict | None = None) -> bool:
    """Return whether package/shuttle constraints should be enforced.

    Most SocMate frontend runs generate reusable soft IP, where block ports are
    internal module interfaces rather than package GPIO. Shuttle pad/area checks
    are useful for MPW benchmark wrappers, but they must be opt-in to avoid
    forcing every soft IP through an OpenFrame pin budget.
    """
    value = os.environ.get("SOCMATE_ENABLE_SHUTTLE_CONSTRAINTS", "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False

    text = requirements.lower()
    if any(token in text for token in ("openframe wrapper", "mpw wrapper", "shuttle gpio")):
        return True

    ers = (ers_spec.get("prd", ers_spec.get("ers", {})) if isinstance(ers_spec, dict) else {}) or {}
    target = json.dumps(ers.get("technology", {})).lower()
    return any(token in target for token in ("openframe", "caravel", "mpw"))


async def check_constraints(
    block_diagram: dict,
    memory_map: dict,
    clock_tree: dict,
    register_spec: dict,
    benchmark_results: dict | None = None,
    pdk_config: dict | None = None,
    requirements: str = "",
    ers_spec: dict | None = None,
    project_root: str = ".",
) -> list[dict]:
    """Validate cross-cutting architectural constraints.

    Runs deterministic shuttle checks first (GPIO pad budget, area fit),
    then delegates holistic review to the LLM for rules 1-10.

    Args:
        block_diagram: Block diagram with blocks and connections.
        memory_map: Memory map with peripherals and address allocations.
        clock_tree: Clock tree with domains and crossings.
        register_spec: Register specifications per block.
        benchmark_results: Benchmark synthesis results for gate estimates.
        pdk_config: PDK configuration dict.
        requirements: High-level system requirements for context.
        ers_spec: Product Requirements Document (structured dict).

    Returns:
        List of violation dicts. Each dict has keys: violation, category,
        check, severity. Empty list = all constraints pass.
    """
    from opentelemetry import trace as _trace

    tracer = _trace.get_tracer("socmate.architecture.constraints")

    with tracer.start_as_current_span("check_constraints") as span:
        # --- Deterministic shuttle checks (opt-in for package/top-level runs) ---
        shuttle_enabled = _shuttle_constraints_enabled(requirements, ers_spec)
        shuttle_violations = (
            _check_shuttle_constraints(block_diagram, ers_spec)
            if shuttle_enabled
            else []
        )

        # --- Extract PRD-driven limits ---
        ers = (ers_spec.get("prd", ers_spec.get("ers", {})) if isinstance(ers_spec, dict) else {}) or {}
        area = ers.get("area_budget", {}) or {}
        dataflow = ers.get("dataflow", {}) or {}

        max_gate_count = _safe_int(area.get("max_gate_count"), 2_000_000)
        sram_size_kb = _extract_sram_budget_kb(ers, default=32)
        bus_protocol = dataflow.get("bus_protocol", "")
        block_count = len(block_diagram.get("blocks", []))
        max_peripheral_count = min(block_count + 2, 15) if block_count > 8 else 8
        memory_map_enabled = bool((memory_map.get("result", memory_map) or {}).get("peripherals"))
        clock_tree_enabled = bool((clock_tree.get("result", clock_tree) or {}).get("domains"))
        register_spec_enabled = bool(
            (register_spec.get("result", register_spec) or {}).get("register_blocks")
        )

        # Build gate budget rationale
        die_area = area.get("max_die_area_mm2")
        if die_area:
            gate_budget_rationale = (
                f"{die_area} mm² die at 60% utilization"
            )
        else:
            gate_budget_rationale = "default budget (no ERS die area specified)"

        shuttle = _get_shuttle_limits()
        total_pads, _ = _count_block_io_pads(block_diagram)
        if shuttle_enabled:
            shuttle_rules = (
                "SHUTTLE CONSTRAINT RULES:\n\n"
                f"11. GPIO PAD BUDGET: This design targets the {shuttle['target'].upper()} "
                f"shuttle with {shuttle['usable_io_pads']} usable I/O pads "
                f"(GPIO[0]=clk, GPIO[1]=rst reserved). Currently {total_pads} pads needed. "
                f"If any block's interface widths would cause GPIO overflow, flag as "
                f"\"structural\" severity \"error\".\n\n"
                f"12. SHUTTLE AREA FIT: The {shuttle['target'].upper()} shuttle user area is "
                f"{shuttle['user_area_mm2']:.3f} mm² ({shuttle['user_width_um']:.0f} x "
                f"{shuttle['user_height_um']:.0f} um). Sum of block estimated_gates / 200000 "
                f"must be < user_area * 0.8 for routing margin. Flag as \"structural\" "
                f"severity \"error\" if exceeded.\n"
            )
        else:
            shuttle_rules = (
                "SHUTTLE CONSTRAINT RULES:\n"
                "11-12. Skipped for this run. The architecture target is reusable "
                "soft IP, so block interfaces are internal RTL ports rather than "
                "package GPIO. Enforce shuttle GPIO/area only when "
                "SOCMATE_ENABLE_SHUTTLE_CONSTRAINTS=1 or the requirements "
                "explicitly target an MPW/OpenFrame/Caravel wrapper.\n"
            )

        # --- Build bus rules (skip for simple designs) ---
        needs_bus = (
            block_count >= 3
            and bus_protocol not in ("none", "None", "N/A", "")
        )
        if needs_bus:
            bus_rules = (
                "BUS AND MEMORY CONSTRAINT RULES:\n\n"
                f"1. PERIPHERAL COUNT: The peripheral bus uses nibble decode.\n"
                f"   Maximum {max_peripheral_count} peripherals. If exceeded, flag as\n"
                f"   \"structural\" severity \"error\".\n\n"
                "2. MEMORY OVERLAP: No two address regions may overlap.\n"
                "   Check every pair. If overlap found, flag as \"structural\" severity \"error\".\n\n"
                f"3. SRAM BUDGET: SRAM is {sram_size_kb}KB ({sram_size_kb * 1024} bytes).\n"
                f"   If total estimated usage exceeds {sram_size_kb}KB, flag as \"structural\" severity \"error\".\n\n"
                "6. BLOCK CONNECTIVITY: Every non-infrastructure block must appear in at least\n"
                "   one connection. Orphaned blocks are \"structural\" severity \"error\"."
            )
        else:
            bus_rules = (
                "BUS AND MEMORY CONSTRAINT RULES:\n"
                "(Skipped -- this design has fewer than 3 blocks or no bus protocol.)"
            )

        optional_stage_note = (
            "OPTIONAL STAGE NOTE:\n"
            "- Memory-map checks are disabled for this run unless a non-empty "
            "memory map is present.\n"
            "- Clock-domain and CDC checks are disabled for this run unless a "
            "non-empty clock tree is present.\n"
            "- Register-spec consistency checks are disabled for this run unless "
            "a non-empty register spec is present.\n\n"
        )

        additional_rules = (
            optional_stage_note +
            "ADDITIONAL CONSTRAINT RULES:\n\n"
            + (
                "4. CLOCK DOMAIN CROSSINGS: If the clock tree has multiple domains, every\n"
                "   connection between blocks in different domains MUST have a CDC module.\n"
                "   Missing CDC is \"auto_fixable\" severity \"error\".\n\n"
                if clock_tree_enabled
                else "4. CLOCK DOMAIN CROSSINGS: skipped because clock-tree generation is disabled.\n\n"
            ) +
            f"5. GATE BUDGET: Total estimated gates must not exceed {max_gate_count:,}\n"
            f"   ({gate_budget_rationale}). If exceeded, flag as \"auto_fixable\" severity \"error\".\n\n"
            "ADDITIONAL REVIEW (go beyond the rules above):\n"
            + (
                "7. Check that register spec blocks match memory map peripherals.\n"
                if register_spec_enabled and memory_map_enabled
                else "7. Register/memory-map consistency skipped because one or both optional artifacts are disabled.\n"
            ) +
            (
                "8. Check that all blocks in the block diagram appear in exactly one clock domain.\n"
                if clock_tree_enabled
                else "8. Per-block clock-domain membership skipped because clock-tree generation is disabled.\n"
            ) +
            "9. Check for data width mismatches in connections (source width != dest width\n"
            "   without an adapter block).\n"
            "10. Check that tier assignments are reasonable (e.g., an FFT should not be tier 1).\n\n"
            + shuttle_rules
        )

        parts = [
            "Review the following ASIC architecture for constraint violations.",
            "Check ALL rules listed in your instructions (1-12).",
        ]
        if requirements:
            parts.append(f"\nSystem requirements: {requirements}")

        parts.append(
            f"\n--- BLOCK DIAGRAM ---\n{json.dumps(block_diagram, indent=2)}"
        )
        parts.append(
            f"\n--- MEMORY MAP ---\n{json.dumps(memory_map, indent=2)}"
        )
        parts.append(
            f"\n--- CLOCK TREE ---\n{json.dumps(clock_tree, indent=2)}"
        )
        parts.append(
            f"\n--- REGISTER SPEC ---\n{json.dumps(register_spec, indent=2)}"
        )

        if benchmark_results:
            parts.append(
                f"\n--- BENCHMARK DATA ---\n{json.dumps(benchmark_results, indent=2)}"
            )
        if pdk_config:
            parts.append(
                f"\n--- PDK CONFIG ---\n{json.dumps(pdk_config, indent=2)}"
            )

        from pathlib import Path as _P
        _root = _P(project_root)
        for doc_name, doc_label in [
            ("sad_spec.md", "SYSTEM ARCHITECTURE DOCUMENT (SAD)"),
            ("frd_spec.md", "FUNCTIONAL REQUIREMENTS DOCUMENT (FRD)"),
        ]:
            doc_path = _root / "arch" / doc_name
            if doc_path.exists():
                try:
                    parts.append(f"\n--- {doc_label} ---\n{doc_path.read_text()}")
                except OSError:
                    pass

        user_message = "\n".join(parts)

        from orchestrator.langchain.agents.socmate_llm import DEFAULT_MODEL, ClaudeLLM

        llm = ClaudeLLM(model=DEFAULT_MODEL, timeout=600)

        # Fill template variables in system prompt
        system_prompt = SYSTEM_PROMPT.format(
            bus_rules=bus_rules,
            additional_rules=additional_rules,
        )

        try:
            content = await llm.call(
                system=system_prompt,
                prompt=user_message,
                run_name="constraint_check",
            )
            result = _parse_response(content)
            llm_violations = result.get("violations", [])

            # Merge: deterministic shuttle violations first, then LLM violations
            # Deduplicate: skip LLM violations for checks already covered
            covered_checks = {v["check"] for v in shuttle_violations}
            deduped_llm = [
                v for v in llm_violations
                if v.get("check") not in covered_checks
            ]
            violations = shuttle_violations + deduped_llm

            span.set_attribute("violation_count", len(violations))
            span.set_attribute("shuttle_violation_count", len(shuttle_violations))
            span.set_attribute("llm_violation_count", len(deduped_llm))
            span.set_attribute("all_pass", len(violations) == 0)
            span.set_attribute("max_gate_count", max_gate_count)
            span.set_attribute("sram_size_kb", sram_size_kb)
            span.set_attribute("needs_bus", needs_bus)
            span.set_attribute("shuttle_constraints_enabled", shuttle_enabled)
            if violations:
                span.set_attribute(
                    "violations",
                    "; ".join(v.get("violation", "") for v in violations[:5]),
                )

            return violations

        except Exception as e:
            span.set_attribute("error", str(e))
            span.set_status(_trace.StatusCode.ERROR, str(e))
            # Still return deterministic violations even if LLM fails
            shuttle_violations.append({
                "violation": f"Constraint check LLM failed: {e}. "
                             f"Architecture may have unchecked violations.",
                "category": "structural",
                "check": "llm_error",
                "severity": "warning",
            })
            return shuttle_violations


def _safe_int(value: Any, default: int) -> int:
    """Extract an integer from a value that may be None, str, or numeric."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _walk_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_walk_text(item))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_walk_text(item))
        return out
    return []


def _extract_sram_budget_kb(ers: dict[str, Any], default: int = 32) -> int:
    """Extract the intended on-chip SRAM budget from PRD/ERS content.

    Older PRD schemas only placed the memory KPI in area/dataflow notes, so
    falling back to a hard 32 KB budget can create false structural failures
    for designs whose stated combined activation+KV budget is 64 KB.
    """
    dataflow = ers.get("dataflow", {}) or {}
    area = ers.get("area_budget", {}) or {}

    for key in (
        "sram_size_kb",
        "sram_budget_kb",
        "onchip_sram_budget_kb",
        "on_chip_sram_budget_kb",
        "combined_sram_budget_kb",
    ):
        if key in dataflow:
            return _safe_int(dataflow.get(key), default)
        if key in area:
            return _safe_int(area.get(key), default)

    text = "\n".join(_walk_text({
        "dataflow": dataflow,
        "area_budget": area,
        "kpis": ers.get("kpis", ers.get("validation_kpis", [])),
        "requirements": ers.get("requirements", []),
    }))
    matches = []
    for match in re.finditer(
        r"(?:<=|less than|no greater than|max(?:imum)?|budget|limit|not exceed)"
        r"[^.\n]{0,80}?(\d+)\s*KB",
        text,
        flags=re.IGNORECASE,
    ):
        matches.append(int(match.group(1)))
    if matches:
        return max(matches)
    return default


def _parse_response(content: str) -> dict[str, Any]:
    """Extract structured JSON from LLM response."""
    from orchestrator.utils import parse_llm_json

    default: dict[str, Any] = {
        "violations": [],
        "reasoning": "",
    }
    result, _ok = parse_llm_json(content, default, context="constraints")
    return result
