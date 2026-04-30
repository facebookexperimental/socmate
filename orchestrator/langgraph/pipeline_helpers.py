# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
Reusable helper functions for the ASIC pipeline.

Extracted from run_pipeline.py so that both the LangGraph pipeline graph
and the CLI runner can share the same implementation.

Provides:
- Constants: PROJECT_ROOT, PDK_ROOT, LIBERTY_FILE, CONFIG_PATH
- Config: load_config(), get_blocks_by_tier(), get_sorted_block_queue()
- Golden model: create_golden_model_wrapper()
- RTL generation: generate_rtl()
- Lint: lint_rtl()
- Testbench: generate_testbench()
- Simulation: run_simulation()
- Synthesis: synthesize_block()
- Debug: diagnose_failure()
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time as _time
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(
    os.environ.get(
        "SOCMATE_PROJECT_ROOT",
        str(Path(__file__).resolve().parent.parent.parent),
    )
)
CONFIG_PATH = PROJECT_ROOT / "orchestrator" / "config.yaml"
PDK_ROOT = PROJECT_ROOT / ".pdk"
def _find_liberty_file() -> Path:
    """Locate the Sky130 liberty file, checking both sky130A and sky130B."""
    lib_name = "sky130_fd_sc_hd__tt_025C_1v80.lib"
    for variant in ("sky130A", "sky130B"):
        candidate = PDK_ROOT / variant / "libs.ref" / "sky130_fd_sc_hd" / "lib" / lib_name
        if candidate.exists():
            return candidate
    # Fallback to sky130A path (will fail at synthesis time with a clear error)
    return PDK_ROOT / "sky130A" / "libs.ref" / "sky130_fd_sc_hd" / "lib" / lib_name


LIBERTY_FILE = _find_liberty_file()


# ---------------------------------------------------------------------------
# Preflight check -- validate PDK/EDA tools before burning retry budgets
# ---------------------------------------------------------------------------

def preflight_check(phases: list[str] | None = None) -> dict:
    """Validate that required PDK files and EDA tools exist.

    Args:
        phases: List of phases to check. Options: "pipeline", "backend".
            Defaults to ["pipeline"] if not specified.

    Returns:
        {"ok": True} or {"ok": False, "errors": [...], "warnings": [...]}
    """
    if not phases:
        phases = ["pipeline"]

    errors: list[str] = []
    warnings: list[str] = []

    if "pipeline" in phases:
        if not LIBERTY_FILE.exists():
            errors.append(f"Liberty file not found: {LIBERTY_FILE}")
        if not shutil.which("verilator"):
            errors.append("verilator not found on PATH")
        if not shutil.which("yosys"):
            errors.append("yosys not found on PATH")
        if not PDK_ROOT.exists():
            errors.append(f"PDK root directory not found: {PDK_ROOT}")
        elif not any((PDK_ROOT / v).is_dir() for v in ("sky130A", "sky130B")):
            errors.append(f"No sky130A or sky130B variant found in {PDK_ROOT}")

    if "backend" in phases:
        from orchestrator.langgraph.backend_helpers import (
            TECH_LEF,
            CELL_LEF,
            CELL_GDS,
            MAGIC_RC,
            OPENROAD_BIN,
            MAGIC_BIN,
            NETGEN_BIN,
        )
        if not TECH_LEF.exists():
            errors.append(f"Tech LEF not found: {TECH_LEF}")
        if not CELL_LEF.exists():
            errors.append(f"Cell LEF not found: {CELL_LEF}")
        if not CELL_GDS.exists():
            errors.append(f"Cell GDS not found: {CELL_GDS}")
        if not MAGIC_RC.exists():
            errors.append(f"Magic RC file not found: {MAGIC_RC}")
        if not Path(OPENROAD_BIN).exists():
            errors.append(f"OpenROAD binary/script not found: {OPENROAD_BIN}")
        if not Path(MAGIC_BIN).exists():
            errors.append(f"Magic binary/script not found: {MAGIC_BIN}")
        if not Path(NETGEN_BIN).exists():
            errors.append(f"Netgen binary/script not found: {NETGEN_BIN}")

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}

# ANSI colors for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def log(msg: str, color: str = "") -> None:
    """Print a coloured log line to stdout."""
    prefix = f"{color}{BOLD}" if color else ""
    suffix = RESET if color else ""
    print(f"{prefix}{msg}{suffix}", flush=True)


# ---------------------------------------------------------------------------
# Step log files  (<project>/.socmate/step_logs/<block>/<step>_attempt<N>.log)
# ---------------------------------------------------------------------------

_LOG_DIR = Path(
    os.environ.get("SOCMATE_LOG_DIR", str(PROJECT_ROOT / ".socmate" / "step_logs"))
)


def _write_step_log(
    block_name: str,
    step: str,
    cmd: list[str],
    result: subprocess.CompletedProcess,
    attempt: int = 1,
) -> str:
    """Write full subprocess output to /tmp and return the log file path."""
    log_dir = _LOG_DIR / block_name
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{step}_attempt{attempt}.log"

    ts = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime())
    content = (
        f"=== {step.upper()} LOG ===\n"
        f"Timestamp: {ts}\n"
        f"Block: {block_name}\n"
        f"Attempt: {attempt}\n"
        f"Command: {' '.join(cmd)}\n"
        f"Return code: {result.returncode}\n"
        f"\n=== STDOUT ===\n"
        f"{result.stdout}\n"
        f"\n=== STDERR ===\n"
        f"{result.stderr}\n"
    )
    log_file.write_text(content, encoding="utf-8")
    return str(log_file)


def _write_step_log_error(
    block_name: str,
    step: str,
    cmd: list[str],
    error_msg: str,
    attempt: int = 1,
) -> str:
    """Write an error-only log file when subprocess didn't complete normally."""
    log_dir = _LOG_DIR / block_name
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{step}_attempt{attempt}.log"

    ts = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime())
    content = (
        f"=== {step.upper()} LOG ===\n"
        f"Timestamp: {ts}\n"
        f"Block: {block_name}\n"
        f"Attempt: {attempt}\n"
        f"Command: {' '.join(cmd)}\n"
        f"Return code: N/A (exception)\n"
        f"\n=== ERROR ===\n"
        f"{error_msg}\n"
    )
    log_file.write_text(content, encoding="utf-8")
    return str(log_file)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load the project config from orchestrator/config.yaml."""
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_blocks_by_tier(config: dict) -> dict[int, list[dict]]:
    """Group blocks by tier, returning sorted dict."""
    tiers: dict[int, list[dict]] = {}
    for name, spec in config.get("blocks", {}).items():
        tier = spec.get("tier", 1)
        block = {"name": name, **spec}
        tiers.setdefault(tier, []).append(block)
    return dict(sorted(tiers.items()))


def get_sorted_block_queue(config: dict) -> list[dict]:
    """Return a flat list of blocks sorted by tier (1 -> 2 -> 3)."""
    tiers = get_blocks_by_tier(config)
    queue: list[dict] = []
    for _tier_num, blocks in tiers.items():
        queue.extend(blocks)
    return queue


def get_tier_list(block_queue: list[dict]) -> list[int]:
    """Return sorted unique tier values from a block queue.

    Example: ``[1, 2, 3]`` for blocks spanning three tiers.
    """
    return sorted(set(b.get("tier", 1) for b in block_queue))


def get_blocks_for_tier(block_queue: list[dict], tier: int) -> list[dict]:
    """Filter blocks by tier number."""
    return [b for b in block_queue if b.get("tier", 1) == tier]


# ---------------------------------------------------------------------------
# Golden model wrapper creation
# ---------------------------------------------------------------------------

def create_golden_model_wrapper(block_name: str, python_source_path: str) -> None:
    """Create a <block_name>_model.py wrapper on PYTHONPATH for cocotb import.

    The testbench generator expects to import ``from <block_name>_model import ...``.
    We create a thin wrapper that imports from the actual source location.
    """
    if not python_source_path or not python_source_path.strip():
        return
    source_path = PROJECT_ROOT / python_source_path
    if not source_path.exists() or source_path.is_dir():
        return

    wrapper_dir = PROJECT_ROOT / "tb" / "cocotb"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = wrapper_dir / f"{block_name}_model.py"

    if wrapper_path.exists():
        return

    module_parts = source_path.relative_to(PROJECT_ROOT).with_suffix("").parts
    module_path = ".".join(module_parts)

    wrapper_content = f'''"""Auto-generated wrapper for {block_name} golden model."""
import sys
from pathlib import Path

# Add project root to path so we can import the golden model
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from {module_path} import *
'''
    wrapper_path.write_text(wrapper_content)


# ---------------------------------------------------------------------------
# Microarchitecture Spec Generation
# ---------------------------------------------------------------------------

async def generate_uarch_spec(
    block: dict,
    feedback: str = "",
    previous_spec: str = "",
    constraints: list[dict] = None,
    callbacks: list = None,
) -> dict:
    """Generate a microarchitecture specification from Python golden model.

    Returns a dict with keys: spec_text, spec_summary, block_name.
    Also writes the spec to ``arch/uarch_specs/<block_name>.md``.
    """
    from orchestrator.langchain.agents.uarch_spec_generator import UarchSpecGenerator
    from orchestrator.langchain.agents.cursor_llm import DEFAULT_MODEL

    python_source_rel = block.get("python_source", "")
    if python_source_rel and python_source_rel.strip():
        source_path = PROJECT_ROOT / python_source_rel
        if not source_path.exists() or source_path.is_dir():
            python_source = ""
        else:
            python_source = source_path.read_text()
    else:
        python_source = ""

    agent = UarchSpecGenerator(model=DEFAULT_MODEL, temperature=0.2)
    result = await agent.generate(
        block_name=block["name"],
        python_source=python_source,
        description=block.get("description", ""),
        feedback=feedback,
        previous_spec=previous_spec,
        constraints=constraints,
        callbacks=callbacks,
        project_root=str(PROJECT_ROOT),
    )

    from orchestrator.architecture.state import ARCH_DOC_DIR

    spec_dir = PROJECT_ROOT / ARCH_DOC_DIR / "uarch_specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / f"{block['name']}.md"
    spec_path.write_text(result["spec_text"])
    result["spec_path"] = str(spec_path)

    return result


# ---------------------------------------------------------------------------
# RTL Generation
# ---------------------------------------------------------------------------

async def generate_rtl(
    block: dict, attempt: int,
    callbacks: list = None,
) -> dict:
    """Generate Verilog RTL -- disk-first, agent reads/writes all files.

    The agent reads the uArch spec, ERS, constraints, golden model, and
    previous error from disk, and writes the Verilog to block["rtl_target"].
    """
    from orchestrator.langchain.agents.rtl_generator import RTLGeneratorAgent
    from orchestrator.langchain.agents.cursor_llm import DEFAULT_MODEL

    rtl_path = PROJECT_ROOT / block["rtl_target"]
    rtl_path.parent.mkdir(parents=True, exist_ok=True)

    agent = RTLGeneratorAgent(model=DEFAULT_MODEL, temperature=0.1)
    try:
        result = await agent.generate(
            block_name=block["name"],
            description=block.get("description", ""),
            attempt=attempt,
            rtl_target=block["rtl_target"],
            python_source_path=block.get("python_source", ""),
            project_root=str(PROJECT_ROOT),
            callbacks=callbacks,
        )
        return result
    except (ValueError, Exception) as e:
        log(f"  [RTL-GEN] Error: {e}", RED)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------

def lint_rtl(rtl_path: str, block_name: str, attempt: int = 1) -> dict:
    """Run Verilator lint on a Verilog file (read-only, no file mutation).

    Uses -Wno-fatal so style warnings (unused signals, EOF newline, etc.)
    don't block the pipeline.  Real errors (%Error) still cause failure.
    """
    cmd = [
        "verilator", "--lint-only", "-Wall", "-Wno-fatal",
        "-Wno-EOFNEWLINE",
    ]
    if block_name == "viterbi_decoder":
        cmd.append("-Wno-BLKSEQ")
    cmd.append(rtl_path)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        log_path = _write_step_log(block_name, "lint", cmd, result, attempt)
        stderr = result.stderr.strip()
        has_errors = "%Error" in stderr
        if result.returncode == 0 and not has_errors:
            return {"clean": True, "warnings": stderr, "log_path": log_path}
        else:
            return {"clean": False, "errors": stderr[-2000:], "log_path": log_path}
    except subprocess.TimeoutExpired:
        log_path = _write_step_log_error(block_name, "lint", cmd, "Verilator lint timed out", attempt)
        return {"clean": False, "errors": "Verilator lint timed out", "log_path": log_path}
    except FileNotFoundError:
        log_path = _write_step_log_error(block_name, "lint", cmd, "Verilator not installed", attempt)
        return {"clean": False, "errors": "Verilator not installed", "log_path": log_path}


# ---------------------------------------------------------------------------
# Testbench Generation
# ---------------------------------------------------------------------------

async def generate_testbench(
    block: dict,
    callbacks: list = None,
) -> dict:
    """Generate cocotb testbench -- disk-first, agent reads/writes all files."""
    from orchestrator.langchain.agents.testbench_generator import TestbenchGeneratorAgent
    from orchestrator.langchain.agents.cursor_llm import DEFAULT_MODEL

    rtl_path = str(PROJECT_ROOT / block["rtl_target"])
    tb_path = str(PROJECT_ROOT / block["testbench"])
    Path(tb_path).parent.mkdir(parents=True, exist_ok=True)

    agent = TestbenchGeneratorAgent(model=DEFAULT_MODEL, temperature=0.1)
    result = await agent.generate(
        block_name=block["name"],
        rtl_path=rtl_path,
        python_source_path=block.get("python_source", ""),
        testbench_path=tb_path,
        project_root=str(PROJECT_ROOT),
        callbacks=callbacks,
    )
    return result


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def run_simulation(block: dict, rtl_path: str, tb_path: str, attempt: int = 1) -> dict:
    """Run cocotb simulation with Verilator."""
    block_name = block["name"]
    sim_dir = PROJECT_ROOT / "sim_build" / block_name
    sim_dir.mkdir(parents=True, exist_ok=True)

    makefile_content = f"""
SIM = verilator
TOPLEVEL_LANG = verilog
VERILOG_SOURCES = {rtl_path}
TOPLEVEL = {block_name}
COCOTB_TEST_MODULES = test_{block_name}
EXTRA_ARGS += --trace
include $(shell cocotb-config --makefiles)/Makefile.sim
"""
    (sim_dir / "Makefile").write_text(makefile_content)

    shutil.copy2(tb_path, sim_dir / f"test_{block_name}.py")

    create_golden_model_wrapper(block_name, block.get("python_source", ""))

    wrapper_src = PROJECT_ROOT / "tb" / "cocotb" / f"{block_name}_model.py"
    if wrapper_src.exists():
        shutil.copy2(wrapper_src, sim_dir / f"{block_name}_model.py")

    env = os.environ.copy()
    import sys
    venv_bin = str(Path(sys.prefix) / "bin")
    env["PATH"] = f"{venv_bin}:{env.get('PATH', '/usr/bin:/bin')}"
    env["SHELL"] = shutil.which("bash") or "/bin/bash"
    env["PYTHONPATH"] = f"{sim_dir}:{PROJECT_ROOT}:{env.get('PYTHONPATH', '')}"

    make_bin = shutil.which("make") or "make"

    try:
        result = subprocess.run(
            [make_bin, "-C", str(sim_dir)],
            capture_output=True,
            text=True,
            timeout=900,
            env=env,
        )
        log_path = _write_step_log(block_name, "simulate", [make_bin, "-C", str(sim_dir)], result, attempt)
        output = (result.stdout + "\n" + result.stderr)[-5000:]
        passed = result.returncode == 0

        vcd_path = sim_dir / "dump.vcd"
        return {
            "passed": passed,
            "log": output,
            "returncode": result.returncode,
            "log_path": log_path,
            "vcd_path": str(vcd_path) if vcd_path.exists() else "",
        }
    except subprocess.TimeoutExpired:
        cmd = [make_bin, "-C", str(sim_dir)]
        log_path = _write_step_log_error(block_name, "simulate", cmd, "Simulation timed out (10 min)", attempt)
        return {"passed": False, "log": "Simulation timed out (10 min)", "log_path": log_path}
    except FileNotFoundError as e:
        cmd = [make_bin, "-C", str(sim_dir)]
        log_path = _write_step_log_error(block_name, "simulate", cmd, f"Tool not found: {e}", attempt)
        return {"passed": False, "log": f"Tool not found: {e}", "log_path": log_path}


# ---------------------------------------------------------------------------
# SDC Generation
# ---------------------------------------------------------------------------

def _detect_clock_port(rtl_source: str) -> str:
    """Regex-based clock port detection from Verilog source.

    Scans the module port declarations for common clock port names.
    Returns the detected clock port name, or 'clk' as fallback.
    """
    import re

    port_pattern = re.compile(
        r'\binput\s+(?:wire\s+)?(\w+)', re.MULTILINE
    )
    ports = port_pattern.findall(rtl_source)

    for name in ("clk", "clk_in", "clock", "CLK", "CLOCK"):
        if name in ports:
            return name

    for p in ports:
        if "clk" in p.lower() or "clock" in p.lower():
            return p

    return "clk"


async def generate_sdc(
    block_name: str,
    rtl_source: str,
    target_clock_mhz: float,
    sdc_path: str,
) -> str:
    """Generate SDC constraints by detecting the clock port from RTL.

    Uses a regex-based detector (fast, no LLM cost). Falls back to 'clk'
    if no clock port is found. Also creates a virtual clock for pure
    combinational modules.

    Returns the SDC file path.
    """
    period_ns = 1000.0 / target_clock_mhz
    clock_port = _detect_clock_port(rtl_source)

    if clock_port:
        sdc_content = (
            f"create_clock -name clk -period {period_ns} [get_ports {clock_port}]\n"
            f"set_input_delay -clock clk {period_ns * 0.2} [all_inputs]\n"
            f"set_output_delay -clock clk {period_ns * 0.2} [all_outputs]\n"
        )
    else:
        sdc_content = (
            f"create_clock -name vclk -period {period_ns}\n"
            f"set_input_delay -clock vclk {period_ns * 0.2} [all_inputs]\n"
            f"set_output_delay -clock vclk {period_ns * 0.2} [all_outputs]\n"
        )

    Path(sdc_path).write_text(sdc_content)
    return sdc_path


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

def synthesize_block(
    block: dict, rtl_path: str, target_clock_mhz: float = 50.0,
    attempt: int = 1,
) -> dict:
    """Run Yosys synthesis targeting Sky130."""
    block_name = block["name"]
    output_dir = PROJECT_ROOT / "syn" / "output" / block_name
    output_dir.mkdir(parents=True, exist_ok=True)

    liberty = str(LIBERTY_FILE)
    netlist_path = output_dir / f"{block_name}_netlist.v"
    report_path = output_dir / f"{block_name}_report.txt"

    script = f"""# Auto-generated synthesis script for {block_name}
read_verilog {rtl_path}
hierarchy -top {block_name}
proc
flatten
opt
synth -run begin:fine
memory_bram
memory_map
synth -run fine:
dfflibmap -liberty {liberty}
abc -liberty {liberty}
opt_clean
stat -liberty {liberty}
write_verilog -noattr {netlist_path}
"""
    script_path = output_dir / f"synth_{block_name}.ys"
    script_path.write_text(script)

    period_ns = 1000.0 / target_clock_mhz
    rtl_source = Path(rtl_path).read_text() if Path(rtl_path).exists() else ""
    clock_port = _detect_clock_port(rtl_source) if rtl_source else "clk"
    sdc_content = (
        f"create_clock -name clk -period {period_ns} [get_ports {clock_port}]\n"
        f"set_input_delay -clock clk {period_ns * 0.2} [all_inputs]\n"
        f"set_output_delay -clock clk {period_ns * 0.2} [all_outputs]\n"
    )
    sdc_path = output_dir / f"{block_name}.sdc"
    sdc_path.write_text(sdc_content)

    try:
        result = subprocess.run(
            ["yosys", "-s", str(script_path)],
            capture_output=True,
            text=True,
            timeout=1800,
        )

        gate_count = 0
        chip_area = 0.0
        for line in result.stdout.split("\n"):
            if "Number of cells:" in line:
                # Plain stat format: "   Number of cells:   178"
                try:
                    gate_count = int(line.split(":")[-1].strip())
                except ValueError:
                    pass
            else:
                stripped = line.strip()
                if stripped.endswith("cells") and not stripped.startswith("-"):
                    # Liberty stat format: "      178 1.73E+03 cells"
                    parts = stripped.split()
                    if len(parts) >= 3:
                        try:
                            gate_count = int(parts[0])
                        except ValueError:
                            pass
            if "Chip area for module" in line:
                # "   Chip area for module '\adder_16bit': 1727.907200"
                try:
                    chip_area = float(line.split(":")[-1].strip())
                except ValueError:
                    pass

        report_path.write_text(result.stdout[-10000:])

        log_path = _write_step_log(block_name, "synthesize", ["yosys", "-s", str(script_path)], result, attempt)

        return {
            "success": result.returncode == 0,
            "gate_count": gate_count,
            "chip_area_um2": chip_area,
            "netlist_path": str(netlist_path),
            "sdc_path": str(sdc_path),
            "log": result.stdout[-3000:] + "\n" + result.stderr[-1000:],
            "log_path": log_path,
        }
    except subprocess.TimeoutExpired:
        cmd = ["yosys", "-s", str(script_path)]
        log_path = _write_step_log_error(block_name, "synthesize", cmd, "Yosys synthesis timed out (30 min)", attempt)
        return {"success": False, "log": "Yosys synthesis timed out (30 min)", "log_path": log_path}
    except FileNotFoundError:
        cmd = ["yosys", "-s", str(script_path)]
        log_path = _write_step_log_error(block_name, "synthesize", cmd, "Yosys not installed", attempt)
        return {"success": False, "log": "Yosys not installed", "log_path": log_path}


# ---------------------------------------------------------------------------
# Lint Fixer (local LLM iteration)
# ---------------------------------------------------------------------------

async def fix_lint_errors(
    block_name: str, rtl_path: str, lint_log_path: str,
    callbacks: list = None,
) -> bool | None:
    """Call an LLM to fix Verilator lint errors in the RTL.

    Disk-first: the agent reads the RTL and lint log from disk, uses
    the Edit tool to fix in-place.  Returns True if the agent modified
    the file, None if it couldn't fix.
    """
    from orchestrator.langchain.agents.cursor_llm import DEFAULT_MODEL, ClaudeLLM

    prompt_file = Path(__file__).resolve().parent.parent / "langchain" / "prompts" / "lint_fixer.md"
    if prompt_file.exists():
        system_prompt = prompt_file.read_text()
    else:
        system_prompt = (
            "You are an expert Verilog lint fixer. Read the RTL file and "
            "lint error log, then use the Edit tool to fix the errors in-place."
        )

    user_message = (
        f"Block: {block_name}\n\n"
        f"## Working Files\n"
        f"- RTL file: {rtl_path}\n"
        f"- Lint log: {lint_log_path}\n"
        f"- Constraints: .socmate/blocks/{block_name}/constraints.json\n\n"
        f"Read the lint errors, then use the Edit tool to fix the RTL file "
        f"in-place. Do NOT rewrite the entire file -- make targeted fixes."
    )

    block_title = block_name.replace("_", " ").title()
    llm = ClaudeLLM(
        model=DEFAULT_MODEL,
        timeout=int(os.environ.get("SOCMATE_LINT_FIX_TIMEOUT", "600")),
    )

    try:
        await llm.call(
            system=system_prompt,
            prompt=user_message,
            run_name=f"Lint Fix [{block_title}]",
        )
        return True

    except Exception as e:
        log(f"  [LINT-FIX] LLM error: {e}", RED)
        return None


# ---------------------------------------------------------------------------
# Synthesis Fixer (local LLM iteration)
# ---------------------------------------------------------------------------

async def fix_synth_errors(
    block_name: str, rtl_path: str, synth_log_path: str,
    callbacks: list = None,
) -> bool | None:
    """Call an LLM to fix Yosys synthesis errors in the RTL.

    Disk-first: the agent reads the RTL and synth log from disk, uses
    the Edit tool to fix in-place.  Returns True if the agent modified
    the file, None if it couldn't fix.
    """
    from orchestrator.langchain.agents.cursor_llm import DEFAULT_MODEL, ClaudeLLM

    prompt_file = Path(__file__).resolve().parent.parent / "langchain" / "prompts" / "synth_fixer.md"
    if prompt_file.exists():
        system_prompt = prompt_file.read_text()
    else:
        system_prompt = (
            "You are an expert synthesis engineer. Read the RTL file and "
            "synthesis log, then use the Edit tool to fix the errors in-place."
        )

    user_message = (
        f"Block: {block_name}\n\n"
        f"## Working Files\n"
        f"- RTL file: {rtl_path}\n"
        f"- Synthesis log: {synth_log_path}\n"
        f"- Constraints: .socmate/blocks/{block_name}/constraints.json\n\n"
        f"Read the synthesis errors, then use the Edit tool to fix the RTL file "
        f"in-place. Do NOT rewrite the entire file -- make targeted fixes."
    )

    block_title = block_name.replace("_", " ").title()
    llm = ClaudeLLM(
        model=DEFAULT_MODEL,
        timeout=int(os.environ.get("SOCMATE_SYNTH_FIX_TIMEOUT", "600")),
    )

    try:
        await llm.call(
            system=system_prompt,
            prompt=user_message,
            run_name=f"Synth Fix [{block_title}]",
        )
        return True

    except Exception as e:
        log(f"  [SYNTH-FIX] LLM error: {e}", RED)
        return None


# ---------------------------------------------------------------------------
# Testbench Fixer (local LLM iteration for sim failures)
# ---------------------------------------------------------------------------

async def fix_testbench_errors(
    block_name: str, rtl_path: str, tb_path: str, sim_log_path: str,
    callbacks: list = None,
) -> bool | None:
    """Call an LLM to fix simulation errors by editing the testbench.

    Disk-first: the agent reads the testbench, RTL, sim log, uArch spec,
    and DV rules from disk, uses the Edit tool to fix in-place.
    Returns True if the agent modified the file, None if it couldn't fix.
    """
    from orchestrator.langchain.agents.cursor_llm import DEFAULT_MODEL, ClaudeLLM

    system_prompt = (
        "You are an expert verification engineer. A cocotb testbench is "
        "failing during simulation. Read the testbench, RTL, simulation log, "
        "and uArch spec, then fix the testbench in-place using the Edit tool.\n\n"
        "Common issues:\n"
        "- Wrong port/signal names (check RTL module ports)\n"
        "- Import errors (use the <block>_model wrapper, not direct imports)\n"
        "- Timer(0) usage (use RisingEdge/FallingEdge instead)\n"
        "- Wrong timing assumptions (check pipeline latency in uArch spec)\n"
        "- Golden model mismatches (check algorithm implementation)\n"
        "- Type errors (cast numpy types to int before DUT assignment)\n"
        "- Cocotb API issues (use unit= not units=, start_soon not start_fork)\n\n"
        "Make targeted fixes. Do NOT rewrite the entire testbench unless "
        "the structure is fundamentally broken."
    )

    user_message = (
        f"Block: {block_name}\n\n"
        f"## Working Files\n"
        f"- Testbench (fix this): {tb_path}\n"
        f"- RTL Verilog: {rtl_path}\n"
        f"- Simulation log: {sim_log_path}\n"
        f"- uArch Spec: arch/uarch_specs/{block_name}.md\n"
        f"- Constraints: .socmate/blocks/{block_name}/constraints.json\n"
        f"- DV Rules: arch/DV_RULES.md\n\n"
        f"Read the simulation log to understand the failure, then read the "
        f"testbench and RTL. Fix the testbench in-place using the Edit tool."
    )

    block_title = block_name.replace("_", " ").title()
    # 600s default; bump via SOCMATE_TB_FIX_TIMEOUT for complex blocks
    # whose TB rewrite genuinely needs more than 10 minutes. The previous
    # 300s default consistently timed out for non-trivial blocks (mcu3
    # 3-stage CPU, multi-stage pipelines) and produced partial fixes that
    # didn't address the root cause.
    llm = ClaudeLLM(
        model=DEFAULT_MODEL,
        timeout=int(os.environ.get("SOCMATE_TB_FIX_TIMEOUT", "600")),
    )

    try:
        await llm.call(
            system=system_prompt,
            prompt=user_message,
            run_name=f"TB Fix [{block_title}]",
        )
        return True
    except Exception as e:
        log(f"  [TB-FIX] LLM error: {e}", RED)
        return None


# ---------------------------------------------------------------------------
# Debug Agent
# ---------------------------------------------------------------------------

async def diagnose_failure(
    block_name: str,
    phase: str = "sim",
    project_root: str = "",
    callbacks: list = None,
) -> dict:
    """Run DebugAgent to analyze failure -- disk-first, agent reads all files."""
    from orchestrator.langchain.agents.debug_agent import DebugAgent
    from orchestrator.langchain.agents.cursor_llm import DEFAULT_MODEL

    agent = DebugAgent(model=DEFAULT_MODEL, temperature=0.1)
    return await agent.analyze(
        block_name=block_name,
        phase=phase,
        project_root=project_root or str(PROJECT_ROOT),
        mode="debug",
        callbacks=callbacks,
    )
