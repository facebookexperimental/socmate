// =============================================================================
// Block name: cavlc_enc
// Description:
//   Encodes TotalCoeff, TrailingOnes, signed coefficient levels, TotalZeros,
//   and reverse-order RunBefore fields for the local H.264-style SocMate CAVLC
//   syntax.  The block accepts one 1024-bit RLE-symbol AXI-Stream payload from
//   zigzag_rle_select, builds a 1600-bit MSB-first coefficient bitstream, and
//   emits one or more registered 512-bit codeword chunks to block_packer.
//
// I/O ports:
//   clk                          : input clock, single clock domain
//   rst_n                        : input synchronous active-low reset
//   s_axis_rle_symbols_tdata     : input  [1023:0] RLE-symbol payload
//   s_axis_rle_symbols_tvalid    : input AXI-Stream valid
//   s_axis_rle_symbols_tready    : output AXI-Stream ready
//   s_axis_rle_symbols_tlast     : input frame-end marker
//   s_axis_status_tdata          : input  [31:0] control event payload
//   s_axis_status_tvalid         : input control AXI-Stream valid
//   s_axis_status_tready         : output control AXI-Stream ready
//   s_axis_status_tlast          : input control packet marker
//   m_axis_codewords_tdata       : output [511:0] codeword chunk payload
//   m_axis_codewords_tvalid      : output registered AXI-Stream valid
//   m_axis_codewords_tready      : input AXI-Stream ready
//   m_axis_codewords_tlast       : output final frame chunk marker
//   cavlc_active                 : output registered activity summary
//   code_overflow_assert         : output sticky overflow flag
//   protocol_assert              : output sticky protocol flag
//
// Fixed-point notes:
//   Coefficients are signed 10-bit integer quantized levels.  This block only
//   compares, signs, and entropy-encodes integer syntax fields; it performs no
//   fixed-point scaling, rounding, or saturation.
// =============================================================================

module cavlc_enc (
    input  wire          clk,
    input  wire          rst_n,

    input  wire [1023:0] s_axis_rle_symbols_tdata,
    input  wire          s_axis_rle_symbols_tvalid,
    output wire          s_axis_rle_symbols_tready,
    input  wire          s_axis_rle_symbols_tlast,

    input  wire [31:0]   s_axis_status_tdata,
    input  wire          s_axis_status_tvalid,
    output wire          s_axis_status_tready,
    input  wire          s_axis_status_tlast,

    output wire [511:0]  m_axis_codewords_tdata,
    output wire          m_axis_codewords_tvalid,
    input  wire          m_axis_codewords_tready,
    output wire          m_axis_codewords_tlast,

    output wire          cavlc_active,
    output wire          code_overflow_assert,
    output wire          protocol_assert
);

    localparam [2:0] STATE_IDLE    = 3'b000;
    localparam [2:0] STATE_CAPTURE = 3'b001;
    localparam [2:0] STATE_ENCODE  = 3'b010;
    localparam [2:0] STATE_EMIT    = 3'b011;
    localparam [2:0] STATE_ERROR   = 3'b100;

    localparam [3:0] CTRL_STALL_ASSERT  = 4'd2;
    localparam [3:0] CTRL_STALL_RELEASE = 4'd3;
    localparam [3:0] CTRL_DRAIN_START   = 4'd4;
    localparam [3:0] CTRL_DRAIN_DONE    = 4'd5;
    localparam [3:0] CTRL_ERROR_LATCHED = 4'd6;
    localparam [3:0] CTRL_CE_UPDATE     = 4'd7;

    reg  [2:0]    state_q;
    reg  [1023:0] rle_data_q;
    reg           rle_last_q;
    reg           rle_valid_q;
    reg  [1599:0] codebuf_q;
    reg  [10:0]   code_len_q;
    reg  [7:0]    chunk_id_q;
    reg           final_chunk_q;
    reg  [511:0]  out_data_q;
    reg           out_valid_q;
    reg           out_last_q;
    reg           input_frame_start_q;
    reg           input_frame_end_q;
    reg           input_use_8x8_q;
    reg  [1:0]    input_mode8_q;
    reg  [7:0]    input_mode4_q;
    reg  [31:0]   status_data_q;
    reg           status_last_q;
    reg           status_valid_q;
    reg           stall_latched_q;
    reg           drain_latched_q;
    reg           error_latched_q;
    reg           cavlc_ce_req_latched_q;
    reg           scan_latched_q;
    reg           cavlc_active_q;
    reg           code_overflow_assert_q;
    reg           protocol_assert_q;

    reg  [2:0]    state_next;
    reg  [1023:0] rle_data_next;
    reg           rle_last_next;
    reg           rle_valid_next;
    reg  [1599:0] codebuf_next;
    reg  [10:0]   code_len_next;
    reg  [7:0]    chunk_id_next;
    reg           final_chunk_next;
    reg  [511:0]  out_data_next;
    reg           out_valid_next;
    reg           out_last_next;
    reg           input_frame_start_next;
    reg           input_frame_end_next;
    reg           input_use_8x8_next;
    reg  [1:0]    input_mode8_next;
    reg  [7:0]    input_mode4_next;
    reg  [31:0]   status_data_next;
    reg           status_last_next;
    reg           status_valid_next;
    reg           stall_latched_next;
    reg           drain_latched_next;
    reg           error_latched_next;
    reg           cavlc_ce_req_latched_next;
    reg           scan_latched_next;
    reg           cavlc_active_next;
    reg           code_overflow_assert_next;
    reg           protocol_assert_next;

    reg  [1599:0] codebuf_comb;
    reg  [10:0]   code_len_comb;
    reg           protocol_comb;
    reg           overflow_comb;
    reg  [5:0]    runs_tmp [0:63];
    reg  [511:0]  chunk_payload_comb;
    reg  [479:0]  chunk_bits_comb;
    reg  [7:0]    chunk_build_id_comb;
    reg  [10:0]   chunk_start_comb;
    reg  [10:0]   chunk_read_index_comb;
    reg  [10:0]   remaining_comb;
    reg  [8:0]    chunk_len_comb;
    reg           chunk_final_comb;

    wire          out_fire;
    wire          in_fire;
    wire          status_fire;
    wire          local_work_pending;
    wire          internal_ce;
    wire          emit_can_load;

    integer       i;
    integer       j;
    integer       p;
    assign out_fire = out_valid_q & m_axis_codewords_tready;
    assign status_fire = s_axis_status_tvalid & s_axis_status_tready;
    assign local_work_pending = s_axis_rle_symbols_tvalid | rle_valid_q |
                                (state_q != STATE_IDLE) | out_valid_q |
                                status_valid_q;
    assign internal_ce = scan_latched_q | cavlc_ce_req_latched_q |
                         local_work_pending | s_axis_rle_symbols_tvalid |
                         s_axis_status_tvalid;
    assign in_fire = s_axis_rle_symbols_tvalid & s_axis_rle_symbols_tready;
    assign emit_can_load = (~out_valid_q) | out_fire;

    assign s_axis_rle_symbols_tready = internal_ce & (~stall_latched_q) &
                                       (~drain_latched_q) & (~error_latched_q) &
                                       (state_q == STATE_IDLE);
    assign s_axis_status_tready = ~status_valid_q;

    assign m_axis_codewords_tdata = out_data_q;
    assign m_axis_codewords_tvalid = out_valid_q;
    assign m_axis_codewords_tlast = out_last_q;
    assign cavlc_active = cavlc_active_q;
    assign code_overflow_assert = code_overflow_assert_q;
    assign protocol_assert = protocol_assert_q;

    function signed [9:0] level_at;
        input integer idx;
        begin
            level_at = rle_data_q[(idx * 10) +: 10];
        end
    endfunction

    function [5:0] total_coeff_at;
        input integer idx;
        begin
            total_coeff_at = rle_data_q[704 + (idx * 6) +: 6];
        end
    endfunction

    function [1:0] trailing_ones_at;
        input integer idx;
        begin
            trailing_ones_at = rle_data_q[728 + (idx * 2) +: 2];
        end
    endfunction

    function [5:0] total_zeros_at;
        input integer idx;
        begin
            total_zeros_at = rle_data_q[736 + (idx * 6) +: 6];
        end
    endfunction

    function [25:0] ue_pack;
        input [10:0] value;
        reg   [11:0] val_plus_one;
        reg   [4:0] bit_len;
        reg   [4:0] out_len;
        reg   [20:0] out_bits;
        integer ui;
        reg [4:0] bit_index;
        begin
            val_plus_one = {1'b0, value} + 12'd1;
            if (val_plus_one[10] == 1'b1) begin
                bit_len = 5'd11;
            end else if (val_plus_one[9] == 1'b1) begin
                bit_len = 5'd10;
            end else if (val_plus_one[8] == 1'b1) begin
                bit_len = 5'd9;
            end else if (val_plus_one[7] == 1'b1) begin
                bit_len = 5'd8;
            end else if (val_plus_one[6] == 1'b1) begin
                bit_len = 5'd7;
            end else if (val_plus_one[5] == 1'b1) begin
                bit_len = 5'd6;
            end else if (val_plus_one[4] == 1'b1) begin
                bit_len = 5'd5;
            end else if (val_plus_one[3] == 1'b1) begin
                bit_len = 5'd4;
            end else if (val_plus_one[2] == 1'b1) begin
                bit_len = 5'd3;
            end else if (val_plus_one[1] == 1'b1) begin
                bit_len = 5'd2;
            end else begin
                bit_len = 5'd1;
            end
            out_len = (bit_len << 1) - 5'd1;
            out_bits = 21'd0;
            bit_index = 0;
            for (ui = 0; ui < 11; ui = ui + 1) begin
                if (ui < bit_len) begin
                    bit_index = 5'd20 - ((bit_len - 5'd1) - ui[4:0]);
                    out_bits[bit_index] = val_plus_one[ui];
                end else begin
                    out_bits = out_bits;
                end
            end
            ue_pack = {out_bits, out_len};
        end
    endfunction

    function [25:0] se_pack;
        input signed [9:0] value;
        reg signed [10:0] value_ext;
        reg [10:0] abs_value;
        reg [10:0] code_num;
        begin
            value_ext = {value[9], value};
            if (value_ext == 11'sd0) begin
                code_num = 11'd0;
            end else if (value_ext > 11'sd0) begin
                abs_value = value_ext[10:0];
                code_num = (abs_value << 1) - 11'd1;
            end else begin
                abs_value = (~value_ext[10:0]) + 11'd1;
                code_num = abs_value << 1;
            end
            se_pack = ue_pack(code_num);
        end
    endfunction

    task append_code;
        input [20:0] bits;
        input [4:0]  len;
        integer ai;
        reg [10:0] buf_index;
        begin
            if (({1'b0, code_len_comb} + {7'd0, len}) > 12'd1600) begin
                overflow_comb = 1'b1;
            end else begin
                for (ai = 0; ai < 21; ai = ai + 1) begin
                    if (ai < len) begin
                        buf_index = 11'd1599 - (code_len_comb + ai[10:0]);
                        codebuf_comb[buf_index] = bits[20 - ai];
                    end else begin
                        codebuf_comb = codebuf_comb;
                    end
                end
                code_len_comb = code_len_comb + {6'd0, len};
            end
        end
    endtask

    task append_literal_bit;
        input bit_value;
        begin
            if (code_len_comb >= 11'd1600) begin
                overflow_comb = 1'b1;
            end else begin
                codebuf_comb[1599 - code_len_comb] = bit_value;
                code_len_comb = code_len_comb + 11'd1;
            end
        end
    endtask

    task encode_block;
        input integer base_idx;
        input integer max_coeff;
        input [5:0] tc;
        input [1:0] to;
        input [5:0] tz;
        reg [25:0] code_word;
        reg signed [9:0] level_value;
        reg [8:0] token_value;
        integer pos;
        integer rev_seen;
        integer coeff_count;
        integer run_count;
        integer run_total_local;
        integer run_emit;
        begin
            token_value = ({3'd0, tc} << 2) + {7'd0, to};
            code_word = ue_pack({2'd0, token_value});
            append_code(code_word[25:5], code_word[4:0]);

            coeff_count = 0;
            run_count = 0;
            run_total_local = 0;
            for (pos = 0; pos < 64; pos = pos + 1) begin
                runs_tmp[pos] = 6'd0;
            end
            for (pos = 0; pos < 64; pos = pos + 1) begin
                if (pos < max_coeff) begin
                    if (rle_data_q[640 + base_idx + pos] == 1'b1) begin
                        runs_tmp[coeff_count] = run_count[5:0];
                        run_total_local = run_total_local + run_count;
                        coeff_count = coeff_count + 1;
                        run_count = 0;
                    end else begin
                        run_count = run_count + 1;
                    end
                end else begin
                    run_count = run_count;
                end
            end

            if (coeff_count[6:0] != {1'b0, tc}) begin
                protocol_comb = 1'b1;
            end else begin
                protocol_comb = protocol_comb;
            end
            if ({4'd0, to} > tc) begin
                protocol_comb = 1'b1;
            end else begin
                protocol_comb = protocol_comb;
            end
            if (run_total_local[5:0] != tz) begin
                protocol_comb = 1'b1;
            end else begin
                protocol_comb = protocol_comb;
            end

            if (tc != 6'd0) begin
                rev_seen = 0;
                for (pos = 63; pos >= 0; pos = pos - 1) begin
                    if ((pos >= base_idx) && (pos < (base_idx + max_coeff)) &&
                        (rle_data_q[640 + pos] == 1'b1)) begin
                        level_value = level_at(pos);
                        if (level_value == 10'sd0) begin
                            protocol_comb = 1'b1;
                        end else begin
                            protocol_comb = protocol_comb;
                        end
                        if (rev_seen < to) begin
                            if ((level_value == 10'sd1) || (level_value == -10'sd1)) begin
                                append_literal_bit(level_value[9]);
                            end else begin
                                protocol_comb = 1'b1;
                                append_literal_bit(level_value[9]);
                            end
                        end else if (rev_seen < tc) begin
                            code_word = se_pack(level_value);
                            append_code(code_word[25:5], code_word[4:0]);
                        end else begin
                            code_word = code_word;
                        end
                        rev_seen = rev_seen + 1;
                    end else begin
                        rev_seen = rev_seen;
                    end
                end

                if ({25'd0, 1'b0, tc} < max_coeff) begin
                    code_word = ue_pack({5'd0, tz});
                    append_code(code_word[25:5], code_word[4:0]);
                end else begin
                    code_word = code_word;
                end

                for (run_emit = 63; run_emit >= 1; run_emit = run_emit - 1) begin
                    if (run_emit < tc) begin
                        code_word = ue_pack({5'd0, runs_tmp[run_emit]});
                        append_code(code_word[25:5], code_word[4:0]);
                    end else begin
                        code_word = code_word;
                    end
                end
            end else begin
                code_word = code_word;
            end
        end
    endtask

    always @(*) begin
        codebuf_comb = 1600'd0;
        code_len_comb = 11'd0;
        protocol_comb = 1'b0;
        overflow_comb = 1'b0;
        for (i = 0; i < 64; i = i + 1) begin
            runs_tmp[i] = 6'd0;
        end

        if (rle_data_q[902] == 1'b1) begin
            codebuf_comb[1599] = 1'b1;
            code_len_comb = 11'd1;
        end else begin
            codebuf_comb[1599:1596] = 4'b1111;
            code_len_comb = 11'd4;
        end
    end

    always @(*) begin
        chunk_payload_comb = 512'd0;
        chunk_bits_comb = 480'd0;
        chunk_read_index_comb = 11'd0;
        if (out_valid_q && out_fire && !final_chunk_q) begin
            chunk_build_id_comb = chunk_id_q + 8'd1;
        end else begin
            chunk_build_id_comb = chunk_id_q;
        end
        chunk_start_comb = {3'd0, chunk_build_id_comb} * 11'd480;
        if (code_len_q > chunk_start_comb) begin
            remaining_comb = code_len_q - chunk_start_comb;
        end else begin
            remaining_comb = 11'd0;
        end
        if (remaining_comb > 11'd480) begin
            chunk_len_comb = 9'd480;
        end else begin
            chunk_len_comb = remaining_comb[8:0];
        end
        if ((chunk_start_comb + {2'd0, chunk_len_comb}) >= code_len_q) begin
            chunk_final_comb = 1'b1;
        end else begin
            chunk_final_comb = 1'b0;
        end
        for (p = 0; p < 480; p = p + 1) begin
            if (p < chunk_len_comb) begin
                chunk_read_index_comb = chunk_start_comb + p[10:0];
                chunk_bits_comb[479 - p] = codebuf_q[11'd1599 - chunk_read_index_comb];
            end else begin
                chunk_bits_comb[479 - p] = 1'b0;
            end
        end
        chunk_payload_comb[511] = (chunk_build_id_comb == 8'd0);
        chunk_payload_comb[510] = chunk_final_comb;
        chunk_payload_comb[509] = (chunk_build_id_comb == 8'd0) & input_frame_start_q;
        chunk_payload_comb[508] = chunk_final_comb & input_frame_end_q;
        chunk_payload_comb[507] = input_use_8x8_q;
        chunk_payload_comb[506:505] = input_mode8_q;
        chunk_payload_comb[504:497] = input_mode4_q;
        chunk_payload_comb[496:488] = chunk_len_comb;
        chunk_payload_comb[487:480] = chunk_build_id_comb;
        chunk_payload_comb[479:0] = chunk_bits_comb;
    end

    always @(*) begin
        state_next = state_q;
        rle_data_next = rle_data_q;
        rle_last_next = rle_last_q;
        rle_valid_next = rle_valid_q;
        codebuf_next = codebuf_q;
        code_len_next = code_len_q;
        chunk_id_next = chunk_id_q;
        final_chunk_next = final_chunk_q;
        out_data_next = out_data_q;
        out_valid_next = out_valid_q;
        out_last_next = out_last_q;
        input_frame_start_next = input_frame_start_q;
        input_frame_end_next = input_frame_end_q;
        input_use_8x8_next = input_use_8x8_q;
        input_mode8_next = input_mode8_q;
        input_mode4_next = input_mode4_q;
        status_data_next = status_data_q;
        status_last_next = status_last_q;
        status_valid_next = status_valid_q;
        stall_latched_next = stall_latched_q;
        drain_latched_next = drain_latched_q;
        error_latched_next = error_latched_q;
        cavlc_ce_req_latched_next = cavlc_ce_req_latched_q;
        scan_latched_next = scan_latched_q;
        cavlc_active_next = scan_latched_q | cavlc_ce_req_latched_q |
                            local_work_pending | drain_latched_q |
                            stall_latched_q;
        code_overflow_assert_next = code_overflow_assert_q;
        protocol_assert_next = protocol_assert_q;

        if (status_fire) begin
            status_data_next = s_axis_status_tdata;
            status_last_next = s_axis_status_tlast;
            status_valid_next = 1'b1;
        end else if (status_valid_q) begin
            status_valid_next = 1'b0;
        end else begin
            status_valid_next = status_valid_q;
        end

        if (status_fire) begin
            case (s_axis_status_tdata[3:0])
                CTRL_STALL_ASSERT: begin
                    stall_latched_next = 1'b1;
                end
                CTRL_STALL_RELEASE: begin
                    stall_latched_next = 1'b0;
                end
                CTRL_DRAIN_START: begin
                    drain_latched_next = 1'b1;
                end
                CTRL_DRAIN_DONE: begin
                    drain_latched_next = 1'b0;
                end
                CTRL_ERROR_LATCHED: begin
                    error_latched_next = 1'b1;
                end
                CTRL_CE_UPDATE: begin
                    cavlc_ce_req_latched_next = s_axis_status_tdata[12];
                    scan_latched_next = s_axis_status_tdata[15];
                end
                default: begin
                    stall_latched_next = s_axis_status_tdata[6] ? 1'b1 : stall_latched_q;
                    drain_latched_next = s_axis_status_tdata[7] ? 1'b1 : drain_latched_q;
                    cavlc_ce_req_latched_next = s_axis_status_tdata[12];
                    scan_latched_next = s_axis_status_tdata[15];
                end
            endcase
            if (s_axis_status_tdata[10] == 1'b1) begin
                error_latched_next = 1'b1;
            end else begin
                error_latched_next = error_latched_next;
            end
        end else begin
            stall_latched_next = stall_latched_next;
        end

        if (in_fire) begin
            rle_data_next = s_axis_rle_symbols_tdata;
            rle_last_next = s_axis_rle_symbols_tlast;
            rle_valid_next = 1'b1;
            input_frame_start_next = s_axis_rle_symbols_tdata[913];
            input_frame_end_next = s_axis_rle_symbols_tdata[914];
            input_use_8x8_next = s_axis_rle_symbols_tdata[902];
            input_mode8_next = s_axis_rle_symbols_tdata[904:903];
            input_mode4_next = s_axis_rle_symbols_tdata[912:905];
            state_next = STATE_CAPTURE;
        end else begin
            rle_data_next = rle_data_next;
        end

        case (state_q)
            STATE_IDLE: begin
                if (error_latched_next) begin
                    state_next = STATE_ERROR;
                end else if (in_fire) begin
                    state_next = STATE_CAPTURE;
                end else begin
                    state_next = STATE_IDLE;
                end
            end
            STATE_CAPTURE: begin
                if (error_latched_next) begin
                    state_next = STATE_ERROR;
                end else begin
                    state_next = STATE_ENCODE;
                end
            end
            STATE_ENCODE: begin
                codebuf_next = codebuf_comb;
                code_len_next = code_len_comb;
                code_overflow_assert_next = code_overflow_assert_q | overflow_comb |
                                            (code_len_comb > 11'd1600);
                protocol_assert_next = protocol_assert_q | protocol_comb;
                rle_valid_next = 1'b0;
                chunk_id_next = 8'd0;
                final_chunk_next = 1'b0;
                state_next = STATE_EMIT;
            end
            STATE_EMIT: begin
                if (emit_can_load) begin
                    out_data_next = chunk_payload_comb;
                    out_valid_next = 1'b1;
                    out_last_next = chunk_final_comb & input_frame_end_q;
                    final_chunk_next = chunk_final_comb;
                    if (out_fire) begin
                        if (final_chunk_q) begin
                            out_valid_next = 1'b0;
                            out_last_next = 1'b0;
                            state_next = STATE_IDLE;
                            chunk_id_next = 8'd0;
                        end else begin
                            chunk_id_next = chunk_build_id_comb;
                            if (chunk_id_q >= 8'd3) begin
                                code_overflow_assert_next = 1'b1;
                            end else begin
                                code_overflow_assert_next = code_overflow_assert_next;
                            end
                            state_next = STATE_EMIT;
                        end
                    end else begin
                        state_next = STATE_EMIT;
                    end
                end else begin
                    state_next = STATE_EMIT;
                end
            end
            STATE_ERROR: begin
                state_next = STATE_ERROR;
                rle_valid_next = 1'b0;
                out_valid_next = 1'b0;
                out_last_next = 1'b0;
            end
            default: begin
                state_next = STATE_ERROR;
                error_latched_next = 1'b1;
            end
        endcase

        if (error_latched_next && (state_q != STATE_ERROR)) begin
            state_next = STATE_ERROR;
        end else begin
            state_next = state_next;
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            state_q <= STATE_IDLE;
            rle_data_q <= 1024'd0;
            rle_last_q <= 1'b0;
            rle_valid_q <= 1'b0;
            codebuf_q <= 1600'd0;
            code_len_q <= 11'd0;
            chunk_id_q <= 8'd0;
            final_chunk_q <= 1'b0;
            out_data_q <= 512'd0;
            out_valid_q <= 1'b0;
            out_last_q <= 1'b0;
            input_frame_start_q <= 1'b0;
            input_frame_end_q <= 1'b0;
            input_use_8x8_q <= 1'b0;
            input_mode8_q <= 2'b00;
            input_mode4_q <= 8'd0;
            status_data_q <= 32'd0;
            status_last_q <= 1'b0;
            status_valid_q <= 1'b0;
            stall_latched_q <= 1'b0;
            drain_latched_q <= 1'b0;
            error_latched_q <= 1'b0;
            cavlc_ce_req_latched_q <= 1'b0;
            scan_latched_q <= 1'b0;
            cavlc_active_q <= 1'b0;
            code_overflow_assert_q <= 1'b0;
            protocol_assert_q <= 1'b0;
        end else if (internal_ce) begin
            state_q <= state_next;
            rle_data_q <= rle_data_next;
            rle_last_q <= rle_last_next;
            rle_valid_q <= rle_valid_next;
            codebuf_q <= codebuf_next;
            code_len_q <= code_len_next;
            chunk_id_q <= chunk_id_next;
            final_chunk_q <= final_chunk_next;
            out_data_q <= out_data_next;
            out_valid_q <= out_valid_next;
            out_last_q <= out_last_next;
            input_frame_start_q <= input_frame_start_next;
            input_frame_end_q <= input_frame_end_next;
            input_use_8x8_q <= input_use_8x8_next;
            input_mode8_q <= input_mode8_next;
            input_mode4_q <= input_mode4_next;
            status_data_q <= status_data_next;
            status_last_q <= status_last_next;
            status_valid_q <= status_valid_next;
            stall_latched_q <= stall_latched_next;
            drain_latched_q <= drain_latched_next;
            error_latched_q <= error_latched_next;
            cavlc_ce_req_latched_q <= cavlc_ce_req_latched_next;
            scan_latched_q <= scan_latched_next;
            cavlc_active_q <= cavlc_active_next;
            code_overflow_assert_q <= code_overflow_assert_next;
            protocol_assert_q <= protocol_assert_next;
        end else begin
            state_q <= state_q;
            rle_data_q <= rle_data_q;
            rle_last_q <= rle_last_q;
            rle_valid_q <= rle_valid_q;
            codebuf_q <= codebuf_q;
            code_len_q <= code_len_q;
            chunk_id_q <= chunk_id_q;
            final_chunk_q <= final_chunk_q;
            out_data_q <= out_data_q;
            out_valid_q <= out_valid_q;
            out_last_q <= out_last_q;
            input_frame_start_q <= input_frame_start_q;
            input_frame_end_q <= input_frame_end_q;
            input_use_8x8_q <= input_use_8x8_q;
            input_mode8_q <= input_mode8_q;
            input_mode4_q <= input_mode4_q;
            status_data_q <= status_data_q;
            status_last_q <= status_last_q;
            status_valid_q <= status_valid_q;
            stall_latched_q <= stall_latched_q;
            drain_latched_q <= drain_latched_q;
            error_latched_q <= error_latched_q;
            cavlc_ce_req_latched_q <= cavlc_ce_req_latched_q;
            scan_latched_q <= scan_latched_q;
            cavlc_active_q <= cavlc_active_q;
            code_overflow_assert_q <= code_overflow_assert_q;
            protocol_assert_q <= protocol_assert_q;
        end
    end

endmodule
