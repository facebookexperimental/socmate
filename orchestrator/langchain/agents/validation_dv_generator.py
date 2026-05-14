# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""ValidationDVGenerator -- Generates ERS/KPI validation cocotb tests."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from opentelemetry import trace

from .socmate_llm import DEFAULT_MODEL, ClaudeLLM

_tracer = trace.get_tracer(__name__)

_PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "validation_dv.md"
if _PROMPT_FILE.exists():
    SYSTEM_PROMPT = _PROMPT_FILE.read_text()
else:
    SYSTEM_PROMPT = (
        "You are a Lead Validation DV engineer. Generate a cocotb testbench "
        "that verifies ERS requirements and measurable application KPIs."
    )


class ValidationDVGenerator:
    """Agent for ERS/KPI validation cocotb testbench generation."""

    def __init__(self, model: str = DEFAULT_MODEL, temperature: float = 0.1):
        self.llm = ClaudeLLM(
            model=model,
            timeout=int(os.environ.get("SOCMATE_VALIDATION_DV_TIMEOUT", "2700")),
        )

    async def generate(
        self,
        design_name: str,
        top_rtl_path: str,
        top_rtl_source: str,
        block_summaries: list[dict],
        connections: list[dict],
        ers_context: str,
        block_rtl_paths: dict[str, str] | None = None,
        output_path: str = "",
    ) -> dict[str, Any]:
        """Generate a cocotb validation DV testbench."""
        with _tracer.start_as_current_span(f"Validation DV [{design_name}]") as span:
            span.set_attribute("design_name", design_name)
            span.set_attribute("block_count", len(block_summaries))

            parts = [
                (
                    "Generate a cocotb validation DV testbench for top-level "
                    f"module '{design_name}'."
                ),
                f"Top-level RTL path: {top_rtl_path}",
            ]

            if output_path:
                parts.append(f"Write the complete testbench to: {output_path}.")

            parts.append(
                f"\n--- TOP-LEVEL VERILOG ---\n```verilog\n{top_rtl_source}\n```"
            )

            parts.append("\n--- ERS JSON / REQUIREMENTS CONTEXT ---")
            parts.append(ers_context)

            parts.append("\n--- BLOCK SUMMARIES ---")
            for bs in block_summaries:
                name = bs.get("name", "unknown")
                ports = bs.get("ports", [])
                port_str = ", ".join(
                    f"{p['name']}({p['direction']} [{p.get('width', 1)}-bit])"
                    for p in ports[:30]
                )
                parts.append(f"  {name}: {port_str}")

            if connections:
                parts.append("\n--- ARCHITECTURE CONNECTIONS ---")
                for c in connections[:50]:
                    fb = c.get("from_block", c.get("from", "?"))
                    tb = c.get("to_block", c.get("to", "?"))
                    iface = c.get("interface", c.get("name", ""))
                    dw = c.get("data_width", "?")
                    parts.append(f"  {fb} -> {tb} ({iface}, {dw}-bit)")

            if block_rtl_paths:
                parts.append("\n--- BLOCK RTL PATHS ---")
                for block_name, rtl_path in sorted(block_rtl_paths.items()):
                    parts.append(f"  {block_name}: {rtl_path}")

            parts.append(
                "\n--- VCD / WAVEKIT REQUIREMENT ---\n"
                "The pipeline will dump sim_build/integration/dump.vcd and "
                "audit it with WaveKit. For every RTL/application ERS "
                "requirement, drive stimulus that leaves observable waveform "
                "evidence for reset, handshakes, control/mode selection, "
                "payload movement, KPI counters, and final outputs."
            )

            content = await self.llm.call(
                system=SYSTEM_PROMPT,
                prompt="\n".join(parts),
                run_name=f"Validation DV [{design_name}]",
            )

            testbench = self._extract_python(content)
            test_count = len(re.findall(r"@cocotb\.test\(\)", testbench))

            if not testbench or test_count == 0:
                raise RuntimeError(
                    "Validation DV generation failed: no usable Python cocotb "
                    "testbench with @cocotb.test() functions"
                )
            if "REQUIREMENT_COVERAGE" not in testbench:
                raise RuntimeError(
                    "Validation DV generation failed: testbench did not define "
                    "REQUIREMENT_COVERAGE"
                )
            if not output_path:
                raise RuntimeError(
                    "Validation DV generation failed: no output_path provided"
                )

            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(testbench, encoding="utf-8")

            span.set_attribute("test_count", test_count)
            return {"tb_path": output_path, "test_count": test_count}

    def _extract_python(self, content: str) -> str:
        """Extract Python code block from an LLM response."""
        match = re.search(r"```python\s*\n(.*?)```", content, re.DOTALL)
        if match:
            return match.group(1).strip()
        return content.strip()
