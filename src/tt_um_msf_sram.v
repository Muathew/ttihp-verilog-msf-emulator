// SPDX-License-Identifier: Apache-2.0
// msfcore SRAM: IHP RM_IHPSG13_2P_512x16 two-port macro.
//   Port A: read-only, instruction fetch (fetch of words 0-127 is served by the
//           boot ROM instead; tt_um_msf selects with a registered bank bit).
//   Port B: data port for LD / ST / MEM_W (all 512 words; 0-127 = data RAM).
// Both ports have a registered read (address at edge N, data during N+1) and,
// like the real macro, DOUT holds its value while the port is not reading.
// The non-SYNTHESIS branch is the RTL simulation model (`mem` is back-door
// loadable from cocotb as user_project.u_sram.mem).
`default_nettype none

module tt_um_msf_sram (
    input  wire        clk,
    input  wire        rst_n,
    // port A: fetch
    input  wire [8:0]  raddr,
    output wire [15:0] rd,
    // port B: data
    input  wire        b_en,
    input  wire        b_we,
    input  wire [8:0]  b_addr,
    input  wire [15:0] b_wd,
    output wire [15:0] b_rd
);

`ifdef SYNTHESIS
  RM_IHPSG13_2P_512x16_c2_bm_bist sram (
      .A_CLK      (clk),
      .A_MEN      (1'b1),
      .A_WEN      (1'b0),
      .A_REN      (1'b1),
      .A_ADDR     (raddr),
      .A_DIN      (16'h0),
      .A_DLY      (1'b1),          // must be tied high (IHP macro requirement)
      .A_DOUT     (rd),
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
      .B_MEN      (b_en),
      .B_WEN      (b_en & b_we),
      .B_REN      (b_en & ~b_we),
      .B_ADDR     (b_addr),
      .B_DIN      (b_wd),
      .B_DLY      (1'b1),          // must be tied high (IHP macro requirement)
      .B_DOUT     (b_rd),
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
  reg [15:0] mem [0:511];
  reg [15:0] rd_r, b_rd_r;

  integer i;
  initial begin
    for (i = 0; i < 512; i = i + 1) mem[i] = 16'h0;
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
`endif

  wire _unused = &{rst_n, 1'b0};
endmodule
