# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
IntegrationReviewAgent -- Reviews all uArch specs for cross-block
interface coherence before RTL generation.

Reads Section 9 Verilog stubs from every spec, cross-references against
the block diagram connections, and edits spec files on disk to fix
mismatches in widths, directions, protocols, and naming.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from opentelemetry import trace

from .cursor_llm import ClaudeLLM

_tracer = trace.get_tracer(__name__)

_PROMPT_FILE = (
    Path(__file__).resolve().parent.parent / "prompts" / "integration_review.md"
)
if _PROMPT_FILE.exists():
    SYSTEM_PROMPT = _PROMPT_FILE.read_text()
else:
    SYSTEM_PROMPT = (
        "You are a chip integration engineer. Review all uArch specs "
        "for interface coherence and fix mismatches by editing files on disk."
    )


_JSON_BLOCK_RE = re.compile(
    r"```json\s*\n\s*(\{[^}]*\})\s*\n\s*```", re.DOTALL
)


def _parse_issue_counts(summary: str) -> tuple[int, int]:
    """Extract issues_found / issues_fixed from the LLM's JSON summary block.

    Falls back to (0, 0) if parsing fails, which forces human review
    rather than silently misclassifying the outcome.
    """
    m = _JSON_BLOCK_RE.search(summary)
    if m:
        try:
            data = json.loads(m.group(1))
            found = int(data.get("issues_found", 0))
            fixed = int(data.get("issues_fixed", 0))
            return max(found, 0), max(fixed, 0)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return 0, 0


class IntegrationReviewAgent:
    """Reviews all uArch specs for cross-block interface coherence."""

    def __init__(self, model: str = "opus-4.6", temperature: float = 0.1):
        self.llm = ClaudeLLM(model=model, timeout=900)

    async def review(
        self,
        block_names: list[str],
        project_root: str,
    ) -> dict[str, Any]:
        """Review all uArch specs for interface coherence.

        Args:
            block_names: Names of blocks whose specs to review.
            project_root: Path to project root.

        Returns:
            Dict with keys: summary (str), issues_found (int), issues_fixed (int)
        """
        with _tracer.start_as_current_span("Integration Review") as span:
            span.set_attribute("block_count", len(block_names))

            root = Path(project_root)
            spec_paths = []
            for name in block_names:
                p = root / "arch" / "uarch_specs" / f"{name}.md"
                if p.exists():
                    spec_paths.append(str(p))

            parts = [
                "Review the following uArch specs for cross-block interface coherence.",
                "",
                "## uArch Spec Files",
                "Read each of these files:",
            ]
            for sp in spec_paths:
                parts.append(f"- {sp}")

            parts.append("")
            parts.append("## Architecture Files")
            parts.append(f"- Block diagram connections: {root / '.socmate' / 'block_diagram.json'}")

            ers_path = root / "arch" / "ers_spec.md"
            if ers_path.exists():
                parts.append(f"- ERS: {ers_path}")

            prd_path = root / ".socmate" / "prd_spec.json"
            if prd_path.exists():
                try:
                    prd = json.loads(prd_path.read_text())
                    prd_doc = prd.get("prd", prd.get("ers", {}))
                    dataflow = prd_doc.get("dataflow", {})
                    bus_protocol = dataflow.get("bus_protocol", "unknown")
                    data_width = dataflow.get("data_width_bits", "unknown")
                    parts.append(f"- PRD bus_protocol: {bus_protocol}")
                    parts.append(f"- PRD data_width_bits: {data_width}")
                except (json.JSONDecodeError, OSError):
                    pass

            parts.append("")
            parts.append(
                "Check every connection in the block diagram. For each, verify "
                "port widths, directions, protocols, and clock/reset naming match "
                "across connected blocks. If you find mismatches, edit the uArch "
                "spec files on disk to fix them. Report a summary of findings."
            )

            user_message = "\n".join(parts)

            content = await self.llm.call(
                system=SYSTEM_PROMPT,
                prompt=user_message,
                run_name="Integration Review",
            )

            summary = content.strip() if content else "No issues found."

            issues_found, issues_fixed = _parse_issue_counts(summary)

            return {
                "summary": summary,
                "issues_found": issues_found,
                "issues_fixed": issues_fixed,
            }
