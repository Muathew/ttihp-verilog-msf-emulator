// SPDX-License-Identifier: Apache-2.0
// msfcore pin I/O: the three output registers (OUT, OE, UO) and the pads.
// The core computes the next value (set/clear/toggle/bit writes) from the *_q
// feedback and asserts *_we; a write executed in cycle N is on the pad from
// cycle N+1. The TX engines override the uio_out bits in tx_mask with tx_val (or,
// v4, the uo_out bits in txu_mask with txu_val) while they are enabled (OUT and UO
// keep their own values), and the late guard takes pins off the bus (oe_off).
//
// v4 (plan-v4 B4): uio_out, uio_oe and uo_out come straight from flops. Each holds
// what the old combinational expression
//     uio_out = (OUT & ~tx_mask) | (tx_val & tx_mask),   uio_oe = OE & ~oe_off,
//     uo_out  = (UO & ~txu_mask) | (txu_val & txu_mask)
// gives, loaded one cycle ahead from the *next* values of OUT, OE, UO, the engines'
// masks and levels, and the guard, so the timing is the old one but a pad cannot
// glitch when an engine takes or releases a pin with both sides at 1.
// uio_out_c / uio_oe_c / uo_out_c are the old expressions, kept only for the formal
// property that the two agree (they have no load, so synthesis drops them).
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
    input  wire [7:0] tx_mask,      // the engines' override of uio_out, now ...
    input  wire [7:0] tx_val,
    input  wire [7:0] tx_mask_n,    // ... and next cycle
    input  wire [7:0] tx_val_n,
    input  wire [7:0] txu_mask,     // the engines' override of uo_out, now ...
    input  wire [7:0] txu_val,
    input  wire [7:0] txu_mask_n,   // ... and next cycle
    input  wire [7:0] txu_val_n,
    input  wire [7:0] oe_off,       // the late guard (tt_um_msf_ctrl): these pins off the bus, now
    input  wire [7:0] oe_off_n,     //   and next cycle
    output wire [7:0] out_q,
    output wire [7:0] oe_q,
    output wire [7:0] uo_q,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    output wire [7:0] uo_out,
    output wire [7:0] uio_out_c,
    output wire [7:0] uio_oe_c,
    output wire [7:0] uo_out_c
);
  reg [7:0] out_r, oe_r, uo_r;
  reg [7:0] pad_out, pad_oe, pad_uo;

  wire [7:0] out_n = out_we ? out_d : out_r;
  wire [7:0] oe_n  = oe_we  ? oe_d  : oe_r;
  wire [7:0] uo_n  = uo_we  ? uo_d  : uo_r;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      out_r   <= 8'h00;
      oe_r    <= 8'h00;
      uo_r    <= 8'h00;
      pad_out <= 8'h00;
      pad_oe  <= 8'h00;
      pad_uo  <= 8'h00;
    end else begin
      out_r   <= out_n;
      oe_r    <= oe_n;
      uo_r    <= uo_n;
      pad_out <= (out_n & ~tx_mask_n) | (tx_val_n & tx_mask_n);
      pad_oe  <= oe_n & ~oe_off_n;
      pad_uo  <= (uo_n & ~txu_mask_n) | (txu_val_n & txu_mask_n);
    end
  end

  assign out_q     = out_r;
  assign oe_q      = oe_r;
  assign uo_q      = uo_r;
  assign uio_out   = pad_out;
  assign uio_oe    = pad_oe;
  assign uo_out    = pad_uo;
  assign uio_out_c = (out_r & ~tx_mask) | (tx_val & tx_mask);
  assign uio_oe_c  = oe_r & ~oe_off;
  assign uo_out_c  = (uo_r & ~txu_mask) | (txu_val & txu_mask);
endmodule

`default_nettype wire
