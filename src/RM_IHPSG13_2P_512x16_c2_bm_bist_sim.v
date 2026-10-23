// SPDX-License-Identifier: Apache-2.0
// Behavioural model of RM_IHPSG13_2P_512x16_c2_bm_bist for gate-level sim.
// Mirrors the IHP SRAM_2P_behavioral_bm_bist core (ihp-sg13g2/libs.ref/
// sg13g2_sram/verilog; CMOS5L's sg13cmos5l_sram links to it): per port, a
// write happens when MEN&WEN, a read when
// MEN&REN, reads are registered (data valid the cycle after the edge) and
// DOUT HOLDS its value when the port is not reading. Bit masks honoured,
// BIST ports ignored, A_DLY/B_DLY must be tied high (as in the real macro).

`ifndef SYNTHESIS
module RM_IHPSG13_2P_512x16_c2_bm_bist (
    input  A_CLK,
    input  A_MEN,
    input  A_WEN,
    input  A_REN,
    input  [8:0] A_ADDR,
    input  [15:0] A_DIN,
    input  A_DLY,
    output [15:0] A_DOUT,
    input  [15:0] A_BM,
    input  A_BIST_CLK,
    input  A_BIST_EN,
    input  A_BIST_MEN,
    input  A_BIST_WEN,
    input  A_BIST_REN,
    input  [8:0] A_BIST_ADDR,
    input  [15:0] A_BIST_DIN,
    input  [15:0] A_BIST_BM,

    input  B_CLK,
    input  B_MEN,
    input  B_WEN,
    input  B_REN,
    input  [8:0] B_ADDR,
    input  [15:0] B_DIN,
    input  B_DLY,
    output [15:0] B_DOUT,
    input  [15:0] B_BM,
    input  B_BIST_CLK,
    input  B_BIST_EN,
    input  B_BIST_MEN,
    input  B_BIST_WEN,
    input  B_BIST_REN,
    input  [8:0] B_BIST_ADDR,
    input  [15:0] B_BIST_DIN,
    input  [15:0] B_BIST_BM
);
  reg [15:0] mem [0:511];
  reg [15:0] a_dout_r = 16'h0;
  reg [15:0] b_dout_r = 16'h0;

  // Real SRAM powers up random; zero it so GL sim is deterministic.
  integer init_i;
  initial
    for (init_i = 0; init_i < 512; init_i = init_i + 1)
      mem[init_i] = 16'h0;

  always @(posedge A_CLK) begin
    if (A_MEN && A_WEN) begin
      mem[A_ADDR] <= (mem[A_ADDR] & ~A_BM) | (A_DIN & A_BM);
      if (A_REN) a_dout_r <= (mem[A_ADDR] & ~A_BM) | (A_DIN & A_BM);
    end else if (A_MEN && A_REN)
      a_dout_r <= mem[A_ADDR];
  end

  always @(posedge B_CLK) begin
    if (B_MEN && B_WEN) begin
      mem[B_ADDR] <= (mem[B_ADDR] & ~B_BM) | (B_DIN & B_BM);
      if (B_REN) b_dout_r <= (mem[B_ADDR] & ~B_BM) | (B_DIN & B_BM);
    end else if (B_MEN && B_REN)
      b_dout_r <= mem[B_ADDR];
  end

  assign A_DOUT = a_dout_r;
  assign B_DOUT = b_dout_r;
endmodule
`endif
