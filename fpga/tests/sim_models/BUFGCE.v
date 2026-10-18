// SPDX-License-Identifier: Apache-2.0
// Simulation-only stand-in for the Xilinx BUFGCE primitive (Vivado uses the
// UNISIM cell; never add this file to the Vivado project). Latch-based clock
// gate: CE is captured while I is low, so a CE that is high for one I cycle
// passes exactly one rising edge, glitch free.
`timescale 1ns/1ps
`default_nettype none

module BUFGCE #(
    parameter CE_TYPE = "SYNC",
    parameter [0:0] IS_CE_INVERTED = 1'b0,
    parameter [0:0] IS_I_INVERTED = 1'b0
) (
    output wire O,
    input  wire CE,
    input  wire I
);
  reg en = 1'b0;
  always @(I or CE) if (!I) en = CE;
  assign O = I & en;
endmodule
