// Copyright (c) Meta Platforms, Inc. and affiliates.
// This source code is licensed under the MIT license found in the
// LICENSE file in the root directory of this source tree.

// adder_16bit.v -- 16-bit pipelined unsigned adder (Sky130 target)
//
// 2-stage pipeline: registered inputs -> combinational add -> registered outputs
// Latency:    2 clock cycles
// Throughput: 1 result per clock cycle
//
// Ports:
//   clk   (input)         System clock
//   rst   (input)         Synchronous active-high reset
//   a     (input  [15:0]) First operand
//   b     (input  [15:0]) Second operand
//   sum   (output [15:0]) Lower 16 bits of a + b
//   cout  (output)        Carry out (bit 16)

module adder_16bit (
    input  wire        clk,
    input  wire        rst,
    input  wire [15:0] a,
    input  wire [15:0] b,
    output reg  [15:0] sum,
    output reg         cout
);

    // Stage 1: input registers
    reg [15:0] a_reg, b_reg;

    // Combinational addition (between stages)
    wire [16:0] add_result = a_reg + b_reg;

    // Stage 1: register inputs
    always @(posedge clk) begin
        if (rst) begin
            a_reg <= 16'd0;
            b_reg <= 16'd0;
        end else begin
            a_reg <= a;
            b_reg <= b;
        end
    end

    // Stage 2: register outputs
    always @(posedge clk) begin
        if (rst) begin
            sum  <= 16'd0;
            cout <= 1'b0;
        end else begin
            sum  <= add_result[15:0];
            cout <= add_result[16];
        end
    end

endmodule
