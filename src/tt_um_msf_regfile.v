// SPDX-License-Identifier: Apache-2.0
// msfcore register file: 8 x 16-bit, 2 read ports / 1 write port, async reset.
// STATUS: plain leaf block; never compiled (sim not built since reorg).
// Read ports ra/rb -> qa/qb (combinational); write at wa/wd on we.
// Deep reset clears all 8 registers (synchronous-style loop).
// BUDGET: ~350 cells (128 FFs + muxes) per isa.md section 7.5 (~4.3K total).
// INTEGRATION: owned by tt_um_msf_ctrl, which picks port muxes (ra_mux/rb_mux);
// ctrl bug list items (a)/(e) may show as port-select errors here.
// UNIT TEST (M2.2 gate): write/read all regs, verify port independence.
// NEXT: write unit cocotb test; verify async-reset clears all regs.
`default_nettype none

module tt_um_msf_regfile (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [2:0]  ra,
    input  wire [2:0]  rb,
    input  wire        we,
    input  wire [2:0]  wa,
    input  wire [15:0] wd,
    output wire [15:0] qa,
    output wire [15:0] qb
);
  reg [15:0] rf [0:7];

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rf[0] <= 16'h0; rf[1] <= 16'h0; rf[2] <= 16'h0; rf[3] <= 16'h0;
      rf[4] <= 16'h0; rf[5] <= 16'h0; rf[6] <= 16'h0; rf[7] <= 16'h0;
    end else if (we) begin
      rf[wa] <= wd;
    end
  end

  assign qa = rf[ra];
  assign qb = rf[rb];
endmodule
