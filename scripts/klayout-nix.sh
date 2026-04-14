#!/usr/bin/env bash
# klayout-nix.sh -- Run KLayout inside a Nix shell
#
# Usage:
#   scripts/klayout-nix.sh -b -r script.py
#   scripts/klayout-nix.sh -b -rd script.drc ...
#
# KLayout is needed for mpw_precheck DRC/density checks (native mode,
# replacing the Docker-based invocation).

set -euo pipefail

exec nix shell "nixpkgs#klayout" --command klayout "$@"
