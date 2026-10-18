// SPDX-License-Identifier: Apache-2.0
`timescale 1ns/1ps

module tb_sram;
  reg clk = 1'b0;
  reg rst_n = 1'b0;
  reg [8:0] raddr = 9'd0;
  wire [15:0] rd;
  reg b_en = 1'b0;
  reg b_we = 1'b0;
  reg [8:0] b_addr = 9'd0;
  reg [15:0] b_wd = 16'd0;
  wire [15:0] b_rd;

  always #5 clk = ~clk;

  tt_um_msf_sram dut (
      .clk(clk), .rst_n(rst_n),
      .raddr(raddr), .rd(rd),
      .b_en(b_en), .b_we(b_we), .b_addr(b_addr), .b_wd(b_wd), .b_rd(b_rd)
  );

  task check(input [15:0] got, input [15:0] expected, input [8*32-1:0] label);
    begin
      if (got !== expected) begin
        $display("FAIL %0s: got %04x, expected %04x", label, got, expected);
        $fatal(1);
      end
    end
  endtask

  initial begin
    #1 rst_n = 1'b1;

    @(negedge clk);
    b_en = 1'b1;
    b_we = 1'b1;
    b_addr = 9'd19;
    b_wd = 16'hcafe;
    @(posedge clk);
    #1;

    @(negedge clk);
    b_we = 1'b0;
    raddr = 9'd19;
    b_addr = 9'd19;
    @(posedge clk);
    #1;
    check(rd, 16'hcafe, "port A registered read");
    check(b_rd, 16'hcafe, "port B registered read");

    @(negedge clk);
    b_en = 1'b0;
    raddr = 9'd20;
    b_addr = 9'd20;
    @(posedge clk);
    #1;
    check(rd, 16'h0000, "port A reads initialized word");
    check(b_rd, 16'hcafe, "disabled port B holds read data");

    @(negedge clk);
    b_en = 1'b1;
    b_we = 1'b1;
    b_addr = 9'd19;
    b_wd = 16'h1234;
    @(posedge clk);
    #1;

    @(negedge clk);
    b_we = 1'b0;
    raddr = 9'd19;
    @(posedge clk);
    #1;
    check(rd, 16'h1234, "port A sees written word");
    check(b_rd, 16'h1234, "port B reads written word");

    $display("PASS: FPGA SRAM latency, write, read and hold semantics");
    $finish;
  end
endmodule
