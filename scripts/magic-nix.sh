#!/usr/bin/env bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
# magic-nix.sh -- Run Magic VLSI inside a Nix shell
#
# Usage:
#   scripts/magic-nix.sh -dnull -noconsole -rcfile <magicrc> script.tcl
#   PDK_ROOT=.pdk scripts/magic-nix.sh -dnull -noconsole script.tcl
#
# Magic does not have a -version flag; use -dnull -noconsole to run batch.

set -euo pipefail

exec nix shell "nixpkgs#magic-vlsi" --command magic "$@"
