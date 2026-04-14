#!/usr/bin/env bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
# yosys-nix.sh -- Run Yosys inside a Nix shell
#
# Usage:
#   scripts/yosys-nix.sh -V
#   scripts/yosys-nix.sh -s synth_script.ys
#   scripts/yosys-nix.sh -p "read_verilog foo.v; synth_sky130"

set -euo pipefail

exec nix shell "nixpkgs#yosys" --command yosys "$@"
