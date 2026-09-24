// SPDX-License-Identifier: Apache-2.0
// msfcore pin I/O: cycle-accurate output stage, OE set/clear, status byte,
// serial (shift) pin on bit 7.
// STATUS: fresh leaf block; never compiled. out_stage latches 1 cycle after
// PIN_W executes (pad output), OE set/clear OR/AND per cycle, uo status byte
// currently tied 0 (ua_w/ua_w_en from ctrl, M3 firmware will use it).
// BUDGET: ~250 cells per isa.md section 7.5.
// INTEGRATION: tt_um_msf.v drives the Tiny Tapeout uio_out/uio_oe/uio_in
// pins from here; ctrl provides the latch controls (pin_w* oe_*).
// NEXT: unit test - output 1-cycle latency + OE behavior (M2.6 gate).
`default_nettype none

module tt_um_msf_pio (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  pin_w,     // out_stage source
    input  wire        pin_w_en,
    input  wire [7:0]  oe_set,
    input  wire [7:0]  oe_clr,
    input  wire        oe_set_en,
    input  wire        oe_clr_en,
    input  wire [7:0]  uo_w,      // status byte source
    input  wire        uo_w_en,
    input  wire        serial_out, // from shift datapath (bit 7)
    output wire [7:0]  uio_out,
    output wire [7:0]  uio_oe,
    output wire [7:0]  uo_out
);
  reg [7:0] out_stage;
  reg [7:0] oe;
  reg [7:0] uo;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      out_stage <= 8'h00;
      oe        <= 8'h00;
      uo        <= 8'h00;
    end else begin
      if (pin_w_en) out_stage <= pin_w;
      if (oe_set_en) oe <= oe | oe_set;
      if (oe_clr_en) oe <= oe & ~oe_clr;
      if (uo_w_en)  uo  <= uo_w;
    end
  end

  assign uio_out = {out_stage[6:0], serial_out};
  assign uio_oe  = oe;
  assign uo_out  = uo;
endmodule
