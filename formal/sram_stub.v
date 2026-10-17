// Formal stand-in for the IHP SRAM macro: read data is unconstrained every
// cycle, which over-approximates any memory content (any program, any data
// RAM). A full 512x16 memory model unrolls to >10M SAT variables.
module RM_IHPSG13_2P_512x16_c2_bm_bist (
    input A_CLK, input A_MEN, input A_WEN, input A_REN, input [8:0] A_ADDR,
    input [15:0] A_DIN, input A_DLY, output [15:0] A_DOUT, input [15:0] A_BM,
    input A_BIST_CLK, input A_BIST_EN, input A_BIST_MEN, input A_BIST_WEN,
    input A_BIST_REN, input [8:0] A_BIST_ADDR, input [15:0] A_BIST_DIN, input [15:0] A_BIST_BM,
    input B_CLK, input B_MEN, input B_WEN, input B_REN, input [8:0] B_ADDR,
    input [15:0] B_DIN, input B_DLY, output [15:0] B_DOUT, input [15:0] B_BM,
    input B_BIST_CLK, input B_BIST_EN, input B_BIST_MEN, input B_BIST_WEN,
    input B_BIST_REN, input [8:0] B_BIST_ADDR, input [15:0] B_BIST_DIN, input [15:0] B_BIST_BM
);
  // Outputs left undriven on purpose: run_formal.py runs
  // `setundef -undriven -expose`, which turns them into free (but defined)
  // top-level inputs -- any read data, every cycle.
  wire [15:0] a_free, b_free;
  assign A_DOUT = a_free;
  assign B_DOUT = b_free;
endmodule
