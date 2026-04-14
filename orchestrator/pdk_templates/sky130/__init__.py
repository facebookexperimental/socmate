# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Sky130 HD PDK templates for backend EDA flows."""

from __future__ import annotations

from pathlib import Path

_TEMPLATE_DIR = Path(__file__).resolve().parent


def load_template(name: str) -> str:
    """Load a Tcl template file by name (without extension)."""
    path = _TEMPLATE_DIR / f"{name}.tcl"
    if not path.exists():
        raise FileNotFoundError(f"Sky130 template not found: {path}")
    return path.read_text()


def load_filler_cells() -> list[str]:
    """Load the ordered filler cell list from filler_cells.txt."""
    path = _TEMPLATE_DIR / "filler_cells.txt"
    if not path.exists():
        return [
            "sky130_fd_sc_hd__decap_12",
            "sky130_fd_sc_hd__decap_8",
            "sky130_fd_sc_hd__decap_6",
            "sky130_fd_sc_hd__decap_4",
            "sky130_fd_sc_hd__decap_3",
            "sky130_fd_sc_hd__fill_2",
            "sky130_fd_sc_hd__fill_1",
        ]
    return [
        line.strip() for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
