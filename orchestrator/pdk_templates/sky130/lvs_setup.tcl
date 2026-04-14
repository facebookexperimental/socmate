# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# Sky130 LVS setup template (Netgen)
# Configures Netgen for Sky130 HD standard cell LVS comparison.
#
# This file is sourced by Netgen's -batch lvs command.
# The actual NETGEN_SETUP path from the PDK is used at runtime;
# this template documents the expected configuration.

# Standard cell library matching
permute default
property default
property parallel none

# Ignore fill and tap cells in LVS
ignore class sky130_fd_sc_hd__fill_*
ignore class sky130_fd_sc_hd__tapvpwrvgnd_*
ignore class sky130_fd_sc_hd__decap_*

# Power net handling: VPWR/VGND pin topology differs between
# extracted SPICE (per-cell connections) and source Verilog (global nets).
# Permute power pins and ignore their properties to prevent false LVS
# mismatches (device_delta/net_delta on power rails).
permute pin VPWR
permute pin VGND
permute pin VPB
permute pin VNB
property VPWR delete
property VGND delete
property VPB delete
property VNB delete
