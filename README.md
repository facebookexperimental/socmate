# socmate

An AI-orchestrated ASIC design pipeline. socmate uses LangGraph to drive the full RTL-to-GDSII flow: architecture specification, RTL generation, verification, synthesis, and physical design -- all orchestrated by Claude as the LLM backbone.

## What It Does

Given a set of requirements, socmate:

1. **Architecture** -- Generates a Product Requirements Document (PRD), block diagram, memory map, clock tree, and register specification via a multi-step LangGraph state machine
2. **RTL Generation** -- An LLM agent converts specifications into synthesizable Verilog-2005
3. **Verification** -- Another LLM agent generates cocotb testbenches; Verilator lints and simulates
4. **Synthesis** -- Yosys synthesizes each block to a gate-level netlist targeting the SkyWater Sky130 130nm PDK
5. **Backend** -- OpenROAD/Magic/netgen handle place-and-route, DRC, and LVS
6. **Diagnosis** -- On failure at any step, a debug agent analyzes the root cause and retries with corrective constraints

The pipeline is interactive via an MCP server that integrates with Claude Code, or can run headlessly in CI mode.

## Prerequisites

| Tool | Version | Purpose | Install |
|------|---------|---------|---------|
| Python | >= 3.11 | Runtime | `brew install python@3.11` |
| Claude Code CLI | latest | LLM backend | `npm install -g @anthropic-ai/claude-code` |
| Yosys | >= 0.40 | Synthesis | `brew install yosys` |
| Verilator | >= 5.0 | Lint / simulation | `brew install verilator` |

Optional (gracefully skipped if missing):

| Tool | Purpose |
|------|---------|
| OpenSTA | Static timing analysis |
| OpenROAD | Place & route |
| Magic | DRC |
| netgen | LVS |
| KLayout | GDS viewer |

### SkyWater Sky130 PDK

```bash
pip install volare
volare enable --pdk sky130 --pdk-root .pdk
```

## Quick Start

### Option A -- Docker / RunPod (recommended for first-time users)

The repo ships a `Dockerfile` that bundles the full EDA toolchain
(Yosys, OpenROAD, Magic, netgen, KLayout, Sky130 PDK, Verilator,
cocotb) plus the orchestrator and the Claude CLI. No Nix or local
EDA install needed.

```bash
git clone https://github.com/facebookresearch/socmate.git
cd socmate
docker build -t socmate:latest .

docker run --rm -it \
    -e ANTHROPIC_API_KEY=sk-ant-... \
    -e SOCMATE_MODE=shell \
    -v "$(pwd)/.socmate:/socmate/.socmate" \
    socmate:latest
# inside the container:
make pipeline
```

For a hosted run, see [docs/RUNPOD.md](docs/RUNPOD.md) for a
ready-to-paste pod template.

### Option B -- Local install (Nix-based backend)

```bash
git clone https://github.com/facebookresearch/socmate.git
cd socmate

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -e orchestrator/

cp .env.example .env  # then edit and add ANTHROPIC_API_KEY

# Optional: pin a non-default model without code edits
# export SOCMATE_MODEL=opus-4.5

# Start the MCP server (for interactive use with Claude Code)
make mcp

# Or run the pipeline headlessly
make pipeline
```

The local path uses `nix shell "nixpkgs#openroad"` etc. for the backend
EDA tools (see `scripts/*-nix.sh`), so Nix with flakes enabled must be
on `$PATH` for any post-synthesis step. The container image avoids
this entirely.

If you have Nix with flakes already enabled, the cleanest local setup
is `nix develop` -- the repo's `flake.nix` pins every EDA tool plus
Verilator and Node/Claude CLI to a single nixpkgs commit, drops them
on `$PATH`, and bypasses the per-call `nix shell` re-entry through the
`SOCMATE_BACKEND_*` env vars:

```bash
nix develop
# then, inside the dev shell:
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt && pip install -e orchestrator/
make pipeline
```

## Architecture

The system is built around three LangGraph state machines:

```
Phase 1: ARCHITECTURE        Phase 2: RTL PIPELINE        Phase 3: BACKEND
------------------------    ------------------------     -----------------
User Requirements            Per-block loop:              Post-synthesis:
  |                            uArch Spec                   Place & Route
  v                            RTL + Lint                   DRC
PRD (sizing questions)         Testbench + Sim              LVS
  |                            Synthesis                    Timing Sign-off
  v                            Diagnose / Retry
Block Diagram
  |
  v
Memory Map -> Clock Tree -> Register Spec -> Constraint Check -> OK2DEV Gate
```

## Project Structure

```
socmate/
  orchestrator/           # Core pipeline engine
    architecture/         #   Architecture phase (PRD, block diagram, constraints)
    langchain/            #   LLM agents (RTL gen, testbench, debug, timing)
    langgraph/            #   State machines (architecture, pipeline, backend, tapeout)
    pdk/                  #   PDK configuration
    pdk_templates/        #   EDA tool templates (Yosys, Magic, netgen)
    telemetry/            #   OpenTelemetry tracing
    mcp_server.py         #   MCP server for Claude Code integration
    config.yaml           #   Pipeline configuration
    tests/                #   Test suite
  scripts/                # Toolchain installer, Nix wrappers
  run_pipeline.py         # CLI entry point
  Makefile                # Build targets
  requirements.txt        # Python dependencies
```

## Usage

### Interactive (Claude Code + MCP)

The MCP server exposes tools for interactive pipeline control:

- `start_architecture(requirements, target_clock_mhz)` -- Begin architecture phase
- `start_pipeline(max_attempts, target_clock_mhz)` -- Begin RTL pipeline
- `get_pipeline_state()` -- Monitor progress
- `resume_pipeline(action, ...)` -- Handle interrupts
- `start_backend()` -- Begin physical design

### Headless (CI)

```bash
python run_pipeline.py
```

Interrupts are auto-resolved: uArch specs are auto-approved, failures retry until max attempts, then skip.

## Testing

```bash
# Run orchestrator tests
source venv/bin/activate
pytest orchestrator/tests/ -v

# Skip tests requiring live LLM
pytest orchestrator/tests/ -v -m "not live_llm"

# Skip tests requiring Nix/EDA tools
pytest orchestrator/tests/ -v -m "not requires_nix and not e2e"
```

## License

MIT License. See [LICENSE](LICENSE) for details.
