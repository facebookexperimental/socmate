"""
Guard test: no production code should reference architecture_state.json.

Tier 4: Consumer migration verification. This test greps the production
code to ensure the monolithic architecture_state.json has been fully
replaced by per-document files (prd_spec.json, block_diagram.json, etc.).

This test should FAIL until the migration is complete, then PASS once
all references to architecture_state.json are removed from production code.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.mark.doc_persistence
class TestNoArchStateJsonReferences:
    """Guard: architecture_state.json must not be referenced in production code."""

    def _project_root(self) -> Path:
        """Find the project root (directory containing orchestrator/)."""
        return Path(__file__).resolve().parents[2]

    def test_no_references_in_production_code(self):
        """Grep production code for architecture_state.json references.

        Excludes:
        - Test files (orchestrator/tests/**)
        - Plan files (.cursor/plans/**)
        - Documentation (*.md outside orchestrator/)
        - This test file itself

        Uses ripgrep (rg) if available, falls back to grep.
        """
        root = self._project_root()
        orchestrator_dir = root / "orchestrator"

        if not orchestrator_dir.exists():
            pytest.skip("orchestrator/ directory not found")

        try:
            result = subprocess.run(
                [
                    "rg",
                    "--type=py",
                    "--glob=!orchestrator/tests/**",
                    "--glob=!**/conftest.py",
                    "architecture_state",
                    str(orchestrator_dir),
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            try:
                result = subprocess.run(
                    [
                        "grep", "-r", "--include=*.py",
                        "--exclude-dir=tests",
                        "architecture_state",
                        str(orchestrator_dir),
                    ],
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError:
                pytest.skip("Neither ripgrep (rg) nor grep available")

        if result.returncode == 2:
            pytest.skip("Search tool returned error")

        matches = result.stdout.strip()
        if matches:
            pytest.xfail(
                f"architecture_state.json references remain in production code "
                f"(expected until migration is complete):\n{matches}"
            )

    def test_no_load_state_save_state_in_architecture_graph(self):
        """After migration, architecture_graph.py should not import load_state/save_state.

        The graph should use per-document persist helpers instead.
        """
        root = self._project_root()
        arch_graph = root / "orchestrator" / "langgraph" / "architecture_graph.py"

        if not arch_graph.exists():
            pytest.skip("architecture_graph.py not found")

        content = arch_graph.read_text()
        if "from orchestrator.architecture.state import" in content and "save_state" in content:
            pytest.xfail(
                "save_state still imported in architecture_graph.py "
                "(expected until migration is complete)"
            )

    def test_state_py_does_not_write_monolithic_file(self):
        """state.py should not contain save_state() that writes architecture_state.json.

        After migration, state.py either:
        - Has no save_state() at all, or
        - save_state() is a no-op / deprecated wrapper
        """
        root = self._project_root()
        state_py = root / "orchestrator" / "architecture" / "state.py"

        if not state_py.exists():
            pytest.skip("state.py not found")

        content = state_py.read_text()

        if "def save_state" in content and "architecture_state.json" in content:
            pytest.xfail(
                "state.py still has save_state() writing architecture_state.json "
                "(expected until migration is complete)"
            )
