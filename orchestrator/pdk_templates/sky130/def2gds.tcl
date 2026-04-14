# Sky130 DEF-to-GDS conversion template (Magic VLSI)
# Usage: magic -dnull -noconsole -rcfile $MAGIC_RC < def2gds.tcl
#
# Variables to substitute: TECH_LEF, CELL_LEF, CELL_GDS, DEF_FILE,
#                          BLOCK_NAME, OUT_DIR

lef read $TECH_LEF
lef read $CELL_LEF
gds read $CELL_GDS
def read $DEF_FILE
load $BLOCK_NAME
select top cell
gds write "$OUT_DIR/$BLOCK_NAME.gds"
puts "GDS written: $OUT_DIR/$BLOCK_NAME.gds"
quit -noprompt
