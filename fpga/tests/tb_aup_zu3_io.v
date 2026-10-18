// SPDX-License-Identifier: Apache-2.0
`timescale 1ns/1ps

module tb_aup_zu3_io;
  reg core_clk = 1'b0;
  reg ps_clk = 1'b0;
  reg core_resetn = 1'b0;
  reg [10:0] ps_gpio_o = 11'd0;
  reg [10:0] ps_gpio_t = 11'h7ff;
  wire [7:0] ps_status_i;
  reg [7:0] PL_USER_SW = 8'ha5;
  reg [3:0] PL_USER_PB = 4'hf;
  wire [7:0] PL_USER_LED;
  wire [7:0] JA_tri_io;
  reg [7:0] pmod_drive = 8'h5a;

  always #41.667 core_clk = ~core_clk;
  always #5 ps_clk = ~ps_clk;
  assign JA_tri_io = pmod_drive;

  aup_zu3_io dut (
      .core_clk(core_clk), .ps_clk(ps_clk), .core_resetn(core_resetn),
      .ps_gpio_o(ps_gpio_o), .ps_gpio_t(ps_gpio_t),
      .ps_status_i(ps_status_i),
      .PL_USER_SW(PL_USER_SW), .PL_USER_PB(PL_USER_PB),
      .PL_USER_LED(PL_USER_LED), .JA_tri_io(JA_tri_io)
  );

  task check(input condition, input [8*40-1:0] label);
    begin
      if (condition !== 1'b1) begin
        $display("FAIL %0s", label);
        $fatal(1);
      end
    end
  endtask

  initial begin
    #100;
    check(JA_tri_io === pmod_drive, "PMOD input is not driven during reset");

    core_resetn = 1'b1;
    ps_gpio_o = 11'h702; // data=02, PS owns the pins, reset released, strobe high
    ps_gpio_t = 11'h000;
    repeat (8) @(posedge core_clk);
    #1;
    check(dut.u_core.ui_sync === 8'h02, "PS parallel-mode input reaches the core");
    check(dut.u_core.uio_sync[1] === 1'b1, "PS strobe reaches the core");
    check(JA_tri_io === pmod_drive, "PMOD is isolated while PS owns the pins");

    ps_gpio_o[9] = 1'b0;
    repeat (8) @(posedge core_clk);
    ps_gpio_o[8] = 1'b0;
    repeat (8) @(posedge core_clk);
    #1;
    check(dut.u_core.ui_sync === PL_USER_SW, "switches reconnect after loading");
    check(dut.u_core.uio_sync === pmod_drive, "PMOD inputs reconnect after loading");

    ps_gpio_o[9] = 1'b1;
    repeat (8) @(posedge core_clk);
    #1;
    check(dut.rst_n === 1'b1, "PS release is synchronized");
    PL_USER_PB[0] = 1'b0;
    #1;
    check(dut.rst_n === 1'b0, "active-low button asserts core reset");
    PL_USER_PB[0] = 1'b1;
    repeat (3) @(posedge core_clk);
    #1;
    check(dut.rst_n === 1'b1, "button reset release is synchronized");

    repeat (8) @(posedge core_clk);
    repeat (3) @(posedge ps_clk);
    #1;
    check(ps_status_i === dut.uo_out, "status is synchronized to the PS clock");

    $display("PASS: AUP-ZU3 GPIO ownership, reset and status handoff");
    $finish;
  end
endmodule
