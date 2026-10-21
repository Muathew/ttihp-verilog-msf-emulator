// SPDX-License-Identifier: Apache-2.0
// FPGA-inferred 1024 x 16 dual-port SRAM (the ASIC's two 512-word banks).
// Port A is a continuously enabled, registered instruction read. Port B is
// an enabled registered data read or a full-word write. Read outputs hold
// their previous value when their port is not reading.
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

  reg [15:0] mem [0:1023];
  reg [15:0] rd_r, b_rd_r;

  integer i;
  initial begin
    // Vivado maps this initialization into the BRAM INIT attributes.
    for (i = 0; i < 1024; i = i + 1)
      mem[i] = 16'h0000;
    rd_r   = 16'h0;
    b_rd_r = 16'h0;
  end

  // Keep the independent synchronous ports in separate processes for BRAM
  // inference. Same-address read/write collision behavior is device-defined.
  always @(posedge clk) begin
    rd_r <= mem[raddr];
  end

  always @(posedge clk) begin
    if (b_en && b_we) mem[b_addr] <= b_wd;
    if (b_en && !b_we) b_rd_r <= mem[b_addr];
  end

  assign rd   = rd_r;
  assign b_rd = b_rd_r;

  wire _unused = &{rst_n, 1'b0};
endmodule
