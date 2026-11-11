// SPDX-License-Identifier: Apache-2.0
// msfcore SRAM: two IHP RM_IHPSG13_2P_512x16 two-port macros, one per bank
// of 512 words (bank = address bit 9), 1024 x 16 in all.
//   Port A: read-only, instruction fetch (fetch of words 0-127 is served by the
//           boot ROM instead; tt_um_msf selects with a registered bank bit).
//   Port B: data port for LD / ST / MEM_W / FIFO (all 1024 words; 0-127 = data RAM).
// Both ports have a registered read (address at edge N, data during N+1) and,
// like the real macro, DOUT holds its value while the port is not reading.
// Each macro is enabled only when its bank is addressed; the read data comes
// from the bank of the last read, held in a register like the ROM/SRAM select.
// The non-SYNTHESIS branch is the RTL simulation model (`mem` is back-door
// loadable from cocotb as user_project.u_sram.mem).
`default_nettype none

module tt_um_msf_sram (
    input  wire        clk,
    input  wire        rst_n,
    // port A: fetch
    input  wire [9:0]  raddr,
    output wire [15:0] rd,
    // port B: data
    input  wire        b_en,
    input  wire        b_we,
    input  wire [9:0]  b_addr,
    input  wire [15:0] b_wd,
    output wire [15:0] b_rd
);

`ifdef SYNTHESIS
  wire [15:0] rd0, rd1, b_rd0, b_rd1;
  reg         a_bank, b_bank;         // bank of the last read on each port
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      a_bank <= 1'b0; b_bank <= 1'b0;
    end else begin
      a_bank <= raddr[9];
      if (b_en && !b_we) b_bank <= b_addr[9];
    end
  end
  assign rd   = a_bank ? rd1 : rd0;
  assign b_rd = b_bank ? b_rd1 : b_rd0;

  // Port A of bank 0 reads only for fetch addresses 128 and up (plan-v4 B5): below that
  // the boot ROM supplies the word, so the read would be unused, and while the
  // loader runs it could meet a port-B write of the same word at one edge.
  wire a_on0 = !raddr[9] && (raddr[8:7] != 2'b00), a_on1 = raddr[9];
  wire b_on0 = b_en && !b_addr[9], b_on1 = b_en && b_addr[9];

  RM_IHPSG13_2P_512x16_c2_bm_bist sram0 (
      .A_CLK      (clk),
      .A_MEN      (a_on0),
      .A_WEN      (1'b0),
      .A_REN      (a_on0),
      .A_ADDR     (raddr[8:0]),
      .A_DIN      (16'h0),
      .A_DLY      (1'b1),          // must be tied high (IHP macro requirement)
      .A_DOUT     (rd0),
      .A_BM       (16'hFFFF),
      .A_BIST_CLK (1'b0),
      .A_BIST_EN  (1'b0),
      .A_BIST_MEN (1'b0),
      .A_BIST_WEN (1'b0),
      .A_BIST_REN (1'b0),
      .A_BIST_ADDR(9'h0),
      .A_BIST_DIN (16'h0),
      .A_BIST_BM  (16'h0),

      .B_CLK      (clk),
      .B_MEN      (b_on0),
      .B_WEN      (b_on0 & b_we),
      .B_REN      (b_on0 & ~b_we),
      .B_ADDR     (b_addr[8:0]),
      .B_DIN      (b_wd),
      .B_DLY      (1'b1),          // must be tied high (IHP macro requirement)
      .B_DOUT     (b_rd0),
      .B_BM       (16'hFFFF),
      .B_BIST_CLK (1'b0),
      .B_BIST_EN  (1'b0),
      .B_BIST_MEN (1'b0),
      .B_BIST_WEN (1'b0),
      .B_BIST_REN (1'b0),
      .B_BIST_ADDR(9'h0),
      .B_BIST_DIN (16'h0),
      .B_BIST_BM  (16'h0)
  );

  RM_IHPSG13_2P_512x16_c2_bm_bist sram1 (
      .A_CLK      (clk),
      .A_MEN      (a_on1),
      .A_WEN      (1'b0),
      .A_REN      (a_on1),
      .A_ADDR     (raddr[8:0]),
      .A_DIN      (16'h0),
      .A_DLY      (1'b1),          // must be tied high (IHP macro requirement)
      .A_DOUT     (rd1),
      .A_BM       (16'hFFFF),
      .A_BIST_CLK (1'b0),
      .A_BIST_EN  (1'b0),
      .A_BIST_MEN (1'b0),
      .A_BIST_WEN (1'b0),
      .A_BIST_REN (1'b0),
      .A_BIST_ADDR(9'h0),
      .A_BIST_DIN (16'h0),
      .A_BIST_BM  (16'h0),

      .B_CLK      (clk),
      .B_MEN      (b_on1),
      .B_WEN      (b_on1 & b_we),
      .B_REN      (b_on1 & ~b_we),
      .B_ADDR     (b_addr[8:0]),
      .B_DIN      (b_wd),
      .B_DLY      (1'b1),          // must be tied high (IHP macro requirement)
      .B_DOUT     (b_rd1),
      .B_BM       (16'hFFFF),
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
  reg [15:0] mem [0:1023];
  reg [15:0] rd_r, b_rd_r;

  integer i;
  initial begin
    for (i = 0; i < 1024; i = i + 1) mem[i] = 16'h0;
    rd_r   = 16'h0;
    b_rd_r = 16'h0;
  end

  always @(posedge clk) begin
    rd_r <= mem[raddr];
    if (b_en && b_we) mem[b_addr] <= b_wd;
    if (b_en && !b_we) b_rd_r <= mem[b_addr];
  end

  assign rd   = rd_r;
  assign b_rd = b_rd_r;

  wire _unused = &{rst_n, 1'b0};
`endif
endmodule
