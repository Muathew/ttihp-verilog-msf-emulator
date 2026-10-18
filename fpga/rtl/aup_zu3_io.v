// SPDX-License-Identifier: Apache-2.0
// AUP-ZU3 board I/O and PS-to-core programming handoff.
`default_nettype none

module aup_zu3_io #(
    parameter BUTTON_ACTIVE_LOW = 1
) (
    input  wire        core_clk,
    input  wire        ps_clk,
    input  wire        core_resetn,
    input  wire [10:0] ps_gpio_o,
    input  wire [10:0] ps_gpio_t,
    output wire [7:0]  ps_status_i,
    input  wire [7:0]  PL_USER_SW,
    input  wire [3:0]  PL_USER_PB,
    output wire [7:0]  PL_USER_LED,
    inout  wire [7:0]  JA_tri_io
);
  wire ps_loader_active = !ps_gpio_t[8] && ps_gpio_o[8];
  wire ps_reset_release = !ps_gpio_t[9] && ps_gpio_o[9];
  wire [7:0] ui_in = ps_loader_active ? ps_gpio_o[7:0] : PL_USER_SW;
  wire [7:0] uio_in;
  wire [7:0] uo_out, uio_out, uio_oe;
  wire button_resetn = BUTTON_ACTIVE_LOW ? PL_USER_PB[0] : !PL_USER_PB[0];

  reg [1:0] ps_reset_sync, button_reset_sync;
  (* ASYNC_REG = "TRUE" *) reg [7:0] status_sync1, status_sync2;

  always @(posedge ps_clk) begin
    status_sync1 <= uo_out;
    status_sync2 <= status_sync1;
  end

  always @(posedge core_clk or negedge core_resetn) begin
    if (!core_resetn) begin
      ps_reset_sync <= 2'b00;
      button_reset_sync <= 2'b00;
    end else begin
      ps_reset_sync <= {ps_reset_sync[0], ps_reset_release};
      button_reset_sync <= {button_reset_sync[0], button_resetn};
    end
  end

  wire rst_n = core_resetn & ps_reset_sync[1] &
               button_reset_sync[1] & button_resetn;

  assign uio_in = ps_loader_active
      ? {6'b0, (!ps_gpio_t[10] && ps_gpio_o[10]), 1'b0}
      : JA_tri_io;
  assign ps_status_i = status_sync2;
  assign PL_USER_LED = uo_out;

  genvar pin;
  generate
    for (pin = 0; pin < 8; pin = pin + 1) begin : gen_pmod
      assign JA_tri_io[pin] =
          (!ps_loader_active && uio_oe[pin]) ? uio_out[pin] : 1'bz;
    end
  endgenerate

  tt_um_msf u_core (
      .ui_in(ui_in),
      .uo_out(uo_out),
      .uio_in(uio_in),
      .uio_out(uio_out),
      .uio_oe(uio_oe),
      .ena(1'b1),
      .clk(core_clk),
      .rst_n(rst_n)
  );

endmodule
