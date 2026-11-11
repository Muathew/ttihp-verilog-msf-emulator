`default_nettype none
`timescale 1ns / 1ps
// msfcore testbench shim: instantiates tt_um_msf; the cocotb tests drive
// clk/rst_n/ui_in/uio_in and read the outputs (and, in RTL sim, internal
// signals) through dut.user_project.*. Waves go to tb.fst.
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

  // msfcore: protocol emulator core (see docs/isa.md)
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

`ifndef GL_TEST
  // Toggles when a WAIT_CMP issues, so that DeadlineWatch (fw_tb.py) only wakes
  // for those cycles.
  reg wcmp_evt = 1'b0;
  always @(negedge clk)
    if (user_project.u_ctrl.exec && user_project.u_ctrl.op == 5'h1B)
      wcmp_evt <= !wcmp_evt;

  // Store-ahead detector (docs/isa.md section 1, plan-v4 B1): toggles when a word
  // issues one or two cycles after a store (ST, MEM_W, FIFO_PUT) wrote it, the rule the
  // ISA model refuses (StoreAheadError). The word then runs its old content on this
  // RTL, and silicon does not define a fetch in the cycle of the write. Words below 128
  // come from the boot ROM. msf_tb.start_clock starts the watcher that fails the test.
  reg        sa_evt = 1'b0;
  reg        sa_v1 = 1'b0, sa_v2 = 1'b0;
  reg [10:0] sa_a1 = 11'd0, sa_a2 = 11'd0;
  wire [10:0] sa_pc = user_project.u_ctrl.dbg_pc;
  always @(negedge clk) begin
    if (user_project.u_ctrl.exec && sa_pc >= 11'd128 &&
        ((sa_v1 && sa_a1 == sa_pc) || (sa_v2 && sa_a2 == sa_pc)))
      sa_evt <= !sa_evt;
    sa_v2 <= sa_v1;
    sa_a2 <= sa_a1;
    sa_v1 <= user_project.u_ctrl.b_en && user_project.u_ctrl.b_we;
    sa_a1 <= user_project.u_ctrl.b_addr;
  end
`endif

endmodule
