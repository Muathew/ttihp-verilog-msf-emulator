// SPDX-License-Identifier: Apache-2.0
// Behavioral model of RM_IHPSG13_2P_512x16_c2_bm_bist for gate-level sim.
// Port A: synchronous read (1-cycle latency when A_DLY=1).
// Port B: synchronous write.
// BIST ports are ignored.

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
  reg [15:0] a_dout_r;

  // Port A: synchronous read (registered, 1-cycle latency)
  always @(posedge A_CLK) begin
    if (A_MEN && A_REN)
      a_dout_r <= mem[A_ADDR];
    else
      a_dout_r <= 16'h0;
  end
  assign A_DOUT = a_dout_r;

  // Port B: synchronous write
  always @(posedge B_CLK) begin
    if (B_MEN && B_WEN)
      mem[B_ADDR] <= B_DIN;
  end

  // Port B read (unused in this design, but provide for completeness)
  assign B_DOUT = mem[B_ADDR];

endmodule
`endif
