# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# Sky130 SPICE extraction template (Magic VLSI)
# Performs hierarchical extraction for LVS comparison.
#
# Variables to substitute: TECH_LEF, CELL_LEF, CELL_GDS, DEF_FILE,
#                          BLOCK_NAME, OUT_DIR

lef read $TECH_LEF
lef read $CELL_LEF
gds read $CELL_GDS
def read $DEF_FILE
load $BLOCK_NAME
select top cell
extract all
ext2spice lvs
ext2spice -o "$OUT_DIR/$BLOCK_NAME.spice"
puts "SPICE written: $OUT_DIR/$BLOCK_NAME.spice"
quit -noprompt
