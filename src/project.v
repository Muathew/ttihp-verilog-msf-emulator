// SPDX-License-Identifier: Apache-2.0
// msfcore protocol emulator -- Tiny Tapeout top level (ISA v2.0).
//
//   ui_in / uio_in --2FF sync--> ctrl (2 hardware threads) --> pio --> uo_out / uio_out / uio_oe
//   IMEM fetch: boot ROM (0-127) or SRAM port A (128-1023), registered bank select
//   SRAM port B: data (LD / ST / MEM_W / FIFO, all 1024 words)
//   peripheral bus: serdes (TX/RX engines), crc (CRC unit)
`default_nettype none

module tt_um_msf (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  // ---- input synchronisers ----
  // Every pad input crosses into the clk domain through two flip-flops.
  // No reset on purpose: they keep tracking the pads while rst_n is low,
  // so the boot ROM's first IN_RD already sees the real ui_in value.
  reg [7:0] ui_s1, ui_sync, uio_s1, uio_sync;
  always @(posedge clk) begin
    ui_s1    <= ui_in;
    ui_sync  <= ui_s1;
    uio_s1   <= uio_in;
    uio_sync <= uio_s1;
  end

  // ---- debug view (isa.md §8) ----
  // ui_in[2] high while rst_n is low selects debug mode until the next reset:
  // uo_out then shows the byte of internal state chosen by ui_in[7:2]
  // instead of the firmware's UO register. Loaded only during reset, so no
  // reset of its own. The select is the raw pad value: it only steers a
  // multiplexer onto the outputs (no flip-flop samples it), and it must act
  // without a clock edge so a host can read every view between single steps.
  reg dbg_mode;
  always @(posedge clk)
    if (!rst_n) dbg_mode <= ui_sync[2];
  wire [7:0] uo_fw, dbg_q;
  assign uo_out = dbg_mode ? dbg_q : uo_fw;

  // ---- instruction memory: boot ROM (0-127) + SRAM (128-1023) ----
  wire [9:0]  imem_raddr;
  wire [15:0] rom_rd, sram_rd;
  wire        b_en, b_we;               // the core's port B request
  wire [9:0]  b_addr;
  wire [15:0] b_wd, b_rd;
  // capture engine: writes port B only in cycles the core leaves it free
  wire        cap_req;
  wire [9:0]  cap_addr;
  wire [15:0] cap_wd;
  wire        sb_en   = b_en | cap_req;
  wire        sb_we   = b_en ? b_we : 1'b1;
  wire [9:0]  sb_addr = b_en ? b_addr : cap_addr;
  wire [15:0] sb_wd   = b_en ? b_wd : cap_wd;

  tt_um_msf_bootrom u_rom (
      .clk(clk), .rst_n(rst_n),
      .a(imem_raddr[6:0]),
      .q(rom_rd)
  );

  tt_um_msf_sram u_sram (
      .clk(clk), .rst_n(rst_n),
      .raddr(imem_raddr), .rd(sram_rd),
      .b_en(sb_en), .b_we(sb_we), .b_addr(sb_addr), .b_wd(sb_wd), .b_rd(b_rd)
  );

  // Both memories have a registered read, so the bank select must be
  // registered too: it has to describe the address the data came from.
  reg imem_sel_sram;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) imem_sel_sram <= 1'b0;
    else        imem_sel_sram <= (imem_raddr[9:7] != 3'b000);
  wire [15:0] imem_rd = imem_sel_sram ? sram_rd : rom_rd;

  // ---- core ----
  wire [7:0]  out_q, oe_q, uo_q, out_d, oe_d, uo_d;
  wire        out_we, oe_we, uo_we;
  wire        per_we, rx_pop;
  wire [4:0]  per_sub;
  wire [15:0] per_wd;
  wire        tx_full, tx_busy, rx_avail, rx_overrun, crc_busy;
  wire [15:0] rx_word, crc_lo, crc_hi;
  wire [7:0]  tx_mask, tx_val;
  wire        pw_we;
  wire [2:0]  pw_port;
  wire [15:0] pw_data, cap_stat;
  wire [9:0]  cap_tpos;

  tt_um_msf_ctrl u_ctrl (
      .clk(clk), .rst_n(rst_n),
      .uio_in(uio_sync), .ui_in(ui_sync),
      .imem_raddr(imem_raddr), .imem_rd(imem_rd),
      .b_en(b_en), .b_we(b_we), .b_addr(b_addr), .b_wd(b_wd), .b_rd(b_rd),
      .out_q(out_q), .oe_q(oe_q), .uo_q(uo_q),
      .out_we(out_we), .out_d(out_d), .oe_we(oe_we), .oe_d(oe_d),
      .uo_we(uo_we), .uo_d(uo_d),
      .per_we(per_we), .per_sub(per_sub), .per_wd(per_wd), .rx_pop(rx_pop),
      .tx_full(tx_full), .tx_busy(tx_busy), .rx_avail(rx_avail),
      .rx_overrun(rx_overrun), .rx_word(rx_word),
      .crc_busy(crc_busy), .crc_lo(crc_lo), .crc_hi(crc_hi),
      .pw_we(pw_we), .pw_port(pw_port), .pw_data(pw_data),
      .cap_stat(cap_stat), .cap_tpos(cap_tpos),
      .dbg_sel(ui_in[7:2]), .dbg_q(dbg_q)
  );

  // ---- capture engine (logic analyser) ----
  tt_um_msf_cap u_cap (
      .clk(clk), .rst_n(rst_n),
      .uio_in(uio_sync), .ui_in(ui_sync),
      .pw_we(pw_we), .pw_port(pw_port), .pw_data(pw_data),
      .core_b(b_en), .req(cap_req), .addr(cap_addr), .wd(cap_wd),
      .stat(cap_stat), .tpos(cap_tpos)
  );

  // ---- pins ----
  tt_um_msf_pio u_pio (
      .clk(clk), .rst_n(rst_n),
      .out_we(out_we), .out_d(out_d), .oe_we(oe_we), .oe_d(oe_d),
      .uo_we(uo_we), .uo_d(uo_d),
      .tx_mask(tx_mask), .tx_val(tx_val),
      .out_q(out_q), .oe_q(oe_q), .uo_q(uo_q),
      .uio_out(uio_out), .uio_oe(uio_oe), .uo_out(uo_fw)
  );

  // ---- peripherals ----
  tt_um_msf_serdes u_serdes (
      .clk(clk), .rst_n(rst_n),
      .per_we(per_we), .per_sub(per_sub), .per_wd(per_wd), .rx_pop(rx_pop),
      .uio_in(uio_sync),
      .tx_full(tx_full), .tx_busy(tx_busy), .tx_mask(tx_mask), .tx_val(tx_val),
      .rx_avail(rx_avail), .rx_overrun(rx_overrun), .rx_word(rx_word)
  );

  tt_um_msf_crc u_crc (
      .clk(clk), .rst_n(rst_n),
      .per_we(per_we), .per_sub(per_sub), .per_wd(per_wd),
      .busy(crc_busy), .crc_lo(crc_lo), .crc_hi(crc_hi)
  );

  wire _unused = &{ena, 1'b0};

`ifdef FORMAL
  // F7 (B3): the word delivered to the core comes from the bank (ROM or
  // SRAM) of the address it was fetched with in the previous cycle.
  reg f_init = 1'b1;
  reg [9:0] f_faddr;
  always @(posedge clk) begin
    f_init  <= 1'b0;
    f_faddr <= imem_raddr;
  end
  always @(*) assume(rst_n == !f_init);
  always @(*) if (!f_init) assert(imem_rd == ((f_faddr[9:7] != 3'b000) ? sram_rd : rom_rd));
  // F9: the capture engine never takes SRAM port B from the core
  always @(*) if (!f_init) assert(!(b_en && cap_req));
`endif
endmodule
