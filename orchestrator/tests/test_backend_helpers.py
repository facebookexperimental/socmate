# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
Tests for the backend helper functions (Tcl generation, report parsing,
subprocess wrappers).

Unit tests run without EDA tools (fast, no Nix).
Integration tests require Nix + Sky130 PDK and are marked with
``@pytest.mark.slow`` and ``@pytest.mark.requires_nix``.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from orchestrator.langgraph.backend_helpers import (
    generate_pnr_tcl,
    generate_drc_tcl,
    generate_rcx_tcl,
    parse_openroad_reports,
    parse_drc_report,
    parse_pnr_stdout,
    _parse_magic_drc_count,
    _parse_lvs_deltas,
    TECH_LEF,
    CELL_LEF,
    LIBERTY,
    OPENROAD_BIN,
    MAGIC_BIN,
    NETGEN_BIN,
    PROJECT_ROOT,
)


# ═══════════════════════════════════════════════════════════════════════════
# Tcl Generation
# ═══════════════════════════════════════════════════════════════════════════

class TestGeneratePnrTcl:
    def test_creates_file(self, tmp_path):
        tcl = generate_pnr_tcl(
            "test_block", "/fake/netlist.v", "/fake/sdc.sdc", str(tmp_path),
        )
        assert Path(tcl).exists()
        assert Path(tcl).name == "pnr_test_block.tcl"

    def test_contains_block_name(self, tmp_path):
        tcl = generate_pnr_tcl(
            "my_adder", "/fake/netlist.v", "/fake/sdc.sdc", str(tmp_path),
        )
        content = Path(tcl).read_text()
        assert "link_design my_adder" in content
        assert "my_adder_routed.def" in content
        assert "my_adder_pnr.v" in content
        assert "my_adder_pwr.v" in content

    def test_contains_make_tracks(self, tmp_path):
        tcl = generate_pnr_tcl(
            "b", "/fake/n.v", "/fake/s.sdc", str(tmp_path),
        )
        content = Path(tcl).read_text()
        assert "make_tracks li1" in content
        assert "make_tracks met1" in content
        assert "make_tracks met5" in content

    def test_contains_set_wire_rc(self, tmp_path):
        tcl = generate_pnr_tcl(
            "b", "/fake/n.v", "/fake/s.sdc", str(tmp_path),
        )
        content = Path(tcl).read_text()
        assert "set_wire_rc -signal -layer met2" in content
        assert "set_wire_rc -clock  -layer met3" in content

    def test_contains_set_routing_layers(self, tmp_path):
        tcl = generate_pnr_tcl(
            "b", "/fake/n.v", "/fake/s.sdc", str(tmp_path),
        )
        content = Path(tcl).read_text()
        assert "set_routing_layers -signal met1-met4 -clock met3-met4" in content

    def test_ends_with_exit(self, tmp_path):
        tcl = generate_pnr_tcl(
            "b", "/fake/n.v", "/fake/s.sdc", str(tmp_path),
        )
        content = Path(tcl).read_text()
        assert content.strip().endswith("exit")

    def test_custom_utilization(self, tmp_path):
        tcl = generate_pnr_tcl(
            "b", "/fake/n.v", "/fake/s.sdc", str(tmp_path),
            utilization=60,
        )
        content = Path(tcl).read_text()
        assert "-utilization 60" in content


class TestGenerateDrcTcl:
    def test_creates_file(self, tmp_path):
        tcl = generate_drc_tcl("test_block", "/fake/routed.def", str(tmp_path))
        assert Path(tcl).exists()
        assert Path(tcl).name == "drc_test_block.tcl"

    def test_contains_block_name(self, tmp_path):
        tcl = generate_drc_tcl("my_block", "/fake/routed.def", str(tmp_path))
        content = Path(tcl).read_text()
        assert "load my_block" in content
        assert "flatten my_block_flat" in content
        assert "my_block.gds" in content
        assert "my_block.spice" in content

    def test_ends_with_quit(self, tmp_path):
        tcl = generate_drc_tcl("b", "/fake/r.def", str(tmp_path))
        content = Path(tcl).read_text()
        assert "quit -noprompt" in content


class TestGenerateRcxTcl:
    def test_creates_file(self, tmp_path):
        tcl = generate_rcx_tcl(
            "test_block", "/fake/routed.def", "/fake/sdc.sdc", str(tmp_path),
        )
        assert Path(tcl).exists()
        assert Path(tcl).name == "rcx_test_block.tcl"

    def test_contains_via_resistance(self, tmp_path):
        tcl = generate_rcx_tcl(
            "b", "/fake/r.def", "/fake/s.sdc", str(tmp_path),
        )
        content = Path(tcl).read_text()
        assert "findLayer mcon" in content
        assert "setResistance 9.249146" in content
        assert "extract_parasitics -ext_model_file" in content

    def test_contains_write_spef(self, tmp_path):
        tcl = generate_rcx_tcl(
            "b", "/fake/r.def", "/fake/s.sdc", str(tmp_path),
        )
        content = Path(tcl).read_text()
        assert "write_spef" in content


# ═══════════════════════════════════════════════════════════════════════════
# Report Parsers
# ═══════════════════════════════════════════════════════════════════════════

class TestParseOpenroadReports:
    def test_parses_wns(self, tmp_path):
        (tmp_path / "timing_wns.rpt").write_text("wns max 0.00\n")
        m = parse_openroad_reports(str(tmp_path))
        assert m["wns_ns"] == 0.0
        assert m["timing_met"] is True

    def test_parses_negative_wns(self, tmp_path):
        (tmp_path / "timing_wns.rpt").write_text("wns max -1.23\n")
        m = parse_openroad_reports(str(tmp_path))
        assert m["wns_ns"] == -1.23
        assert m["timing_met"] is False

    def test_parses_tns(self, tmp_path):
        (tmp_path / "timing_tns.rpt").write_text("tns max 0.00\n")
        m = parse_openroad_reports(str(tmp_path))
        assert m["tns_ns"] == 0.0

    def test_parses_power(self, tmp_path):
        power_rpt = textwrap.dedent("""\
            Group                  Internal  Switching    Leakage      Total
                                      Power      Power      Power      Power (Watts)
            ----------------------------------------------------------------
            Total                  5.93e-05   1.77e-05   3.80e-10   7.70e-05 100.0%
                                      77.0%      23.0%       0.0%
        """)
        (tmp_path / "power.rpt").write_text(power_rpt)
        m = parse_openroad_reports(str(tmp_path))
        assert abs(m["total_power_mw"] - 0.077) < 0.001
        assert m["dynamic_power_mw"] > 0
        assert m["leakage_power_mw"] < 0.001

    def test_parses_setup_slack(self, tmp_path):
        setup_rpt = textwrap.dedent("""\
            Startpoint: a[0]
                       15.40   slack (MET)
        """)
        (tmp_path / "timing_setup.rpt").write_text(setup_rpt)
        m = parse_openroad_reports(str(tmp_path))
        assert m["setup_slack_ns"] == 15.40

    def test_empty_dir(self, tmp_path):
        m = parse_openroad_reports(str(tmp_path))
        assert m["wns_ns"] == 0.0
        assert m["timing_met"] is True


class TestParsePnrStdout:
    def test_parses_area_format2(self):
        stdout = "Design area 955 um^2 49% utilization.\n"
        m = parse_pnr_stdout(stdout)
        assert m["design_area_um2"] == 955.0
        assert m["utilization_pct"] == 49.0

    def test_parses_wns_from_stdout(self):
        stdout = "wns max 0.00\ntns max 0.00\n"
        m = parse_pnr_stdout(stdout)
        assert m["wns_ns"] == 0.0
        assert m["tns_ns"] == 0.0

    def test_parses_power_from_stdout(self):
        stdout = "Total  5.93e-05   1.77e-05   3.80e-10   7.70e-05 100.0%\n"
        m = parse_pnr_stdout(stdout)
        assert abs(m["total_power_mw"] - 0.077) < 0.001

    def test_empty_stdout(self):
        m = parse_pnr_stdout("")
        assert m["design_area_um2"] == 0.0


class TestParseDrcReport:
    def test_clean(self, tmp_path):
        rpt = tmp_path / "drc.rpt"
        rpt.write_text("Design: test\nDRC count: 0\n")
        r = parse_drc_report(str(rpt))
        assert r["clean"] is True
        assert r["violation_count"] == 0

    def test_violations(self, tmp_path):
        rpt = tmp_path / "drc.rpt"
        rpt.write_text("Design: test\nDRC count: 3\nvia spacing\nmetal width\nenclosure\n")
        r = parse_drc_report(str(rpt))
        assert r["clean"] is False
        assert r["violation_count"] == 3
        assert len(r["violations"]) == 3

    def test_missing_file(self):
        r = parse_drc_report("/nonexistent/file.rpt")
        assert r["clean"] is False
        assert r["violation_count"] == -1


class TestParseMagicDrcCount:
    def test_normal_count(self):
        assert _parse_magic_drc_count("DRC violations: 5\n") == 5

    def test_zero_count(self):
        assert _parse_magic_drc_count("DRC violations: 0\n") == 0

    def test_empty_count(self):
        """Magic prints 'DRC violations: ' (no number) when count is 0."""
        assert _parse_magic_drc_count("DRC violations: \n") == 0

    def test_multiple_lines(self):
        stdout = "stuff\nDRC violations: \nmore stuff\nDRC violations: \n"
        assert _parse_magic_drc_count(stdout) == 0

    def test_no_match(self):
        assert _parse_magic_drc_count("no DRC info here\n") == -1


class TestParseLvsDeltas:
    def test_matching(self):
        stdout = "Circuit 1: 117 devices, 50 nets\nCircuit 2: 117 devices, 50 nets\n"
        d, n = _parse_lvs_deltas(stdout)
        assert d == 0
        assert n == 0

    def test_mismatch(self):
        stdout = "Circuit 1: 117 devices, 50 nets\nCircuit 2: 118 devices, 56 nets\n"
        d, n = _parse_lvs_deltas(stdout)
        assert d == 1
        assert n == 6

    def test_no_data(self):
        d, n = _parse_lvs_deltas("nothing here")
        assert d == 0
        assert n == 0


# ═══════════════════════════════════════════════════════════════════════════
# Tool Binary Resolution
# ═══════════════════════════════════════════════════════════════════════════

class TestToolResolution:
    def test_openroad_binary_resolved(self):
        assert OPENROAD_BIN.endswith("openroad-nix.sh")
        assert Path(OPENROAD_BIN).exists()

    def test_magic_binary_resolved(self):
        assert MAGIC_BIN.endswith("magic-nix.sh")
        assert Path(MAGIC_BIN).exists()

    def test_netgen_binary_resolved(self):
        assert NETGEN_BIN.endswith("netgen-nix.sh")
        assert Path(NETGEN_BIN).exists()

    def test_pdk_files_exist(self):
        assert TECH_LEF.exists(), f"Tech LEF not found: {TECH_LEF}"
        assert CELL_LEF.exists(), f"Cell LEF not found: {CELL_LEF}"
        assert LIBERTY.exists(), f"Liberty not found: {LIBERTY}"


# ═══════════════════════════════════════════════════════════════════════════
# Integration Tests (require Nix + PDK -- slow)
# ═══════════════════════════════════════════════════════════════════════════

_NETLIST = PROJECT_ROOT / "syn" / "output" / "adder_16bit" / "adder_16bit_netlist.v"
_SDC = PROJECT_ROOT / "syn" / "output" / "adder_16bit" / "adder_16bit.sdc"
_HAS_NETLIST = _NETLIST.exists() and _SDC.exists()
_HAS_NIX = shutil.which("nix") is not None

requires_nix = pytest.mark.skipif(
    not _HAS_NIX, reason="Nix not installed"
)
requires_netlist = pytest.mark.skipif(
    not _HAS_NETLIST, reason="adder_16bit netlist not found (run synthesis first)"
)


@requires_nix
@requires_netlist
class TestPnRIntegration:
    """Run real OpenROAD PnR on adder_16bit. Slow (~15s)."""

    @pytest.mark.slow
    def test_run_pnr_flow(self, tmp_path):
        from orchestrator.langgraph.backend_helpers import run_pnr_flow

        out_dir = str(tmp_path / "pnr")
        result = run_pnr_flow(
            "adder_16bit", str(_NETLIST), str(_SDC), out_dir,
        )

        assert result["success"] is True
        assert result["timing_met"] is True
        assert result["design_area_um2"] > 0
        assert result["total_power_mw"] > 0
        assert result["route_drc_violations"] == 0
        assert Path(result["routed_def_path"]).exists()
        assert Path(result["pnr_verilog_path"]).exists()
        assert Path(result["pwr_verilog_path"]).exists()
        assert result.get("log_path")


@requires_nix
@requires_netlist
class TestDrcIntegration:
    """Run real Magic DRC on adder_16bit. Slow (~5s)."""

    @pytest.mark.slow
    def test_run_drc_flow(self, tmp_path):
        from orchestrator.langgraph.backend_helpers import run_pnr_flow, run_drc_flow

        out_dir = str(tmp_path / "pnr")
        pnr = run_pnr_flow(
            "adder_16bit", str(_NETLIST), str(_SDC), out_dir,
        )
        assert pnr["success"]

        drc = run_drc_flow(
            "adder_16bit", pnr["routed_def_path"], out_dir,
        )

        assert drc["clean"] is True
        assert drc["violation_count"] == 0
        assert Path(drc["gds_path"]).exists()
        assert Path(drc["spice_path"]).exists()

        # No stray .ext files in project root
        ext_files = list(PROJECT_ROOT.glob("*.ext"))
        assert len(ext_files) == 0, f"Stray .ext files: {ext_files}"


@requires_nix
@requires_netlist
class TestLvsIntegration:
    """Run real Netgen LVS on adder_16bit. Slow (~20s total)."""

    @pytest.mark.slow
    def test_run_lvs_flow(self, tmp_path):
        from orchestrator.langgraph.backend_helpers import (
            run_pnr_flow, run_drc_flow, run_lvs_flow,
        )

        out_dir = str(tmp_path / "pnr")
        pnr = run_pnr_flow(
            "adder_16bit", str(_NETLIST), str(_SDC), out_dir,
        )
        assert pnr["success"]

        drc = run_drc_flow(
            "adder_16bit", pnr["routed_def_path"], out_dir,
        )
        assert drc["clean"]

        lvs = run_lvs_flow(
            "adder_16bit", drc["spice_path"],
            pnr["pwr_verilog_path"], out_dir,
        )

        assert lvs["match"] is True
        # Expected tap cell delta
        assert lvs["device_delta"] <= 2
        assert Path(lvs["report_path"]).exists()
