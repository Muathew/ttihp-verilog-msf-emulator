`default_nettype none
`timescale 1ns / 1ps
// msfcore testbench (cocotb shim). STATUS: updated to instantiate tt_um_msf
// (was the template tt_um_example); NO clock created here -- cocotb tests
// drive clk/rst_n/inputs via the dut.* handles (cocotb start_soon/Clock).
// INTEGRATION: TOPLEVEL = tb in test/Makefile; cocotb module test_core is
// declared in the Makefile but NOT YET WRITTEN (test/test.py is the stale
// template example asserting uo==ui+uio -- outdated, harmless if not run).
// KNOWN BUGS: (1) Makefile references test_core.py which does not exist;
// (2) this file dumps everything to tb.fst (fine for dev, drop for CAP).
// NEXT: write test_core.py (blink smoke: 20-word program -> uio bit0).
/* This testbench just instantiates the module and makes some convenient wires
   that can be driven / tested by the cocotb test.py.
*/
module tb ();

  // Dump the signals to a FST file. You can view it with gtkwave or surfer.
  initial begin
    $dumpfile("tb.fst");
    $dumpvars(0, tb);
    #1;
  end

  // Wire up the inputs and outputs:
  reg clk;
  reg rst_n;
  reg ena;
  reg [7:0] ui_in;
  reg [7:0] uio_in;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;

  // msfcore: protocol emulator core (see docs/sam/isa.md)
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

endmodule
