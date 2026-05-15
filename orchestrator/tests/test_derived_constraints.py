# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Deterministic tests for generic derived architecture constraints.

These tests do not call an LLM. They verify the rule-based contract checker
that runs before the holistic LLM constraint review.
"""

from __future__ import annotations

import json


def _diagram_with_contract(contract: str) -> dict:
    return {
        "blocks": [
            {
                "name": "ingest",
                "tier": 2,
                "interfaces": {"pixels": {"width": 8}, "macroblocks": {"width": 768}},
                "semantic_contracts": [contract],
                "estimated_gates": 10000,
            },
            {
                "name": "encode",
                "tier": 3,
                "interfaces": {"macroblocks": {"width": 768}, "bytes": {"width": 8}},
                "semantic_contracts": ["Consumes macroblocks in the same raster order."],
                "estimated_gates": 20000,
            },
        ],
        "connections": [
            {"from": "ingest", "to": "encode", "interface": "macroblocks", "data_width": 768}
        ],
        "system_invariants": [contract],
    }


def test_derived_geometry_passes_for_consistent_contract(tmp_path):
    from orchestrator.architecture.constraints import _check_derived_constraints

    contract = (
        "For 640x360 frames split into 8x8 macroblocks, emit 80 macroblock "
        "columns and 45 macroblock rows in raster order. mb_x=0..79, "
        "mb_y=0..44. Exactly 3600 macroblocks per frame. mb_x uses 7 bits "
        "and mb_y uses 6 bits."
    )

    violations = _check_derived_constraints(
        block_diagram=_diagram_with_contract(contract),
        memory_map={},
        clock_tree={},
        register_spec={},
        requirements=contract,
        ers_spec={},
        project_root=str(tmp_path),
    )

    assert violations == []
    audit = json.loads((tmp_path / ".socmate" / "derived_constraints_audit.json").read_text())
    assert audit["facts"]["source_dim"][:2] == [640, 360]


def test_derived_geometry_catches_transposed_rows_and_columns(tmp_path):
    from orchestrator.architecture.constraints import _check_derived_constraints

    contract = (
        "For 640x360 frames split into 8x8 macroblocks, emit 45 macroblock "
        "columns and 80 macroblock rows. mb_x=0..44, mb_y=0..79. "
        "Exactly 3600 macroblocks per frame."
    )

    violations = _check_derived_constraints(
        block_diagram=_diagram_with_contract(contract),
        memory_map={},
        clock_tree={},
        register_spec={},
        requirements=contract,
        ers_spec={},
        project_root=str(tmp_path),
    )

    checks = {v["check"] for v in violations}
    assert "derived_geometry_columns_rows" in checks
    assert "derived_coordinate_range" in checks
    assert any("80 columns and 45 rows" in v["violation"] for v in violations)


def test_derived_geometry_catches_too_narrow_coordinate_width(tmp_path):
    from orchestrator.architecture.constraints import _check_derived_constraints

    contract = (
        "For 640x360 frames split into 8x8 macroblocks, emit 80 macroblock "
        "columns and 45 macroblock rows. mb_x=0..79, mb_y=0..44. "
        "Exactly 3600 macroblocks per frame. mb_x is 6 bits; mb_y is 6 bits."
    )

    violations = _check_derived_constraints(
        block_diagram=_diagram_with_contract(contract),
        memory_map={},
        clock_tree={},
        register_spec={},
        requirements=contract,
        ers_spec={},
        project_root=str(tmp_path),
    )

    width_violations = [v for v in violations if v["check"] == "derived_coordinate_width"]
    assert width_violations
    assert "requires at least 7 bits" in width_violations[0]["violation"]


def test_derived_geometry_catches_bad_total_count(tmp_path):
    from orchestrator.architecture.constraints import _check_derived_constraints

    contract = (
        "For a 96x64 matrix split into 16x8 tiles, emit 6 tile columns and "
        "8 tile rows. Exactly 47 tiles total."
    )

    violations = _check_derived_constraints(
        block_diagram=_diagram_with_contract(contract),
        memory_map={},
        clock_tree={},
        register_spec={},
        requirements=contract,
        ers_spec={},
        project_root=str(tmp_path),
    )

    assert any(v["check"] == "derived_transaction_count" for v in violations)
