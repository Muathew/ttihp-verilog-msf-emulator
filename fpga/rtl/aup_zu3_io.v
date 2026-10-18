// SPDX-License-Identifier: Apache-2.0
// AUP-ZU3 board I/O, PS-to-core programming handoff and PS-stepped test mode.
//
// PS EMIO GPIO bank 3 (EMIO 0-31); keep in sync with fpga/ps/common/msf_emio.h.
// Outputs count only while the PS drives them (ps_gpio_t = 0):
//   [7:0]   UI    ui_in while the PS owns the pins
//   [8]     OWN   PS owns ui_in/uio_in; PMOD JA is released (high-Z)
//   [9]     RUN   core reset release
//   [10]    STB   parallel-boot strobe, ORed into uio_in[1] while the PS owns the pins
//   [11]    STEP  step mode: the core clock only runs on step requests
//   [12]    REQ   step request: every toggle runs exactly one core clock cycle
//   [20:13] UIO   uio_in while the PS owns the pins
// Inputs:
//   [10:3]  uo_out    [19:12] uio_out    [27:20] uio_oe
//   [11]    ACK   = REQ once the requested cycle has run and [27:3] show its result
//   [28]    core rst_n    [29] step mode active
// In free-run mode [27:3] follow the pins every clock and ACK follows REQ.
`default_nettype none

module aup_zu3_io #(
    parameter BUTTON_ACTIVE_LOW = 1
) (
    input  wire        core_clk,     // pl_clk0, free running
    input  wire        core_resetn,
    input  wire [31:0] ps_gpio_o,
    input  wire [31:0] ps_gpio_t,
    output wire [31:0] ps_gpio_i,
    input  wire [7:0]  PL_USER_SW,
    input  wire [3:0]  PL_USER_PB,
    output wire [7:0]  PL_USER_LED,
    inout  wire [7:0]  JA_tri_io
);
  wire [31:0] ps_drive = ps_gpio_o & ~ps_gpio_t;
  wire ps_own  = ps_drive[8];
  wire ps_run  = ps_drive[9];
  wire ps_stb  = ps_drive[10];
  wire ps_step = ps_drive[11];
  wire ps_req  = ps_drive[12];

  wire [7:0] ui_in  = ps_own ? ps_drive[7:0] : PL_USER_SW;
  wire [7:0] uio_in = ps_own ? (ps_drive[20:13] | {6'b0, ps_stb, 1'b0}) : JA_tri_io;
  wire [7:0] uo_out, uio_out, uio_oe;
  wire button_resetn = BUTTON_ACTIVE_LOW ? PL_USER_PB[0] : !PL_USER_PB[0];

  (* ASYNC_REG = "TRUE" *) reg [1:0] run_sync, button_sync, step_sync;
  (* ASYNC_REG = "TRUE" *) reg [2:0] req_sync;

  always @(posedge core_clk or negedge core_resetn) begin
    if (!core_resetn) begin
      run_sync    <= 2'b00;
      button_sync <= 2'b00;
      step_sync   <= 2'b00;
      req_sync    <= 3'b000;
    end else begin
      run_sync    <= {run_sync[0], ps_run};
      button_sync <= {button_sync[0], button_resetn};
      step_sync   <= {step_sync[0], ps_step};
      req_sync    <= {req_sync[1:0], ps_req};
    end
  end

  wire rst_n = core_resetn & run_sync[1] & button_sync[1] & button_resetn;
  wire step_mode = step_sync[1];
  wire req_edge  = req_sync[2] ^ req_sync[1];

  // Core clock enable: always 1 in free run; in step mode 1 for exactly one
  // core_clk cycle per REQ toggle, so BUFGCE passes exactly one rising edge.
  reg ce = 1'b1;
  always @(posedge core_clk or negedge core_resetn)
    if (!core_resetn) ce <= 1'b1;
    else              ce <= !step_mode || req_edge;

  wire core_gclk;
  BUFGCE u_core_bufg (.I(core_clk), .CE(ce), .O(core_gclk));

  // Step result: the gated edge happens at the edge that sees ce = 1 (ran),
  // the pins are captured one clock later and ACK flips one clock after
  // that, so the PS never sees ACK before the captured pins are stable.
  reg ran, settled, ack, rst_q;
  reg [23:0] snap;   // {uio_oe, uio_out, uo_out}
  always @(posedge core_clk or negedge core_resetn) begin
    if (!core_resetn) begin
      ran     <= 1'b0;
      settled <= 1'b0;
      ack     <= 1'b0;
      rst_q   <= 1'b0;
      snap    <= 24'h0;
    end else begin
      ran     <= step_mode && ce;
      settled <= ran;
      rst_q   <= rst_n;
      if (!step_mode || ran)     snap <= {uio_oe, uio_out, uo_out};
      if (!step_mode || settled) ack  <= req_sync[2];
    end
  end

  assign ps_gpio_i = {2'b00, step_mode, rst_q, snap[23:8], ack, snap[7:0], 3'b000};
  assign PL_USER_LED = uo_out;

  genvar pin;
  generate
    for (pin = 0; pin < 8; pin = pin + 1) begin : gen_pmod
      assign JA_tri_io[pin] = (!ps_own && uio_oe[pin]) ? uio_out[pin] : 1'bz;
    end
  endgenerate

  tt_um_msf u_core (
      .ui_in(ui_in),
      .uo_out(uo_out),
      .uio_in(uio_in),
      .uio_out(uio_out),
      .uio_oe(uio_oe),
      .ena(1'b1),
      .clk(core_gclk),
      .rst_n(rst_n)
  );

endmodule
