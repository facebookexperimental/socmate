# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
Live LLM integration test: architecture loop for a 16-bit adder.

This test uses REAL Claude CLI calls (no mocks) to drive the architecture
graph through the MCP layer.  It verifies that the LLM can:

  1. Generate sensible ERS sizing questions for a simple adder.
  2. Draft an ERS document from auto-answered questions.
  3. Produce a block diagram with at least one adder-related block.
  4. Run all specialist nodes (memory map, clock tree, register spec).
  5. Pass constraint checks (or iterate until they pass).
  6. Reach the final review gate with a valid architecture.

Run with:
    pytest orchestrator/tests/test_live_architecture.py -v --tb=long

Skip in CI with:
    pytest -m "not live_llm"

Requires:
    - Claude CLI installed and authenticated (``claude`` on PATH)
    - ~5-10 minutes per test
    - ~$0.50-1.00 in API costs per test run
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from orchestrator.tests.conftest import wait_for_status

# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

requires_claude = pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="Claude CLI not installed -- required for live LLM tests",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ADDER_16BIT_REQUIREMENTS = (
    "Design a 16-bit synchronous adder. "
    "Inputs: two 16-bit unsigned operands (A, B) and a carry-in (cin). "
    "Outputs: 16-bit sum (S) and carry-out (cout). "
    "Synchronous design with registered inputs and outputs. "
    "Single clock domain. Target: sky130 PDK, 50 MHz."
)


def _auto_answer_ers(questions: list[dict]) -> dict[str, str]:
    """Generate sensible default answers for ERS questions.

    For each question, picks the first option if available, otherwise
    provides a reasonable default based on the question category.
    """
    category_defaults = {
        "technology": "sky130 130nm",
        "speed_and_feeds": "50 MHz, single sample per clock",
        "area": "< 5,000 gates",
        "power": "Not critical for this design",
        "dataflow": "Combinational with registered I/O, no bus protocol",
    }

    answers = {}
    for q in questions:
        qid = q.get("id", f"q{len(answers)}")
        options = q.get("options", [])
        category = q.get("category", "")

        if options:
            answers[qid] = options[0]
        elif category in category_defaults:
            answers[qid] = category_defaults[category]
        else:
            answers[qid] = "Default / not applicable"

    return answers


async def _wait_for_task_done(runner, target_statuses, timeout=300):
    """Wait for the background task to complete, then check status.

    Uses ``task.done()`` polling to avoid the race where mocked (or fast)
    specialists transition through "running" faster than the poll interval.
    """
    import time

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if runner.task is not None and runner.task.done():
            break
        await asyncio.sleep(1.0)

    if runner.status in target_statuses:
        return runner.status

    raise TimeoutError(
        f"Runner '{runner.name}' status is '{runner.status}', "
        f"expected one of {target_statuses} within {timeout}s. "
        f"Error: {runner.error_message or 'none'}"
    )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def live_arch(reset_mcp_state):
    """Provide the MCP module for live architecture tests.

    Uses ``reset_mcp_state`` for test isolation (tmp_path, fresh singletons).
    Cleans up background tasks on teardown.
    """
    import orchestrator.mcp_server as mcp

    yield mcp

    if mcp._architecture.task and not mcp._architecture.task.done():
        mcp._architecture.task.cancel()
        try:
            await mcp._architecture.task
        except (asyncio.CancelledError, Exception):
            pass
    await mcp._architecture.cleanup()


# ═══════════════════════════════════════════════════════════════════════════
# Live Architecture Tests
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.live_llm
@pytest.mark.slow
@requires_claude
class TestLiveArchitecture16BitAdder:
    """Live LLM architecture tests for a 16-bit adder.

    These tests call the real Claude CLI -- they are slow and cost money.
    Each test is independent and self-contained.
    """

    @pytest.mark.asyncio
    async def test_ers_questions_generated(self, live_arch):
        """LLM generates ERS sizing questions for a 16-bit adder.

        Verifies that:
        - The graph starts and reaches the ERS interrupt.
        - The interrupt contains at least 2 questions.
        - Questions have the expected structure (id, question, category).
        """
        mcp = live_arch

        result = json.loads(await mcp.start_architecture(
            requirements=ADDER_16BIT_REQUIREMENTS,
            target_clock_mhz=50.0,
        ))
        assert "error" not in result, f"start_architecture failed: {result}"

        status = await wait_for_status(
            mcp._architecture, {"interrupted", "error"}, timeout=120,
        )
        assert status == "interrupted", (
            f"Expected 'interrupted', got '{status}'. "
            f"Error: {mcp._architecture.error_message}"
        )

        state = json.loads(await mcp.get_architecture_state())

        assert state["status"] == "interrupted"
        assert state["interrupt_payload"]["type"] == "prd_questions"

        questions = state["interrupt_payload"]["questions"]
        assert len(questions) >= 2, (
            f"Expected at least 2 ERS questions, got {len(questions)}"
        )

        for q in questions:
            assert "id" in q, f"Question missing 'id': {q}"
            assert "question" in q, f"Question missing 'question': {q}"

    @pytest.mark.asyncio
    async def test_full_architecture_cycle(self, live_arch):
        """Full architecture lifecycle with real LLM.

        Walks the entire architecture loop:
        1. Start architecture for 16-bit adder.
        2. Wait for ERS questions.
        3. Auto-answer ERS questions.
        4. Wait for next interrupt (block diagram questions or final review).
        5. Handle any intermediate interrupts (diagram questions, constraints).
        6. Accept at final review.
        7. Verify block specs are sane.

        This is the marquee live test -- it proves the architecture loop
        works end-to-end with a real LLM generating real designs.
        """
        mcp = live_arch

        # --- Step 1: Start ---
        result = json.loads(await mcp.start_architecture(
            requirements=ADDER_16BIT_REQUIREMENTS,
            target_clock_mhz=50.0,
        ))
        assert "error" not in result, f"start_architecture failed: {result}"

        # --- Step 2: Wait for ERS questions ---
        status = await wait_for_status(
            mcp._architecture, {"interrupted", "error"}, timeout=120,
        )
        assert status == "interrupted", (
            f"Expected ERS interrupt, got '{status}'. "
            f"Error: {mcp._architecture.error_message}"
        )
        state = json.loads(await mcp.get_architecture_state())
        assert state["interrupt_payload"]["type"] == "prd_questions"

        # --- Step 3: Auto-answer ERS ---
        questions = state["interrupt_payload"]["questions"]
        answers = _auto_answer_ers(questions)
        assert len(answers) > 0, "No answers generated for ERS questions"

        await mcp.resume_architecture(
            action="continue",
            feedback=json.dumps(answers),
        )

        # --- Step 4-6: Handle interrupts until done ---
        max_interrupts = 10
        for i in range(max_interrupts):
            # Specialists (clock tree, memory map, register spec) each make LLM calls
            # Budget 600s = 10 min per interrupt (vs. 300s = 5 min before)
            status = await _wait_for_task_done(
                mcp._architecture,
                {"interrupted", "done", "error"},
                timeout=600,
            )

            if status == "error":
                pytest.fail(
                    f"Graph errored at interrupt {i}: "
                    f"{mcp._architecture.error_message}"
                )

            if status == "done":
                break

            state = json.loads(await mcp.get_architecture_state())
            payload = state.get("interrupt_payload", {})
            itype = payload.get("type", "unknown")

            if itype == "final_review":
                # Accept the architecture (OK2DEV)
                await mcp.resume_architecture(action="accept")

            elif itype == "prd_questions":
                # Shouldn't loop back to ERS -- but handle it gracefully
                new_answers = _auto_answer_ers(payload.get("questions", []))
                await mcp.resume_architecture(
                    action="continue",
                    feedback=json.dumps(new_answers),
                )

            elif itype == "architecture_review_needed":
                phase = payload.get("phase", "")
                if phase == "block_diagram":
                    # Accept the block diagram as-is
                    await mcp.resume_architecture(action="continue")
                elif phase == "constraints":
                    # Retry on constraint violations
                    await mcp.resume_architecture(action="retry")
                elif phase == "max_rounds_exhausted":
                    # Accept whatever we have
                    await mcp.resume_architecture(action="accept")
                else:
                    await mcp.resume_architecture(action="continue")
            else:
                # Unknown interrupt type -- try continuing
                await mcp.resume_architecture(action="continue")
        else:
            pytest.fail(
                f"Architecture did not complete within {max_interrupts} "
                f"interrupt cycles. Last status: {mcp._architecture.status}"
            )

        # --- Step 7: Verify final state ---
        state = json.loads(await mcp.get_architecture_state())

        assert state["success"] is True, (
            f"Architecture did not succeed. Status: {state['status']}, "
            f"Error: {state.get('error_message', 'none')}"
        )
        assert state["ers_complete"] is True, "ERS was not completed"
        assert state["block_count"] >= 1, "No blocks in the architecture"
        assert state["block_count"] <= 10, (
            f"Too many blocks ({state['block_count']}) for a simple adder"
        )

        # Verify block_specs.json
        specs_path = state.get("block_specs_path", "")
        assert specs_path, "block_specs_path is empty"
        assert Path(specs_path).exists(), (
            f"block_specs_path does not exist: {specs_path}"
        )

        specs = json.loads(Path(specs_path).read_text())
        assert len(specs) >= 1, "block_specs.json is empty"

        # At least one block should relate to an adder
        names = [s["name"] for s in specs]
        has_adder = any(
            keyword in name.lower()
            for name in names
            for keyword in ("adder", "add", "sum", "alu")
        )
        assert has_adder, (
            f"No adder-related block found in names: {names}. "
            f"Expected at least one block with 'adder', 'add', 'sum', or 'alu'."
        )

        # Every block should have required fields
        for spec in specs:
            assert spec.get("name"), f"Block missing name: {spec}"
            assert spec.get("rtl_target"), (
                f"Block '{spec.get('name')}' missing rtl_target"
            )
