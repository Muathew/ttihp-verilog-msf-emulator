// SPDX-License-Identifier: Apache-2.0
// msfcore register file: 8 x 16-bit, 2 combinational read ports (+ a third
// for the debug view), 1 write port, async reset. `clr` synchronously zeroes
// every register (used to give a hardware thread a clean register set on
// TSTART); clr wins over we.
// One instance per hardware thread (u_rf = T0, u_rf1 = T1, u_rf2 = T2,
// u_rf3 = T3) in tt_um_msf_ctrl.
`default_nettype none

module tt_um_msf_regfile (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        clr,
    input  wire [2:0]  ra,
    input  wire [2:0]  rb,
    input  wire [2:0]  rc,
    input  wire        we,
    input  wire [2:0]  wa,
    input  wire [15:0] wd,
    output wire [15:0] qa,
    output wire [15:0] qb,
    output wire [15:0] qc
);
  reg [15:0] rf [0:7];
  integer i;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (i = 0; i < 8; i = i + 1) rf[i] <= 16'h0;
    end else if (clr) begin
      for (i = 0; i < 8; i = i + 1) rf[i] <= 16'h0;
    end else if (we) begin
      rf[wa] <= wd;
    end
  end

  assign qa = rf[ra];
  assign qb = rf[rb];
  assign qc = rf[rc];
endmodule
