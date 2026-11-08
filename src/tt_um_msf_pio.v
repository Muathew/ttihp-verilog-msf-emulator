// SPDX-License-Identifier: Apache-2.0
// msfcore pin I/O: the three output registers (OUT -> uio_out, OE -> uio_oe,
// UO -> uo_out). The core computes the next value (set/clear/toggle/bit
// writes) from the *_q feedback and asserts *_we; a write executed in cycle
// N is on the pad from cycle N+1. The TX engine overrides the uio_out bits in
// tx_mask with tx_val while it is enabled (OUT keeps its own value).
`default_nettype none

module tt_um_msf_pio (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       out_we,
    input  wire [7:0] out_d,
    input  wire       oe_we,
    input  wire [7:0] oe_d,
    input  wire       uo_we,
    input  wire [7:0] uo_d,
    input  wire [7:0] tx_mask,
    input  wire [7:0] tx_val,
    input  wire [7:0] oe_off,     // the late guard (tt_um_msf_ctrl): these pins off the bus
    output wire [7:0] out_q,
    output wire [7:0] oe_q,
    output wire [7:0] uo_q,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    output wire [7:0] uo_out
);
  reg [7:0] out_r, oe_r, uo_r;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      out_r <= 8'h00;
      oe_r  <= 8'h00;
      uo_r  <= 8'h00;
    end else begin
      if (out_we) out_r <= out_d;
      if (oe_we)  oe_r  <= oe_d;
      if (uo_we)  uo_r  <= uo_d;
    end
  end

  assign out_q   = out_r;
  assign oe_q    = oe_r;
  assign uo_q    = uo_r;
  assign uio_out = (out_r & ~tx_mask) | (tx_val & tx_mask);
  assign uio_oe  = oe_r & ~oe_off;
  assign uo_out  = uo_r;
endmodule
