`default_nettype none
`timescale 1ns / 1ps
// tb.v with a VCD window for power analysis: the test raises dump_go to
// start recording tb.user_project and lowers it to stop.
module tb ();
  reg clk;
  reg rst_n;
  reg ena;
  reg [7:0] ui_in;
  reg [7:0] uio_in;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;
  reg dump_go = 1'b0;

  tt_um_msf user_project (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (ena),
      .clk    (clk),
      .rst_n  (rst_n)
  );

  reg [8*128:1] vcd;
  initial begin
    if (!$value$plusargs("vcd=%s", vcd)) vcd = "power.vcd";
    $dumpfile(vcd);
  end
  always @(posedge dump_go) $dumpvars(0, tb.user_project);
  always @(negedge dump_go) begin
    $dumpoff;
    $dumpflush;
  end
endmodule
