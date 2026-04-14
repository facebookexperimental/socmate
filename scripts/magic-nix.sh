#!/usr/bin/env bash
# magic-nix.sh -- Run Magic VLSI inside a Nix shell
#
# Usage:
#   scripts/magic-nix.sh -dnull -noconsole -rcfile <magicrc> script.tcl
#   PDK_ROOT=.pdk scripts/magic-nix.sh -dnull -noconsole script.tcl
#
# Magic does not have a -version flag; use -dnull -noconsole to run batch.

set -euo pipefail

exec nix shell "nixpkgs#magic-vlsi" --command magic "$@"
