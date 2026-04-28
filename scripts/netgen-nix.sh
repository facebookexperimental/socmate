#!/usr/bin/env bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
# netgen-nix.sh -- Run Netgen VLSI (LVS) inside a Nix shell
#
# Usage:
#   scripts/netgen-nix.sh -batch lvs "layout.spice top" "schematic.v top" setup.tcl report.txt
#
# Netgen 1.5.316 via nixpkgs.

set -euo pipefail

exec nix shell "nixpkgs#netgen-vlsi" --command netgen "$@"
