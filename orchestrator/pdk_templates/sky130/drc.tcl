# Sky130 DRC template (Magic VLSI)
# Runs full design-rule check on the flattened layout.
#
# Variables to substitute: TECH_LEF, CELL_LEF, CELL_GDS, DEF_FILE,
#                          BLOCK_NAME, OUT_DIR

lef read $TECH_LEF
lef read $CELL_LEF
gds read $CELL_GDS
def read $DEF_FILE
load $BLOCK_NAME

# Flatten for DRC
flatten ${BLOCK_NAME}_flat
load ${BLOCK_NAME}_flat
select top cell
drc catchup
drc count
set drc_count [drc listall count]

set drc_rpt [open "$OUT_DIR/magic_drc.rpt" w]
puts $drc_rpt "Design: $BLOCK_NAME"
puts $drc_rpt "DRC count: $drc_count"
set drc_result [drc listall why]
puts $drc_rpt $drc_result
close $drc_rpt

puts "DRC violations: $drc_count"
puts "DRC report: $OUT_DIR/magic_drc.rpt"
quit -noprompt
