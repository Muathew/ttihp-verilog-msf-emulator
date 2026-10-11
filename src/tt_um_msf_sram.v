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
    // write ports
    input  wire        we,
    input  wire [8:0]  waddr,   // 9-bit (wraps 128..511)
    input  wire [15:0] wd,
    // read ports
    input  wire [8:0]  raddr,
    output wire [15:0] rd
);

/*
  IHP SG13G2 2-port SRAM
  512 words x 16 bits

  Port A: read-only (for instruction fetch)
  Port B: write-only (for MEM_W path)

  Memory map is:
  0-127 -> boot ROM
  128-511 -> SRAM (384 words)

  The SRAM uses the full 9-bit address, no address subtraction is needed.
*/


`ifdef SYNTHESIS
RM_IHPSG13_2P_512x16_c2_bm_bist sram (
  // ---------- Port A : read-only (instruction fetch) ----------
  .A_CLK      (clk),
  .A_MEN      (1'b1),          // memory enable (active high)
  .A_WEN      (1'b0),          // write enable (0 = read)
  .A_REN      (1'b1),          // read enable
  .A_ADDR     (raddr),
  .A_DIN      (16'h0),
  .A_DLY      (1'b1),          // delay control – tie low for normal use
  .A_DOUT     (rd),
  .A_BM       (16'hFFFF),      // bit mask (all bits enabled)

  // BIST ports A – tie off
  .A_BIST_CLK (1'b0),
  .A_BIST_EN  (1'b0),
  .A_BIST_MEN (1'b0),
  .A_BIST_WEN (1'b0),
  .A_BIST_REN (1'b0),
  .A_BIST_ADDR(9'h0),
  .A_BIST_DIN (16'h0),
  .A_BIST_BM  (16'h0),

  // ---------- Port B : write-only ----------
  .B_CLK      (clk),
  .B_MEN      (we),            // enable when writing
  .B_WEN      (we),            // write enable
  .B_REN      (1'b0),          // no read on this port
  .B_ADDR     (waddr),
  .B_DIN      (wd),
  .B_DLY      (1'b1),
  .B_DOUT     (),              // unused
  .B_BM       (16'hFFFF),

  // BIST ports B – tie off
  .B_BIST_CLK (1'b0),
  .B_BIST_EN  (1'b0),
  .B_BIST_MEN (1'b0),
  .B_BIST_WEN (1'b0),
  .B_BIST_REN (1'b0),
  .B_BIST_ADDR(9'h0),
  .B_BIST_DIN (16'h0),
  .B_BIST_BM  (16'h0)
);
`else
  reg [15:0] mem [0:511];
  reg [15:0] rd_r;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rd_r <= 16'h0;
    end else begin
      if (we) begin
        mem[waddr] <= wd;
      end
      rd_r <= mem[raddr];
    end
  end

  assign rd = rd_r;
`endif

endmodule
