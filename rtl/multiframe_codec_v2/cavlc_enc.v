// =============================================================================
// Block name: cavlc_enc
// Description:
//   H.264-style local CAVLC coefficient encoder.  The block consumes RLE
//   symbols from zigzag_rle_select, reconstructs one 4x4 or 8x8 coding block,
//   appends the macroblock/header mode bits used by codec_golden.py, and emits
//   MSB-first variable-length bit groups to block_packer.
//
// I/O ports:
//   clk                         : input clock, single clock domain
//   rst_n                       : input synchronous active-low reset
//   s_axis_rle_symbols_tdata    : input  [511:0] RLE symbol chunk payload
//   s_axis_rle_symbols_tuser    : input  [47:0] metadata sideband
//   s_axis_rle_symbols_tvalid   : input AXI-Stream valid
//   s_axis_rle_symbols_tready   : output AXI-Stream ready
//   s_axis_rle_symbols_tlast    : input frame-final chunk marker
//   m_axis_bits_tdata           : output [63:0] MSB-first bit group
//   m_axis_bits_tuser           : output [7:0], [6:0]=valid bit count,
//                                 [7]=frame-start group marker
//   m_axis_bits_tvalid          : output registered AXI-Stream valid
//   m_axis_bits_tready          : input AXI-Stream ready
//   m_axis_bits_tlast           : output final frame bit-group marker
//
// Fixed-point notes:
//   Stored coefficient levels are signed Q16.0 integers.  All entropy fields
//   are integer code numbers; no floating-point arithmetic is used.
// =============================================================================

module cavlc_enc (
    input  wire         clk,
    input  wire         rst_n,

    input  wire [511:0] s_axis_rle_symbols_tdata,
    input  wire [47:0]  s_axis_rle_symbols_tuser,
    input  wire         s_axis_rle_symbols_tvalid,
    output wire         s_axis_rle_symbols_tready,
    input  wire         s_axis_rle_symbols_tlast,

    output wire [63:0]  m_axis_bits_tdata,
    output wire [7:0]   m_axis_bits_tuser,
    output wire         m_axis_bits_tvalid,
    input  wire         m_axis_bits_tready,
    output wire         m_axis_bits_tlast
);

    localparam [2:0] STATE_IDLE         = 3'b000;
    localparam [2:0] STATE_COLLECT      = 3'b001;
    localparam [2:0] STATE_BUILD_TOKEN  = 3'b010;
    localparam [2:0] STATE_BUILD_LEVELS = 3'b011;
    localparam [2:0] STATE_BUILD_ZEROS  = 3'b100;
    localparam [2:0] STATE_EMIT         = 3'b101;
    localparam [2:0] STATE_ERROR        = 3'b110;

    reg  [2:0]         state_q;
    reg  [5:0]         run_store_q [0:63];
    reg  signed [15:0] level_store_q [0:63];
    reg  [63:0]        valid_store_q;
    reg  [5:0]         total_coeff_q;
    reg  [1:0]         trailing_ones_q;
    reg  [5:0]         total_zeros_q;
    reg  [6:0]         max_coeff_q;
    reg  [6:0]         symbol_seen_count_q;
    reg  [6:0]         run_sum_q;
    reg                eob_seen_q;
    reg  [47:0]        meta_q;
    reg                last_q;
    reg                use_8x8_q;
    reg  [1:0]         block_idx_q;
    reg                mb_start_q;
    reg                mb_end_q;
    reg                frame_start_q;
    reg                frame_end_q;
    reg  [2303:0]      codebuf_q;
    reg  [11:0]        code_len_q;
    reg  [11:0]        emit_offset_q;
    reg  [63:0]        out_data_q;
    reg  [7:0]         out_user_q;
    reg                out_last_q;
    reg                out_valid_q;
    reg                protocol_error_q;
    reg                code_overflow_q;

    reg  [2303:0]      build_buf_v;
    reg  [11:0]        build_len_v;
    reg  [63:0]        group_data_v;
    reg  [6:0]         group_len_v;
    reg  [11:0]        remaining_v;
    reg  [11:0]        abs_index_v;
    reg  [6:0]         store_index_v;
    reg  [5:0]         sym_run_v;
    reg  signed [15:0] sym_level_v;
    reg  [4:0]         symbol_count_v;
    reg  [6:0]         symbol_base_v;
    reg  [5:0]         total_coeff_in_v;
    reg  [1:0]         trailing_ones_in_v;
    reg  [5:0]         total_zeros_in_v;
    reg                block_start_v;
    reg                block_end_v;
    reg                sym_valid_v;
    reg                sym_eob_v;
    reg  [8:0]         token_v;
    reg  [16:0]        codenum_v;
    reg  [1:0]         mode_v;
    reg  [6:0]         reverse_rank_v;
    reg  [7:0]         sum8_v;
    reg  [6:0]         seen_next_v;
    reg  [6:0]         run_sum_next_v;
    reg  [63:0]        valid_next_v;
    reg                eob_next_v;
    reg                final_group_v;
    reg                in_fire_v;
    reg                out_fire_v;

    integer i;
    integer j;

    assign s_axis_rle_symbols_tready = rst_n &
                                       (~protocol_error_q) &
                                       ((state_q == STATE_IDLE) |
                                        (state_q == STATE_COLLECT));

    assign m_axis_bits_tdata  = out_data_q;
    assign m_axis_bits_tuser  = out_user_q;
    assign m_axis_bits_tvalid = out_valid_q;
    assign m_axis_bits_tlast  = out_last_q;

    task append_bit;
        inout [2303:0] buf_t;
        inout [11:0]   len_t;
        input          bit_t;
        begin
            if (len_t < 12'd2304) begin
                buf_t[12'd2303 - len_t] = bit_t;
                len_t = len_t + 12'd1;
            end else begin
                len_t = len_t;
            end
        end
    endtask

    task append_ue;
        inout [2303:0] buf_t;
        inout [11:0]   len_t;
        input [16:0]   value_t;
        reg   [17:0]   val_t;
        reg   [5:0]    bit_len_t;
        reg   [5:0]    leading_zero_count_t;
        integer        k;
        begin
            val_t = {1'b0, value_t} + 18'd1;
            bit_len_t = 6'd1;
            for (k = 17; k >= 0; k = k - 1) begin
                if ((val_t[k] == 1'b1) && (bit_len_t == 6'd1)) begin
                    bit_len_t = k[5:0] + 6'd1;
                end else begin
                    bit_len_t = bit_len_t;
                end
            end
            leading_zero_count_t = bit_len_t - 6'd1;
            for (k = 0; k < 17; k = k + 1) begin
                if (k[5:0] < leading_zero_count_t) begin
                    append_bit(buf_t, len_t, 1'b0);
                end else begin
                    len_t = len_t;
                end
            end
            for (k = 17; k >= 0; k = k - 1) begin
                if (k[5:0] < bit_len_t) begin
                    append_bit(buf_t, len_t, val_t[k]);
                end else begin
                    len_t = len_t;
                end
            end
        end
    endtask

    task append_se;
        inout [2303:0]      buf_t;
        inout [11:0]        len_t;
        input signed [15:0] level_t;
        reg   signed [17:0] level_ext_t;
        reg   signed [17:0] neg_ext_t;
        reg   [16:0]        codenum_t;
        begin
            level_ext_t = {{2{level_t[15]}}, level_t};
            if (level_t == 16'sd0) begin
                codenum_t = 17'd0;
            end else if (level_t[15] == 1'b0) begin
                codenum_t = (({1'b0, level_t} << 1) - 17'd1) & 17'h1ffff;
            end else begin
                neg_ext_t = -level_ext_t;
                codenum_t = (neg_ext_t[16:0] << 1) & 17'h1ffff;
            end
            append_ue(buf_t, len_t, codenum_t);
        end
    endtask

    task append_mode2;
        inout [2303:0] buf_t;
        inout [11:0]   len_t;
        input [1:0]    mode_t;
        begin
            append_bit(buf_t, len_t, mode_t[1]);
            append_bit(buf_t, len_t, mode_t[0]);
        end
    endtask

    task make_group;
        input [2303:0] buf_t;
        input [11:0]   len_t;
        input [11:0]   off_t;
        output [63:0]  data_t;
        output [6:0]   glen_t;
        output         final_t;
        reg   [11:0]   rem_t;
        reg   [11:0]   src_t;
        integer        k;
        begin
            data_t = 64'd0;
            rem_t = len_t - off_t;
            if (rem_t >= 12'd64) begin
                glen_t = 7'd64;
            end else begin
                glen_t = rem_t[6:0];
            end
            for (k = 0; k < 64; k = k + 1) begin
                if (k[6:0] < glen_t) begin
                    src_t = off_t + k[11:0];
                    data_t[6'd63 - k[5:0]] = buf_t[12'd2303 - src_t];
                end else begin
                    data_t[6'd63 - k[5:0]] = 1'b0;
                end
            end
            final_t = (rem_t <= 12'd64) ? 1'b1 : 1'b0;
        end
    endtask

    always @(posedge clk) begin
        if (!rst_n) begin
            state_q <= STATE_IDLE;
            valid_store_q <= 64'd0;
            total_coeff_q <= 6'd0;
            trailing_ones_q <= 2'd0;
            total_zeros_q <= 6'd0;
            max_coeff_q <= 7'd0;
            symbol_seen_count_q <= 7'd0;
            run_sum_q <= 7'd0;
            eob_seen_q <= 1'b0;
            meta_q <= 48'd0;
            last_q <= 1'b0;
            use_8x8_q <= 1'b0;
            block_idx_q <= 2'd0;
            mb_start_q <= 1'b0;
            mb_end_q <= 1'b0;
            frame_start_q <= 1'b0;
            frame_end_q <= 1'b0;
            codebuf_q <= 2304'd0;
            code_len_q <= 12'd0;
            emit_offset_q <= 12'd0;
            out_data_q <= 64'd0;
            out_user_q <= 8'd0;
            out_last_q <= 1'b0;
            out_valid_q <= 1'b0;
            protocol_error_q <= 1'b0;
            code_overflow_q <= 1'b0;
            for (i = 0; i < 64; i = i + 1) begin
                run_store_q[i] <= 6'd0;
                level_store_q[i] <= 16'sd0;
            end
        end else begin
            in_fire_v = s_axis_rle_symbols_tvalid & s_axis_rle_symbols_tready;
            out_fire_v = out_valid_q & m_axis_bits_tready;

            if (protocol_error_q || code_overflow_q) begin
                state_q <= STATE_ERROR;
                out_valid_q <= 1'b0;
                out_data_q <= 64'd0;
                out_user_q <= 8'd0;
                out_last_q <= 1'b0;
            end else begin
                case (state_q)
                    STATE_IDLE: begin
                        out_valid_q <= 1'b0;
                        out_data_q <= 64'd0;
                        out_user_q <= 8'd0;
                        out_last_q <= 1'b0;
                        emit_offset_q <= 12'd0;
                        if (in_fire_v) begin
                            block_start_v = s_axis_rle_symbols_tdata[415];
                            block_end_v = s_axis_rle_symbols_tdata[416];
                            if (!block_start_v) begin
                                protocol_error_q <= 1'b1;
                                state_q <= STATE_ERROR;
                            end else begin
                                total_coeff_in_v = s_axis_rle_symbols_tdata[389:384];
                                trailing_ones_in_v = s_axis_rle_symbols_tdata[391:390];
                                total_zeros_in_v = s_axis_rle_symbols_tdata[397:392];
                                symbol_base_v = {1'b0, s_axis_rle_symbols_tdata[409:404]};
                                symbol_count_v = s_axis_rle_symbols_tdata[414:410];
                                total_coeff_q <= total_coeff_in_v;
                                trailing_ones_q <= trailing_ones_in_v;
                                total_zeros_q <= total_zeros_in_v;
                                use_8x8_q <= s_axis_rle_symbols_tdata[419];
                                block_idx_q <= s_axis_rle_symbols_tdata[418:417];
                                max_coeff_q <= s_axis_rle_symbols_tdata[419] ? 7'd64 : 7'd16;
                                meta_q <= s_axis_rle_symbols_tuser;
                                mb_start_q <= s_axis_rle_symbols_tdata[420];
                                mb_end_q <= s_axis_rle_symbols_tdata[421];
                                frame_start_q <= s_axis_rle_symbols_tdata[422];
                                frame_end_q <= s_axis_rle_symbols_tdata[423];
                                last_q <= s_axis_rle_symbols_tlast;
                                seen_next_v = 7'd0;
                                run_sum_next_v = 7'd0;
                                valid_next_v = 64'd0;
                                eob_next_v = 1'b0;
                                for (i = 0; i < 64; i = i + 1) begin
                                    run_store_q[i] <= 6'd0;
                                    level_store_q[i] <= 16'sd0;
                                end
                                for (j = 0; j < 16; j = j + 1) begin
                                    sym_valid_v = s_axis_rle_symbols_tdata[(24*j)+22];
                                    sym_eob_v = s_axis_rle_symbols_tdata[(24*j)+23];
                                    sym_run_v = s_axis_rle_symbols_tdata[(24*j) +: 6];
                                    sym_level_v = s_axis_rle_symbols_tdata[(24*j)+6 +: 16];
                                    store_index_v = symbol_base_v + j[6:0];
                                    if (sym_valid_v && !sym_eob_v && (store_index_v < 7'd64)) begin
                                        run_store_q[store_index_v[5:0]] <= sym_run_v;
                                        level_store_q[store_index_v[5:0]] <= sym_level_v;
                                        valid_next_v[store_index_v[5:0]] = 1'b1;
                                        seen_next_v = seen_next_v + 7'd1;
                                        run_sum_next_v = run_sum_next_v + {1'b0, sym_run_v};
                                    end else if (sym_valid_v && sym_eob_v) begin
                                        eob_next_v = 1'b1;
                                    end else begin
                                        eob_next_v = eob_next_v;
                                    end
                                end
                                valid_store_q <= valid_next_v;
                                symbol_seen_count_q <= seen_next_v;
                                run_sum_q <= run_sum_next_v;
                                eob_seen_q <= eob_next_v;
                                state_q <= block_end_v ? STATE_BUILD_TOKEN : STATE_COLLECT;
                            end
                        end else begin
                            state_q <= STATE_IDLE;
                        end
                    end

                    STATE_COLLECT: begin
                        if (in_fire_v) begin
                            block_start_v = s_axis_rle_symbols_tdata[415];
                            block_end_v = s_axis_rle_symbols_tdata[416];
                            symbol_base_v = {1'b0, s_axis_rle_symbols_tdata[409:404]};
                            symbol_count_v = s_axis_rle_symbols_tdata[414:410];
                            if (block_start_v) begin
                                protocol_error_q <= 1'b1;
                                state_q <= STATE_ERROR;
                            end else begin
                                mb_end_q <= s_axis_rle_symbols_tdata[421];
                                frame_end_q <= s_axis_rle_symbols_tdata[423];
                                last_q <= s_axis_rle_symbols_tlast;
                                seen_next_v = symbol_seen_count_q;
                                run_sum_next_v = run_sum_q;
                                valid_next_v = valid_store_q;
                                eob_next_v = eob_seen_q;
                                for (j = 0; j < 16; j = j + 1) begin
                                    sym_valid_v = s_axis_rle_symbols_tdata[(24*j)+22];
                                    sym_eob_v = s_axis_rle_symbols_tdata[(24*j)+23];
                                    sym_run_v = s_axis_rle_symbols_tdata[(24*j) +: 6];
                                    sym_level_v = s_axis_rle_symbols_tdata[(24*j)+6 +: 16];
                                    store_index_v = symbol_base_v + j[6:0];
                                    if (sym_valid_v && !sym_eob_v && (store_index_v < 7'd64)) begin
                                        run_store_q[store_index_v[5:0]] <= sym_run_v;
                                        level_store_q[store_index_v[5:0]] <= sym_level_v;
                                        valid_next_v[store_index_v[5:0]] = 1'b1;
                                        seen_next_v = seen_next_v + 7'd1;
                                        run_sum_next_v = run_sum_next_v + {1'b0, sym_run_v};
                                    end else if (sym_valid_v && sym_eob_v) begin
                                        eob_next_v = 1'b1;
                                    end else begin
                                        eob_next_v = eob_next_v;
                                    end
                                end
                                valid_store_q <= valid_next_v;
                                symbol_seen_count_q <= seen_next_v;
                                run_sum_q <= run_sum_next_v;
                                eob_seen_q <= eob_next_v;
                                state_q <= block_end_v ? STATE_BUILD_TOKEN : STATE_COLLECT;
                            end
                        end else begin
                            state_q <= STATE_COLLECT;
                        end
                    end

                    STATE_BUILD_TOKEN: begin
                        build_buf_v = 2304'd0;
                        build_len_v = 12'd0;
                        if (mb_start_q) begin
                            append_bit(build_buf_v, build_len_v, 1'b1);
                            if (use_8x8_q) begin
                                append_bit(build_buf_v, build_len_v, 1'b1);
                                mode_v = meta_q[2:1];
                                append_mode2(build_buf_v, build_len_v, mode_v);
                                if (mode_v == 2'b11) begin
                                    protocol_error_q <= 1'b1;
                                end else begin
                                    protocol_error_q <= protocol_error_q;
                                end
                            end else begin
                                append_bit(build_buf_v, build_len_v, 1'b0);
                            end
                        end else begin
                            build_len_v = build_len_v;
                        end
                        if (!use_8x8_q) begin
                            case (block_idx_q)
                                2'd0: mode_v = meta_q[4:3];
                                2'd1: mode_v = meta_q[6:5];
                                2'd2: mode_v = meta_q[8:7];
                                2'd3: mode_v = meta_q[10:9];
                                default: mode_v = 2'b00;
                            endcase
                            append_mode2(build_buf_v, build_len_v, mode_v);
                            if (mode_v == 2'b11) begin
                                protocol_error_q <= 1'b1;
                            end else begin
                                protocol_error_q <= protocol_error_q;
                            end
                        end else begin
                            mode_v = 2'b00;
                        end
                        token_v = ({3'd0, total_coeff_q} << 2) + {7'd0, trailing_ones_q};
                        append_ue(build_buf_v, build_len_v, {8'd0, token_v});
                        codebuf_q <= build_buf_v;
                        code_len_q <= build_len_v;
                        state_q <= STATE_BUILD_LEVELS;
                    end

                    STATE_BUILD_LEVELS: begin
                        build_buf_v = codebuf_q;
                        build_len_v = code_len_q;
                        if (total_coeff_q != 6'd0) begin
                            for (i = 63; i >= 0; i = i - 1) begin
                                if (i < {26'd0, total_coeff_q}) begin
                                    reverse_rank_v = ({1'b0, total_coeff_q} - 7'd1) - i[6:0];
                                    if (reverse_rank_v < {5'd0, trailing_ones_q}) begin
                                        append_bit(build_buf_v, build_len_v, level_store_q[i][15]);
                                    end else begin
                                        append_se(build_buf_v, build_len_v, level_store_q[i]);
                                    end
                                end else begin
                                    build_len_v = build_len_v;
                                end
                            end
                        end else begin
                            build_len_v = build_len_v;
                        end
                        codebuf_q <= build_buf_v;
                        code_len_q <= build_len_v;
                        state_q <= STATE_BUILD_ZEROS;
                    end

                    STATE_BUILD_ZEROS: begin
                        build_buf_v = codebuf_q;
                        build_len_v = code_len_q;
                        if (total_coeff_q != 6'd0) begin
                            if ({1'b0, total_coeff_q} < max_coeff_q) begin
                                append_ue(build_buf_v, build_len_v, {11'd0, total_zeros_q});
                            end else begin
                                build_len_v = build_len_v;
                            end
                            for (i = 63; i >= 1; i = i - 1) begin
                                if (i < {26'd0, total_coeff_q}) begin
                                    append_ue(build_buf_v, build_len_v, {11'd0, run_store_q[i]});
                                end else begin
                                    build_len_v = build_len_v;
                                end
                            end
                        end else begin
                            build_len_v = build_len_v;
                        end
                        if (build_len_v > 12'd2304) begin
                            code_overflow_q <= 1'b1;
                        end else begin
                            code_overflow_q <= code_overflow_q;
                        end
                        codebuf_q <= build_buf_v;
                        code_len_q <= build_len_v;
                        emit_offset_q <= 12'd0;
                        state_q <= STATE_EMIT;
                    end

                    STATE_EMIT: begin
                        if (!out_valid_q) begin
                            make_group(codebuf_q, code_len_q, emit_offset_q,
                                       group_data_v, group_len_v, final_group_v);
                            out_data_q <= group_data_v;
                            out_user_q <= {frame_start_q & (emit_offset_q == 12'd0), group_len_v};
                            out_last_q <= frame_end_q & last_q & final_group_v;
                            out_valid_q <= (group_len_v != 7'd0);
                            state_q <= STATE_EMIT;
                        end else if (out_fire_v) begin
                            make_group(codebuf_q, code_len_q, emit_offset_q,
                                       group_data_v, group_len_v, final_group_v);
                            remaining_v = code_len_q - emit_offset_q;
                            if (final_group_v) begin
                                out_valid_q <= 1'b0;
                                out_data_q <= 64'd0;
                                out_user_q <= 8'd0;
                                out_last_q <= 1'b0;
                                emit_offset_q <= 12'd0;
                                state_q <= STATE_IDLE;
                            end else begin
                                emit_offset_q <= emit_offset_q + {5'd0, group_len_v};
                                abs_index_v = emit_offset_q + {5'd0, group_len_v};
                                make_group(codebuf_q, code_len_q, abs_index_v,
                                           group_data_v, group_len_v, final_group_v);
                                out_data_q <= group_data_v;
                                out_user_q <= {frame_start_q & (abs_index_v == 12'd0), group_len_v};
                                out_last_q <= frame_end_q & last_q & final_group_v;
                                out_valid_q <= (group_len_v != 7'd0);
                                state_q <= STATE_EMIT;
                            end
                        end else begin
                            out_valid_q <= out_valid_q;
                            out_data_q <= out_data_q;
                            out_user_q <= out_user_q;
                            out_last_q <= out_last_q;
                            state_q <= STATE_EMIT;
                        end
                    end

                    STATE_ERROR: begin
                        state_q <= STATE_ERROR;
                        out_valid_q <= 1'b0;
                        out_data_q <= 64'd0;
                        out_user_q <= 8'd0;
                        out_last_q <= 1'b0;
                    end

                    default: begin
                        state_q <= STATE_ERROR;
                        protocol_error_q <= 1'b1;
                    end
                endcase
            end
        end
    end

endmodule
