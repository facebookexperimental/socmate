#!/usr/bin/env bash
# openroad-nix.sh -- Run OpenROAD inside a Nix shell
#
# Usage:
#   scripts/openroad-nix.sh -version
#   scripts/openroad-nix.sh -no_init script.tcl
#   scripts/openroad-nix.sh < script.tcl
#
# The socmate backend graph calls this instead of a bare "openroad" binary.
# Configure the path in orchestrator/config.yaml under backend.openroad_binary.

set -euo pipefail

exec nix shell "nixpkgs#openroad" --command openroad -exit "$@"
