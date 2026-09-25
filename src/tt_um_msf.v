// SPDX-License-Identifier: Apache-2.0
// msfcore (Protocol Emulator) -- top-level Tiny Tapeout wrapper.
// STATUS: wired, UNVERIFIED. Instantiates ctrl + pio + bootrom + sram and
// plumbs Tiny Tapeout pins (ui_in/uio_in -> in; uio_out/uo_out -> out).
// IMEM = bootrom (0-127) + SRAM (128-511), muxed by fetch addr bit 7; only
// the SRAM has a write port (MEM_W). All 7 src files are in the Makefile.
// BUDGET: ~4.3K cells estimated (isa.md 7.5) vs 18K gate -- plenty of headroom.
// INTEGRATION: `make -B` in test/ compiles this via tb.v (TOPLEVEL = tb).
// OPEN BUGS: ctrl pipeline not smoke-tested; make -B not green since the
// rewrite; branch/jmp fetch alignment with the registered read unverified.
// NEXT: get a green build, blink smoke in test/test_core.py, then commit.
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
  // ---- IMEM: bootrom (words 0-127) + sram (words 128-511) ----
  wire [8:0]  imem_raddr;
  wire [15:0] rom_rd, sram_rd;
  wire        sram_we;
  wire [8:0]  sram_waddr;
  wire [15:0] sram_wd;

  tt_um_msf_bootrom u_rom (
      .clk(clk), .rst_n(rst_n),
      .a(imem_raddr[6:0]),
      .q(rom_rd)
  );

  tt_um_msf_sram u_sram (
      .clk(clk), .rst_n(rst_n),
      .we(sram_we),
      .waddr(sram_waddr),
      .wd(sram_wd),
      .raddr(imem_raddr),
      .rd(sram_rd)
  );

  wire [15:0] imem_rd = (imem_raddr >= 9'd128) ? sram_rd : rom_rd;

  // ---- control ----
  wire [7:0]  pin_w;
  wire        pin_w_en;
  wire [7:0]  oe_set, oe_clr;
  wire        oe_set_en, oe_clr_en;
  wire [7:0]  uo_w;
  wire        uo_w_en;
  wire        serial_out;
  wire        rf_we;
  wire [2:0]  rf_waddr;
  wire [15:0] rf_wdata;
  wire [2:0]  rf_ra, rf_rb;

  tt_um_msf_ctrl u_ctrl (
      .clk(clk), .rst_n(rst_n),
      .uio_in(uio_in),
      .ui_in(ui_in),
      .imem_rd(imem_rd),
      .imem_raddr(imem_raddr),
      .sram_we(sram_we),
      .sram_waddr(sram_waddr),
      .sram_wd(sram_wd),
      .pin_w(pin_w),
      .pin_w_en(pin_w_en),
      .oe_set(oe_set),
      .oe_clr(oe_clr),
      .oe_set_en(oe_set_en),
      .oe_clr_en(oe_clr_en),
      .uo_w(uo_w),
      .uo_w_en(uo_w_en),
      .serial_out(serial_out),
      .rf_we(rf_we),
      .rf_waddr(rf_waddr),
      .rf_wdata(rf_wdata),
      .rf_ra(rf_ra),
      .rf_rb(rf_rb)
  );

  // ---- pin I/O ----
  tt_um_msf_pio u_pio (
      .clk(clk), .rst_n(rst_n),
      .pin_w(pin_w),
      .pin_w_en(pin_w_en),
      .oe_set(oe_set),
      .oe_clr(oe_clr),
      .oe_set_en(oe_set_en),
      .oe_clr_en(oe_clr_en),
      .uo_w(uo_w),
      .uo_w_en(uo_w_en),
      .serial_out(serial_out),
      .uio_out(uio_out),
      .uio_oe(uio_oe),
      .uo_out(uo_out)
  );

  // ---- unused inputs ----
  wire _unused = &{ena, 1'b0};
endmodule
