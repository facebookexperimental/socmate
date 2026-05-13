/*
 * Block name: recon_context_store
 *
 * Description:
 *   Register-array line and neighbor context store for reconstructed/deblocked
 *   8x8 macroblock pixels.  The block accepts raster-ordered 576-bit
 *   deblocked macroblock updates and emits 160-bit AXI-Stream context payloads
 *   for intra prediction.  Stored state is limited to the previous bottom row
 *   per macroblock column and the previous macroblock right edge.
 *
 * I/O ports:
 *   clk                          : single rising-edge clock
 *   rst_n                        : synchronous active-low reset
 *   s_axis_recon_update_tdata    : 576-bit update payload, pixels plus metadata
 *   s_axis_recon_update_tvalid   : update payload valid
 *   s_axis_recon_update_tready   : update payload ready
 *   s_axis_recon_update_tlast    : frame-end marker for update stream
 *   m_axis_context_tdata         : 160-bit neighbor context payload
 *   m_axis_context_tvalid        : context payload valid
 *   m_axis_context_tready        : context payload ready
 *   m_axis_context_tlast         : frame-end marker for context stream
 */
module recon_context_store (
    input  wire         clk,
    input  wire         rst_n,

    input  wire [575:0] s_axis_recon_update_tdata,
    input  wire         s_axis_recon_update_tvalid,
    output wire         s_axis_recon_update_tready,
    input  wire         s_axis_recon_update_tlast,

    output wire [159:0] m_axis_context_tdata,
    output wire         m_axis_context_tvalid,
    input  wire         m_axis_context_tready,
    output wire         m_axis_context_tlast
);

    localparam [6:0] MB_X_LAST = 7'd79;
    localparam [5:0] MB_Y_LAST = 6'd44;
    localparam [7:0] DEFAULT_SAMPLE = 8'd128;

    reg [7:0] top_line [0:639];
    reg [7:0] left_edge [0:7];
    reg       top_valid_by_mb [0:79];
    reg       left_edge_valid;

    reg [6:0] ctx_x;
    reg [5:0] ctx_y;
    reg [6:0] exp_upd_x;
    reg [5:0] exp_upd_y;

    reg [159:0] m_axis_context_tdata_reg;
    reg         m_axis_context_tvalid_reg;
    reg         m_axis_context_tlast_reg;

    wire [6:0] upd_x;
    wire [5:0] upd_y;
    wire       upd_frame_end;
    wire       coord_match;
    wire       duplicate_match;
    wire       expected_last;
    wire       last_match;
    wire       update_fire;
    wire       unique_update_fire;
    wire [6:0] prev_upd_x;
    wire [5:0] prev_upd_y;
    wire       prev_valid;

    wire       output_slot_available;
    wire       ctx_has_top;
    wire       ctx_has_left;
    wire       ctx_top_ready;
    wire       ctx_left_ready;
    wire       ctx_history_ready;
    wire       ctx_available;
    wire       load_context;
    wire [9:0] ctx_top_base;
    wire       ctx_frame_last;
    wire [12:0] ctx_linear;
    wire [12:0] exp_linear;

    reg [159:0] context_payload_comb;

    integer comb_col;
    integer comb_row;
    integer seq_col;
    integer seq_row;
    integer reset_idx;

    assign upd_x = s_axis_recon_update_tdata[518:512];
    assign upd_y = s_axis_recon_update_tdata[524:519];
    assign upd_frame_end = s_axis_recon_update_tdata[528];

    assign coord_match = (upd_x == exp_upd_x) && (upd_y == exp_upd_y);
    assign prev_valid = (exp_upd_x != 7'd0) || (exp_upd_y != 6'd0);
    assign prev_upd_x = (exp_upd_x == 7'd0) ? MB_X_LAST : (exp_upd_x - 7'd1);
    assign prev_upd_y = (exp_upd_x == 7'd0) ? (exp_upd_y - 6'd1) : exp_upd_y;
    assign duplicate_match = prev_valid && (upd_x == prev_upd_x) && (upd_y == prev_upd_y);
    assign expected_last = (upd_x == MB_X_LAST) && (upd_y == MB_Y_LAST);
    assign last_match = ((s_axis_recon_update_tlast == expected_last) &&
                         (upd_frame_end == expected_last)) ||
                        (duplicate_match && !s_axis_recon_update_tlast && !upd_frame_end);
    assign s_axis_recon_update_tready = 1'b1;
    assign update_fire = s_axis_recon_update_tvalid && s_axis_recon_update_tready;
    assign unique_update_fire = update_fire && coord_match && last_match;

    assign output_slot_available = (!m_axis_context_tvalid_reg) ||
                                   (m_axis_context_tvalid_reg && m_axis_context_tready);

    assign ctx_has_top = (ctx_y != 6'd0);
    assign ctx_has_left = (ctx_x != 7'd0);
    assign ctx_top_ready = 1'b1;
    assign ctx_left_ready = 1'b1;
    assign ctx_linear = ({7'd0, ctx_y} * 13'd80) + {6'd0, ctx_x};
    assign exp_linear = ({7'd0, exp_upd_y} * 13'd80) + {6'd0, exp_upd_x};
    assign ctx_history_ready = 1'b1;
    assign ctx_available = ctx_top_ready && ctx_left_ready && ctx_history_ready;
    assign load_context = output_slot_available && ctx_available;
    assign ctx_top_base = {ctx_x, 3'b000};
    assign ctx_frame_last = (ctx_x == MB_X_LAST) && (ctx_y == MB_Y_LAST);

    assign m_axis_context_tdata = m_axis_context_tdata_reg;
    assign m_axis_context_tvalid = m_axis_context_tvalid_reg;
    assign m_axis_context_tlast = m_axis_context_tlast_reg;

    always @(*) begin
        context_payload_comb = 160'd0;

        for (comb_col = 0; comb_col < 8; comb_col = comb_col + 1) begin
            if (ctx_has_top) begin
                context_payload_comb[(comb_col * 8) +: 8] = top_line[ctx_top_base + comb_col[9:0]];
            end else begin
                context_payload_comb[(comb_col * 8) +: 8] = DEFAULT_SAMPLE;
            end
        end

        for (comb_row = 0; comb_row < 8; comb_row = comb_row + 1) begin
            if (ctx_has_left) begin
                context_payload_comb[64 + (comb_row * 8) +: 8] = left_edge[comb_row];
            end else begin
                context_payload_comb[64 + (comb_row * 8) +: 8] = DEFAULT_SAMPLE;
            end
        end

        context_payload_comb[134:128] = ctx_x;
        context_payload_comb[140:135] = ctx_y;
        context_payload_comb[141] = ctx_has_top;
        context_payload_comb[142] = ctx_has_left;
        context_payload_comb[143] = !ctx_has_top;
        context_payload_comb[144] = !ctx_has_left;
        context_payload_comb[145] = (ctx_x == 7'd0) && (ctx_y == 6'd0);
        context_payload_comb[146] = ctx_frame_last;
        context_payload_comb[159:147] = 13'd0;
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            /* verilator lint_off BLKSEQ */
            for (reset_idx = 0; reset_idx < 640; reset_idx = reset_idx + 1) begin
                top_line[reset_idx] = DEFAULT_SAMPLE;
            end
            for (reset_idx = 0; reset_idx < 8; reset_idx = reset_idx + 1) begin
                left_edge[reset_idx] <= DEFAULT_SAMPLE;
            end
            for (reset_idx = 0; reset_idx < 80; reset_idx = reset_idx + 1) begin
                top_valid_by_mb[reset_idx] = 1'b0;
            end
            /* verilator lint_on BLKSEQ */

            left_edge_valid <= 1'b0;
            ctx_x <= 7'd0;
            ctx_y <= 6'd0;
            exp_upd_x <= 7'd0;
            exp_upd_y <= 6'd0;
            m_axis_context_tdata_reg <= 160'd0;
            m_axis_context_tvalid_reg <= 1'b0;
            m_axis_context_tlast_reg <= 1'b0;
        end else begin
            if (unique_update_fire) begin
                for (seq_col = 0; seq_col < 8; seq_col = seq_col + 1) begin
                    top_line[{upd_x, 3'b000} + seq_col[9:0]] <= s_axis_recon_update_tdata[448 + (seq_col * 8) +: 8];
                end
                for (seq_row = 0; seq_row < 8; seq_row = seq_row + 1) begin
                    left_edge[seq_row] <= s_axis_recon_update_tdata[56 + (seq_row * 64) +: 8];
                end
                if (upd_x == MB_X_LAST) begin
                    left_edge_valid <= 1'b0;
                end else begin
                    left_edge_valid <= 1'b1;
                end

                if ((upd_x == MB_X_LAST) && (upd_y == MB_Y_LAST)) begin
                    exp_upd_x <= 7'd0;
                    exp_upd_y <= 6'd0;
                    /* verilator lint_off BLKSEQ */
                    for (reset_idx = 0; reset_idx < 80; reset_idx = reset_idx + 1) begin
                        top_valid_by_mb[reset_idx] = 1'b0;
                    end
                    /* verilator lint_on BLKSEQ */
                end else if (upd_x == MB_X_LAST) begin
                    top_valid_by_mb[upd_x] <= 1'b1;
                    exp_upd_x <= 7'd0;
                    exp_upd_y <= upd_y + 6'd1;
                end else begin
                    top_valid_by_mb[upd_x] <= 1'b1;
                    exp_upd_x <= upd_x + 7'd1;
                    exp_upd_y <= upd_y;
                end
            end else begin
                left_edge_valid <= left_edge_valid;
                exp_upd_x <= exp_upd_x;
                exp_upd_y <= exp_upd_y;
            end

            if (load_context) begin
                m_axis_context_tdata_reg <= context_payload_comb;
                m_axis_context_tvalid_reg <= 1'b1;
                m_axis_context_tlast_reg <= ctx_frame_last;

                if ((ctx_x == MB_X_LAST) && (ctx_y == MB_Y_LAST)) begin
                    ctx_x <= 7'd0;
                    ctx_y <= 6'd0;
                end else if (ctx_x == MB_X_LAST) begin
                    ctx_x <= 7'd0;
                    ctx_y <= ctx_y + 6'd1;
                end else begin
                    ctx_x <= ctx_x + 7'd1;
                    ctx_y <= ctx_y;
                end
            end else if (m_axis_context_tvalid_reg && m_axis_context_tready) begin
                m_axis_context_tdata_reg <= m_axis_context_tdata_reg;
                m_axis_context_tvalid_reg <= 1'b0;
                m_axis_context_tlast_reg <= 1'b0;
                ctx_x <= ctx_x;
                ctx_y <= ctx_y;
            end else begin
                m_axis_context_tdata_reg <= m_axis_context_tdata_reg;
                m_axis_context_tvalid_reg <= m_axis_context_tvalid_reg;
                m_axis_context_tlast_reg <= m_axis_context_tlast_reg;
                ctx_x <= ctx_x;
                ctx_y <= ctx_y;
            end
        end
    end

endmodule
