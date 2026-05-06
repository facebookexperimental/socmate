# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
Block diagram visualization JSON schema and validation.

Validates that the block diagram doc JSON matches the format expected by
the ReactFlow-based Block Diagram viewer tab. Uses the same node type
vocabulary as taskgraph_dash_component.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Valid node types matching taskgraph_dash_component architecture graph
VALID_NODE_TYPES = frozenset({
    "",              # Subsystem / group container
    "compute",       # Processing / DSP / CPU blocks
    "bus",           # Interconnects, AXI fabric
    "memory",        # FIFO, SRAM, RAM, buffers
    "sensor",        # ADC, analog front-end
    "hwa",           # Hardware accelerator / CSR bridges
    "power_domain",  # Power domain grouping
    "pll",           # PLL / clock generation
    "pcie",          # PCIe interface
    "i3c",           # I3C / I2C / SPI interface
    "gpio",          # GPIO / IO pads
    "pmic",          # Power management
})

VALID_EDGE_TYPES = frozenset({
    "edgeArchGraph",
})

VALID_NODE_RF_TYPES = frozenset({
    "nodeArchGraph",
})


class BlockDiagramValidationError(Exception):
    """Raised when block diagram JSON fails validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"{len(errors)} validation error(s): {'; '.join(errors[:5])}")


def validate_block_diagram_json(doc: dict) -> list[str]:
    """Validate a block diagram visualization JSON document.

    Checks:
    1. Top-level structure (version, metadata, architecture)
    2. Node schema (required fields, valid types, valid IDs)
    3. Edge schema (required fields, source/target reference valid nodes)
    4. Layout configuration
    5. Referential integrity (edges reference existing nodes)
    6. No duplicate IDs

    Args:
        doc: The block diagram JSON document to validate.

    Returns:
        List of error strings. Empty list means valid.
    """
    errors: list[str] = []

    # ── Top-level structure ──
    if not isinstance(doc, dict):
        return ["Document must be a JSON object"]

    if "version" not in doc:
        errors.append("Missing top-level 'version' field")

    if "metadata" not in doc:
        errors.append("Missing top-level 'metadata' field")
    else:
        meta = doc["metadata"]
        if not isinstance(meta, dict):
            errors.append("'metadata' must be an object")
        else:
            for req in ("design_name", "source"):
                if req not in meta:
                    errors.append(f"Missing metadata.{req}")

    if "architecture" not in doc:
        errors.append("Missing top-level 'architecture' field")
        return errors  # Can't validate further

    arch = doc["architecture"]
    if not isinstance(arch, dict):
        errors.append("'architecture' must be an object")
        return errors

    # ── Nodes ──
    nodes = arch.get("systemNodes", [])
    if not isinstance(nodes, list):
        errors.append("architecture.systemNodes must be an array")
        nodes = []

    if len(nodes) == 0:
        errors.append("architecture.systemNodes is empty (no blocks)")

    node_ids: set[str] = set()
    for i, node in enumerate(nodes):
        prefix = f"node[{i}]"
        if not isinstance(node, dict):
            errors.append(f"{prefix}: must be an object")
            continue

        # Required fields
        node_id = node.get("id")
        if not node_id:
            errors.append(f"{prefix}: missing 'id'")
        elif node_id in node_ids:
            errors.append(f"{prefix}: duplicate id '{node_id}'")
        else:
            node_ids.add(node_id)

        rf_type = node.get("type")
        if rf_type not in VALID_NODE_RF_TYPES:
            errors.append(
                f"{prefix} (id={node_id}): invalid ReactFlow type '{rf_type}', "
                f"expected one of {sorted(VALID_NODE_RF_TYPES)}"
            )

        pos = node.get("position")
        if not isinstance(pos, dict) or "x" not in pos or "y" not in pos:
            errors.append(f"{prefix} (id={node_id}): missing or invalid 'position' {{x, y}}")

        data = node.get("data")
        if not isinstance(data, dict):
            errors.append(f"{prefix} (id={node_id}): missing or invalid 'data' object")
            continue

        node_type = data.get("node_type")
        if node_type is not None and node_type not in VALID_NODE_TYPES:
            errors.append(
                f"{prefix} (id={node_id}): invalid node_type '{node_type}', "
                f"expected one of {sorted(VALID_NODE_TYPES)}"
            )

        if "node_name" not in data:
            errors.append(f"{prefix} (id={node_id}): missing data.node_name")

        if "node_parentId" not in data:
            errors.append(f"{prefix} (id={node_id}): missing data.node_parentId")

    # ── Edges ──
    edges = arch.get("systemEdges", [])
    if not isinstance(edges, list):
        errors.append("architecture.systemEdges must be an array")
        edges = []

    edge_ids: set[str] = set()
    for i, edge in enumerate(edges):
        prefix = f"edge[{i}]"
        if not isinstance(edge, dict):
            errors.append(f"{prefix}: must be an object")
            continue

        edge_id = edge.get("id")
        if not edge_id:
            errors.append(f"{prefix}: missing 'id'")
        elif edge_id in edge_ids:
            errors.append(f"{prefix}: duplicate id '{edge_id}'")
        else:
            edge_ids.add(edge_id)

        source = edge.get("source")
        target = edge.get("target")

        if not source:
            errors.append(f"{prefix} (id={edge_id}): missing 'source'")
        elif source not in node_ids:
            errors.append(
                f"{prefix} (id={edge_id}): source '{source}' not found in nodes"
            )

        if not target:
            errors.append(f"{prefix} (id={edge_id}): missing 'target'")
        elif target not in node_ids:
            errors.append(
                f"{prefix} (id={edge_id}): target '{target}' not found in nodes"
            )

        rf_type = edge.get("type")
        if rf_type and rf_type not in VALID_EDGE_TYPES:
            errors.append(
                f"{prefix} (id={edge_id}): invalid edge type '{rf_type}', "
                f"expected one of {sorted(VALID_EDGE_TYPES)}"
            )

    # ── Layout ──
    layout = arch.get("systemLayout")
    if layout is not None:
        if not isinstance(layout, dict):
            errors.append("architecture.systemLayout must be an object")
        elif "elk_layoutOptions" not in layout:
            errors.append("architecture.systemLayout missing 'elk_layoutOptions'")

    return errors
