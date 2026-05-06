# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
Read and query OTel spans from the SQLite trace store.

The main entry point is :func:`get_node_traces`, which returns spans
for a given graph-node name grouped by attempt/round and assembled
into a parent-child tree.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def _ns_to_ms(ns: int | None) -> float | None:
    """Convert nanoseconds to milliseconds."""
    if ns is None:
        return None
    return round(ns / 1_000_000, 2)


def _duration_ms(start_ns: int | None, end_ns: int | None) -> float | None:
    if start_ns is None or end_ns is None:
        return None
    return round((end_ns - start_ns) / 1_000_000, 2)


_STATUS_NAMES = {0: "unset", 1: "ok", 2: "error"}


def _row_to_span(row: tuple) -> dict[str, Any]:
    """Convert a SQLite row to a span dict."""
    (
        span_id,
        trace_id,
        parent_id,
        node_name,
        name,
        status_code,
        status_msg,
        start_ns,
        end_ns,
        attributes_json,
        events_json,
        resource_json,
    ) = row

    attrs: dict = json.loads(attributes_json) if attributes_json else {}
    events: list = json.loads(events_json) if events_json else []
    resource: dict = json.loads(resource_json) if resource_json else {}

    return {
        "span_id": span_id,
        "trace_id": trace_id,
        "parent_id": parent_id,
        "node_name": node_name,
        "name": name,
        "status": _STATUS_NAMES.get(status_code, "unset"),
        "status_message": status_msg,
        "start_ns": start_ns,
        "end_ns": end_ns,
        "duration_ms": _duration_ms(start_ns, end_ns),
        "attributes": attrs,
        "events": events,
        "resource": resource,
        "children": [],
    }


def _build_tree(spans: list[dict]) -> list[dict]:
    """Assemble flat spans into a parent-child tree.

    Returns the list of root-level spans (those whose parent is not in
    the span set).
    """
    by_id: dict[str, dict] = {s["span_id"]: s for s in spans}
    roots: list[dict] = []

    for span in spans:
        pid = span["parent_id"]
        if pid and pid in by_id:
            by_id[pid]["children"].append(span)
        else:
            roots.append(span)

    return roots


def _attempt_key(span: dict) -> int:
    """Extract the attempt / round number from span attributes."""
    attrs = span.get("attributes", {})
    # Block-RTL and pipeline graphs use "attempt" (or "attempt.new")
    for key in ("attempt", "attempt.new", "round", "round.new"):
        val = attrs.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    return 1


def get_failure_diagnostics(
    db_path: str,
    graph_filter: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Query traces.db for recent failures and build a diagnostic summary.

    Returns a dict suitable for inclusion in MCP get_*_state() responses,
    giving the outer-loop agent visibility into what failed, when, and why
    without having to read raw log files.

    Args:
        db_path: Path to the ``traces.db`` SQLite file.
        graph_filter: If set, only include spans whose name or attributes
            contain this string (e.g. ``"backend"``, ``"pipeline"``).
        limit: Max number of failure spans to return.

    Returns::

        {
            "failure_count": 3,
            "failures": [
                {
                    "name": "Run PnR [adder_8bit] attempt 1",
                    "node": "Run PnR",
                    "status": "error",
                    "status_message": "DPL-0036",
                    "duration_ms": 12345.0,
                    "error": "Detailed placement failed",
                    "category": "PNR_FAILURE",
                    "success": false,
                    "block": "adder_8bit",
                    "timestamp_iso": "2026-02-21T06:23:22",
                    "attributes": { ... full attributes ... }
                },
                ...
            ],
            "recent_spans": [
                {
                    "name": "Init Design [adder_8bit]",
                    "node": "Init Design",
                    "duration_ms": 500.0,
                    "status": "ok",
                    "key_attrs": { ... selected attributes ... }
                },
                ...
            ],
            "span_summary": {
                "total": 15,
                "ok": 12,
                "error": 3,
                "nodes_seen": ["Init Design", "Flat Top Synthesis", "Run PnR", ...]
            }
        }
    """
    import time as _time

    result: dict[str, Any] = {
        "failure_count": 0,
        "failures": [],
        "recent_spans": [],
        "span_summary": {"total": 0, "ok": 0, "error": 0, "nodes_seen": []},
    }

    try:
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=3000")
    except Exception:
        return result

    try:
        # Failure spans (status_code=2 or attributes contain error/failure)
        if graph_filter:
            cursor = conn.execute(
                "SELECT span_id, trace_id, parent_id, node_name, name, "
                "       status_code, status_msg, start_ns, end_ns, "
                "       attributes, events, resource "
                "FROM spans "
                "WHERE (status_code = 2 "
                "       OR json_extract(attributes, '$.success') = 0 "
                "       OR json_extract(attributes, '$.success') = 'false' "
                "       OR json_extract(attributes, '$.error') IS NOT NULL) "
                "  AND (name LIKE ? OR json_extract(attributes, '$.graph') = ?) "
                "ORDER BY start_ns DESC LIMIT ?",
                (f"%{graph_filter}%", graph_filter, limit),
            )
        else:
            cursor = conn.execute(
                "SELECT span_id, trace_id, parent_id, node_name, name, "
                "       status_code, status_msg, start_ns, end_ns, "
                "       attributes, events, resource "
                "FROM spans "
                "WHERE status_code = 2 "
                "   OR json_extract(attributes, '$.success') = 0 "
                "   OR json_extract(attributes, '$.success') = 'false' "
                "   OR json_extract(attributes, '$.error') IS NOT NULL "
                "ORDER BY start_ns DESC LIMIT ?",
                (limit,),
            )

        failures = []
        for row in cursor.fetchall():
            span = _row_to_span(row)
            attrs = span.get("attributes", {})
            ts_ns = span.get("start_ns")
            ts_iso = ""
            if ts_ns:
                ts_s = ts_ns / 1_000_000_000
                ts_iso = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.localtime(ts_s))

            failures.append({
                "name": span["name"],
                "node": span["node_name"],
                "status": span["status"],
                "status_message": span.get("status_message", ""),
                "duration_ms": span.get("duration_ms"),
                "error": attrs.get("error", attrs.get("error_2d", attrs.get("error_3d", ""))),
                "category": attrs.get("category", ""),
                "success": attrs.get("success"),
                "block": attrs.get("block", attrs.get("design_name", "")),
                "confidence": attrs.get("confidence"),
                "action": attrs.get("action", ""),
                "timestamp_iso": ts_iso,
                "attributes": attrs,
            })

        result["failure_count"] = len(failures)
        result["failures"] = failures

        # Recent spans (last N regardless of status) for timeline context
        cursor = conn.execute(
            "SELECT span_id, trace_id, parent_id, node_name, name, "
            "       status_code, status_msg, start_ns, end_ns, "
            "       attributes, events, resource "
            "FROM spans "
            "WHERE node_name NOT LIKE 'LLM%' "
            "ORDER BY start_ns DESC LIMIT 15",
        )
        recent = []
        for row in cursor.fetchall():
            span = _row_to_span(row)
            attrs = span.get("attributes", {})
            key_attrs = {}
            for k in ("success", "error", "category", "block", "design_name",
                       "gate_count", "design_area_um2", "wns_ns", "violation_count",
                       "match", "clean", "skipped", "action", "confidence",
                       "graph", "log_path"):
                if k in attrs:
                    key_attrs[k] = attrs[k]
            recent.append({
                "name": span["name"],
                "node": span["node_name"],
                "duration_ms": span.get("duration_ms"),
                "status": span["status"],
                "key_attrs": key_attrs,
            })
        result["recent_spans"] = recent

        # Summary counts
        cursor = conn.execute(
            "SELECT COUNT(*), "
            "       SUM(CASE WHEN status_code = 1 THEN 1 ELSE 0 END), "
            "       SUM(CASE WHEN status_code = 2 THEN 1 ELSE 0 END) "
            "FROM spans"
        )
        row = cursor.fetchone()
        if row:
            result["span_summary"]["total"] = row[0] or 0
            result["span_summary"]["ok"] = row[1] or 0
            result["span_summary"]["error"] = row[2] or 0

        cursor = conn.execute(
            "SELECT DISTINCT node_name FROM spans "
            "WHERE node_name IS NOT NULL AND node_name NOT LIKE 'LLM%' "
            "ORDER BY node_name"
        )
        result["span_summary"]["nodes_seen"] = [r[0] for r in cursor.fetchall()]

    except Exception:
        pass
    finally:
        conn.close()

    return result


def get_node_traces(db_path: str, node_name: str) -> list[dict[str, Any]]:
    """Query traces for a graph node, grouped by attempt/round.

    Args:
        db_path: Path to the ``traces.db`` SQLite file.
        node_name: Canonical graph-node name (e.g. ``"Generate RTL"``).

    Returns:
        A list of attempt dicts, each containing a tree of spans::

            [
                {
                    "attempt": 1,
                    "spans": [ {span with children ...} ]
                },
                ...
            ]
    """
    try:
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=3000")
    except Exception:
        return []

    try:
        # Fetch all spans whose node_name matches, plus any child spans
        # that belong to the same trace IDs (for nested LLM call spans).
        cursor = conn.execute(
            "SELECT span_id, trace_id, parent_id, node_name, name, "
            "       status_code, status_msg, start_ns, end_ns, "
            "       attributes, events, resource "
            "FROM spans "
            "WHERE node_name = ? "
            "ORDER BY start_ns ASC",
            (node_name,),
        )
        node_spans = [_row_to_span(r) for r in cursor.fetchall()]
        if not node_spans:
            return []

        # Collect trace IDs to also fetch child spans (e.g. LLM calls)
        list({s["trace_id"] for s in node_spans})
        parent_ids = [s["span_id"] for s in node_spans]

        # Fetch descendants: spans in the same traces whose ancestor
        # is one of our node spans.  We do a breadth-first expansion
        # up to 3 levels deep to keep queries bounded.
        all_spans_by_id: dict[str, dict] = {s["span_id"]: s for s in node_spans}
        frontier = list(parent_ids)

        for _ in range(3):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            cursor = conn.execute(
                "SELECT span_id, trace_id, parent_id, node_name, name, "
                "       status_code, status_msg, start_ns, end_ns, "
                "       attributes, events, resource "
                f"FROM spans WHERE parent_id IN ({placeholders}) "
                "ORDER BY start_ns ASC",
                frontier,
            )
            children = [_row_to_span(r) for r in cursor.fetchall()]
            frontier = []
            for child in children:
                if child["span_id"] not in all_spans_by_id:
                    all_spans_by_id[child["span_id"]] = child
                    frontier.append(child["span_id"])

        # Build trees rooted at the node-level spans
        all_spans = list(all_spans_by_id.values())
        tree_roots = _build_tree(all_spans)

        # Group roots by attempt/round
        by_attempt: dict[int, list[dict]] = {}
        for root in tree_roots:
            key = _attempt_key(root)
            by_attempt.setdefault(key, []).append(root)

        return [
            {"attempt": attempt, "spans": spans}
            for attempt, spans in sorted(by_attempt.items())
        ]
    except Exception:
        return []
    finally:
        conn.close()
