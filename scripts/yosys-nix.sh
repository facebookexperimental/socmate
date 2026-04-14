#!/usr/bin/env bash
# yosys-nix.sh -- Run Yosys inside a Nix shell
#
# Usage:
#   scripts/yosys-nix.sh -V
#   scripts/yosys-nix.sh -s synth_script.ys
#   scripts/yosys-nix.sh -p "read_verilog foo.v; synth_sky130"

set -euo pipefail

exec nix shell "nixpkgs#yosys" --command yosys "$@"
