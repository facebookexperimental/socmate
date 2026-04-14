# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
IntegrationLeadAgent -- LLM-driven integration of RTL blocks into a
chip-level top module.

Replaces the previous deterministic compatibility check and top-level
Verilog generation with an agent that can reason about port semantics,
naming conventions, and cross-block wiring.

The agent:
1. Analyzes all block RTL ports against the architecture connection graph
2. Identifies compatibility issues (width mismatches, missing ports, etc.)
3. Generates a synthesizable top-level Verilog module wiring all blocks
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from opentelemetry import trace

from .socmate_llm import DEFAULT_MODEL, ClaudeLLM

_tracer = trace.get_tracer(__name__)

_PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "integration_lead.md"
if _PROMPT_FILE.exists():
    SYSTEM_PROMPT = _PROMPT_FILE.read_text()
else:
    SYSTEM_PROMPT = (
        "You are an Integration Lead engineer. Analyze block RTL ports for "
        "compatibility and generate a top-level Verilog module wiring all "
        "blocks together. Respond with JSON only."
    )


class IntegrationLeadAgent:
    """Agent for chip-level integration: compatibility check + top-level RTL."""

    def __init__(self, model: str = DEFAULT_MODEL, temperature: float = 0.1):
        self.llm = ClaudeLLM(
            model=model,
            timeout=int(os.environ.get("SOCMATE_INTEGRATION_LEAD_TIMEOUT", "2700")),
        )

    async def integrate(
        self,
        design_name: str,
        block_rtl_sources: dict[str, str],
        block_port_summaries: list[dict],
        connections: list[dict],
        prd_summary: str = "",
        output_path: str = "",
    ) -> dict[str, Any]:
        """Analyze compatibility and generate top-level integration module.

        Args:
            design_name: Name for the top-level module.
            block_rtl_sources: Map of block_name -> full Verilog source text.
            block_port_summaries: List of dicts with block name, ports, etc.
            connections: Architecture connection list from block diagram.
            prd_summary: PRD summary for requirements context.
            output_path: File path to write the generated Verilog to.

        Returns:
            Dict with keys: rtl_path, mismatches, module_name, wire_count,
            skipped_connections, notes.
        """
        with _tracer.start_as_current_span(
            f"Integration Lead [{design_name}]"
        ) as span:
            span.set_attribute("design_name", design_name)
            span.set_attribute("block_count", len(block_rtl_sources))

            user_message = self._build_prompt(
                design_name, block_rtl_sources, block_port_summaries,
                connections, prd_summary, output_path,
            )

            content = await self.llm.call(
                system=SYSTEM_PROMPT,
                prompt=user_message,
                run_name=f"Integration Lead [{design_name}]",
            )

            result = self._parse_response(content, design_name)

            verilog = result.pop("verilog", "")
            if verilog and output_path:
                out = Path(output_path)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(verilog, encoding="utf-8")
                result["rtl_path"] = output_path
            else:
                result["rtl_path"] = ""

            span.set_attribute("mismatch_count", len(result.get("mismatches", [])))
            span.set_attribute("wire_count", result.get("wire_count", 0))
            span.set_attribute("module_name", result.get("module_name", ""))

            return result

    def _build_prompt(
        self,
        design_name: str,
        block_rtl_sources: dict[str, str],
        block_port_summaries: list[dict],
        connections: list[dict],
        prd_summary: str,
        output_path: str = "",
    ) -> str:
        parts = [
            f"Design name: {design_name}",
            f"Total blocks: {len(block_rtl_sources)}",
        ]

        parts.append("\n--- BLOCK RTL SOURCES ---")
        for name, source in sorted(block_rtl_sources.items()):
            truncated = source[:8000]
            parts.append(f"\n### {name}\n```verilog\n{truncated}\n```")

        parts.append("\n--- PARSED PORT SUMMARIES ---")
        for bs in block_port_summaries:
            name = bs.get("name", "unknown")
            ports = bs.get("ports", [])
            port_lines = []
            for p in ports[:30]:
                w = p.get("width", 1)
                width_str = f"[{w}-bit]" if w > 1 else "[1-bit]"
                port_lines.append(
                    f"    {p['direction']} {width_str} {p['name']}"
                )
            parts.append(f"  {name}:")
            parts.extend(port_lines)

        if connections:
            parts.append("\n--- ARCHITECTURE CONNECTIONS ---")
            for c in connections[:50]:
                fb = c.get("from_block", c.get("from", "?"))
                fp = c.get("from_port", "")
                tb = c.get("to_block", c.get("to", "?"))
                tp = c.get("to_port", "")
                iface = c.get("interface", c.get("name", ""))
                dw = c.get("data_width", "?")
                parts.append(
                    f"  {fb}.{fp} -> {tb}.{tp} "
                    f"(interface: {iface}, width: {dw})"
                )

        if prd_summary:
            parts.append(f"\n--- PRD SUMMARY ---\n{prd_summary}")

        out_instr = ""
        if output_path:
            out_instr = (
                f" Write the complete top-level Verilog module to: {output_path}."
            )

        parts.append(
            f"\nGenerate the integration analysis and top-level Verilog "
            f"for module '{design_name}'.{out_instr} Respond with JSON only."
        )

        return "\n".join(parts)

    def _parse_response(self, content: str, design_name: str) -> dict[str, Any]:
        """Parse the LLM JSON response, with fallback extraction."""
        content = content.strip()

        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return self._validate_result(data, design_name)
            except json.JSONDecodeError:
                pass

        return {
            "verilog": "",
            "mismatches": [],
            "module_name": design_name,
            "wire_count": 0,
            "skipped_connections": [],
            "notes": f"Failed to parse LLM response. Raw: {content[:500]}",
            "parse_error": True,
        }

    def _validate_result(self, data: dict, design_name: str) -> dict[str, Any]:
        """Validate and normalize the parsed result."""
        result = {
            "verilog": data.get("verilog", ""),
            "mismatches": data.get("mismatches", []),
            "module_name": data.get("module_name", design_name),
            "wire_count": data.get("wire_count", 0),
            "skipped_connections": data.get("skipped_connections", []),
            "notes": data.get("notes", ""),
        }

        for m in result["mismatches"]:
            if "severity" not in m:
                m["severity"] = "warning"
            if "issue_type" not in m:
                m["issue_type"] = "unknown"

        if result["verilog"] and "module" not in result["verilog"]:
            result["notes"] += " WARNING: verilog field does not contain a module declaration."

        return result
