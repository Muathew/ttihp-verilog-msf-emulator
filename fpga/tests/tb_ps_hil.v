// SPDX-License-Identifier: Apache-2.0
// cocotb top for fpga/tests/test_ps_hil.py: the AUP-ZU3 wrapper (core and
// step logic) with its PS EMIO GPIO driven by the C test code.
`default_nettype none
`timescale 1ns / 1ps

module tb_ps_hil ();
  reg         core_clk = 1'b0;
  reg         core_resetn = 1'b0;
  reg  [31:0] ps_gpio_o = 32'h0;
  reg  [31:0] ps_gpio_t = 32'hffffffff;
  wire [31:0] ps_gpio_i;
  reg  [7:0]  PL_USER_SW = 8'h00;
  reg  [3:0]  PL_USER_PB = 4'h0;     // not pressed (the board's buttons are active high)
  wire [7:0]  PL_USER_LED;
  tri0 [7:0]  JA_tri_io;             // nothing plugged into PMOD JA

  aup_zu3_io dut (
      .core_clk(core_clk), .core_resetn(core_resetn),
      .ps_gpio_o(ps_gpio_o), .ps_gpio_t(ps_gpio_t), .ps_gpio_i(ps_gpio_i),
      .PL_USER_SW(PL_USER_SW), .PL_USER_PB(PL_USER_PB),
      .PL_USER_LED(PL_USER_LED), .JA_tri_io(JA_tri_io)
  );
endmodule
