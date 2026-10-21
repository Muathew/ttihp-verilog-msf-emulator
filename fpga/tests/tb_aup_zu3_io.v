// SPDX-License-Identifier: Apache-2.0
`timescale 1ns/1ps

module tb_aup_zu3_io;
  reg core_clk = 1'b0;
  reg core_resetn = 1'b0;
  reg [31:0] ps_gpio_o = 32'd0;
  reg [31:0] ps_gpio_t = 32'hffffffff;
  wire [31:0] ps_gpio_i;
  reg [7:0] PL_USER_SW = 8'ha5;
  reg [3:0] PL_USER_PB = 4'h0;      // released (active-high buttons)
  wire [7:0] PL_USER_LED;
  wire [7:0] JA_tri_io;
  reg [7:0] pmod_drive = 8'h5a;

  localparam OWN = 8, RUN = 9, STB = 10, STEP = 11, REQ = 12, UIO = 13;
  localparam ACK = 11, RSTN = 28, MODE = 29;

  always #41.667 core_clk = ~core_clk;
  assign JA_tri_io = pmod_drive;

  aup_zu3_io dut (
      .core_clk(core_clk), .core_resetn(core_resetn),
      .ps_gpio_o(ps_gpio_o), .ps_gpio_t(ps_gpio_t), .ps_gpio_i(ps_gpio_i),
      .PL_USER_SW(PL_USER_SW), .PL_USER_PB(PL_USER_PB),
      .PL_USER_LED(PL_USER_LED), .JA_tri_io(JA_tri_io)
  );

  integer gedges = 0;
  always @(posedge dut.core_gclk) gedges = gedges + 1;

  task check(input condition, input [8*48-1:0] label);
    begin
      if (condition !== 1'b1) begin
        $display("FAIL %0s", label);
        $fatal(1);
      end
    end
  endtask

  // One PS step: toggle REQ (with new uio_in), wait for ACK to match.
  task step(input [7:0] uio);
    integer n;
    begin
      ps_gpio_o[20:13] = uio;
      ps_gpio_o[REQ] = !ps_gpio_o[REQ];
      n = 0;
      while (ps_gpio_i[ACK] !== ps_gpio_o[REQ] && n < 50) begin
        @(posedge core_clk);
        #1;
        n = n + 1;
      end
      check(n < 50, "step request is acknowledged");
    end
  endtask

  integer e0, k;
  initial begin
    #300;   // the core's reset reaches the pins on its first (gated) clock edge
    check(JA_tri_io === pmod_drive, "PMOD input is not driven during reset");

    core_resetn = 1'b1;
    ps_gpio_o = 32'h702; // data=02, PS owns the pins, reset released, strobe high
    ps_gpio_t = 32'hffe00000;
    repeat (8) @(posedge core_clk);
    #1;
    check(dut.u_core.ui_sync === 8'h02, "PS parallel-mode input reaches the core");
    check(dut.u_core.uio_sync[1] === 1'b1, "PS strobe reaches the core");
    check(JA_tri_io === pmod_drive, "PMOD is isolated while PS owns the pins");
    check(ps_gpio_i[RSTN] === 1'b1 && ps_gpio_i[MODE] === 1'b0, "free run, out of reset");

    ps_gpio_o[STB] = 1'b0;
    ps_gpio_o[20:13] = 8'hc3;
    repeat (4) @(posedge core_clk);
    #1;
    check(dut.u_core.uio_sync === 8'hc3, "PS drives every uio_in bit");

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
    PL_USER_PB[0] = 1'b1;
    #1;
    check(dut.rst_n === 1'b0, "pressed (active-high) button asserts core reset");
    PL_USER_PB[0] = 1'b0;
    repeat (3) @(posedge core_clk);
    #1;
    check(dut.rst_n === 1'b1, "button reset release is synchronized");

    repeat (4) @(posedge core_clk);
    #1;
    check(ps_gpio_i[10:3] === dut.uo_out, "uo_out is visible to the PS");
    check(ps_gpio_i[19:12] === dut.uio_out, "uio_out is visible to the PS");
    check(ps_gpio_i[27:20] === dut.uio_oe, "uio_oe is visible to the PS");

    // ---- step mode: the core clock only runs on PS requests ----
    ps_gpio_o[OWN] = 1'b1;
    ps_gpio_o[RUN] = 1'b0;
    ps_gpio_o[7:0] = 8'h01;          // direct boot when released
    ps_gpio_o[STEP] = 1'b1;
    repeat (6) @(posedge core_clk);
    #1;
    check(ps_gpio_i[MODE] === 1'b1 && ps_gpio_i[RSTN] === 1'b0, "step mode entered in reset");
    e0 = gedges;
    repeat (20) @(posedge core_clk);
    check(gedges == e0, "no core clock without a request");

    for (k = 0; k < 3; k = k + 1) step(8'h00);  // clock the synchronisers in reset
    check(gedges == e0 + 3, "each request gives exactly one core edge");
    check(dut.u_core.ui_sync === 8'h01, "synchronisers track while stepped in reset");

    ps_gpio_o[RUN] = 1'b1;
    repeat (4) @(posedge core_clk);
    #1;
    check(ps_gpio_i[RSTN] === 1'b1 && gedges == e0 + 3, "reset release needs no core edge");
    for (k = 0; k < 9; k = k + 1) begin
      step(8'h00);
      check(ps_gpio_i[10:3] === dut.uo_out && ps_gpio_i[27:12] === {dut.uio_oe, dut.uio_out},
            "step result matches the pins");
    end
    check(gedges == e0 + 12, "nine more requests, nine more edges");
    check(dut.u_core.u_ctrl.pc0 >= 9'd128, "direct boot reached SRAM in step mode");

    ps_gpio_o[STEP] = 1'b0;
    e0 = gedges;
    repeat (10) @(posedge core_clk);   // 2-flop sync + enable register first
    #1;
    check(gedges == e0 + 7, "free run resumes");

    $display("PASS: AUP-ZU3 GPIO ownership, reset, status and step-mode handoff");
    $finish;
  end
endmodule
