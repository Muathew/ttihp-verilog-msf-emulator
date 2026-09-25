// SPDX-License-Identifier: Apache-2.0
// msfcore firmware SRAM: IMEM words 128..511 (384 entries; addr[8:2] index).
// Synchronous write; REGISTERED read (addr at edge N, data N+1) -- mirrors the
// TT SRAM macro; the 1-cycle fetch latency is handled by ctrl's 2-stage pipe.
// STATUS: never compiled. Reset zeroes all 384 entries via a for-loop in the
// async-reset block -- functional in sim, not synthesis-validated.
// BUDGET: ~1.5K as a macro; FF build costs ~8K extra (both < 18K gate).
// INTEGRATION: tt_um_msf.v wires we/waddr/wd from ctrl's MEM_W path and
// raddr from the fetch PC; read data muxed with bootrom -> imem_rd.
// NEXT: full-coverage unit test + check reset-zeroing time budget in sim.
`default_nettype none

module tt_um_msf_sram (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        we,
    input  wire [8:0]  waddr,   // 9-bit (wraps 128..511)
    input  wire [15:0] wd,
    input  wire [8:0]  raddr,
    output wire [15:0] rd
);
  reg [15:0] mem [0:383];
  reg [15:0] rd_r;
  integer i;

  wire [8:0] sram_ridx = (raddr >= 9'd128) ? (raddr - 9'd128) : 9'd0;
  wire [8:0] sram_widx = (waddr >= 9'd128) ? (waddr - 9'd128) : 9'd0;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rd_r <= 16'h0;
    end else begin
      if (we) begin
        mem[sram_widx] <= wd;
      end
      rd_r <= mem[sram_ridx];
    end
  end

  assign rd = rd_r;
endmodule
