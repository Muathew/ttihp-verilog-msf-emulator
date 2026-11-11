// SPDX-License-Identifier: Apache-2.0
// msfcore SERDES (isa.md §6.1/§6.2/§6.9): generic serialiser + deserialiser that
// run autonomously at a programmable symbol rate while firmware feeds them.
// They implement line codings, not protocols -- framing (start/stop bits,
// SYNC, EOP, preambles, CRC) stays in firmware.
//
// TX: NRZ / NRZI / Manchester, optional bit stuffing, optional differential
//     pin pair, 1..16-bit words, MSB- or LSB-first, double buffered (gapless),
//     optional release mode (the engine owns its pins only while a symbol is
//     on the line -- exact USB SYNC/EOP and Ethernet idle/TP_IDL timing).
// v4 (docs/plan-v4.md section 6.5): the engines work on all 24 pins. RX_CFG bit 3 selects the
// `ui` inputs instead of `uio` (so a monitor needs no bidirectional pin), TX_CFG bit 15 makes
// the TX engine drive `uo` instead of `uio` (its pin and, with `diff`, the next one, modulo 8).
// One instance is one engine set; the core has two (ESEL bit 0, docs/isa.md section 3.2).
// Engine options (isa.md 6.9, port page 2): general stuffing (n equal bits, either kind),
// error flags (frame, stuff, underrun), and an external clock: the engine shifts on edges of
// a pin, gated by another, instead of its divider (a synchronous slave, word by word).
// RX: NRZ / NRZI, optional de-stuffing, optional edge resync, continuous
//     (start at first transition) or async-character (falling-edge start
//     bit, re-arm after each word) start, single word buffer + overrun flag;
//     Manchester with blanking (mode 2) or with a fractional phase that
//     tracks the bit centres (mode 3, v3: 10BASE-T at 50 MHz).
`default_nettype none

module tt_um_msf_serdes (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        per_we,
    input  wire [4:0]  per_sub,
    input  wire [15:0] per_wd,
    input  wire        rx_pop,
    input  wire [7:0]  uio_in,      // synchronised pad inputs
    input  wire [7:0]  ui_in,       // synchronised `ui` inputs (RX_CFG bit 3)
    // engine options, port page 2 (isa.md 6.9)
    input  wire        opt_we,
    input  wire [2:0]  opt_port,
    input  wire [15:0] opt_wd,
    input  wire [2:0]  opt_rport,
    output reg  [15:0] opt_rd,
    output wire        err_any,     // STAT bit 15: an error flag of this set is set
    output wire        tx_full,
    output wire        tx_busy,
    output wire [7:0]  tx_mask,     // the pins the engine drives, and their levels, now
    output wire [7:0]  tx_val,
    output wire [7:0]  tx_mask_n,   // the same as they will be next cycle (the pad
    output wire [7:0]  tx_val_n,    // registers load these: plan-v4 B4)
    output wire [7:0]  txu_mask,    // the same for the `uo` pins (TX_CFG bit 15), now ...
    output wire [7:0]  txu_val,
    output wire [7:0]  txu_mask_n,  // ... and next cycle
    output wire [7:0]  txu_val_n,
    output wire        rx_avail,
    output wire        rx_overrun,
    output wire [15:0] rx_word
);
  localparam [4:0] X_TXCFG = 5'd12, X_TXDIV = 5'd13, X_TXPUSH = 5'd14,
                   X_RXCFG = 5'd16, X_RXDIV = 5'd17;

  // option ports (page 2)
  wire opt_txo = opt_we && opt_port == 3'd0, opt_rxo = opt_we && opt_port == 3'd1,
       opt_clr = opt_we && opt_port == 3'd2, opt_txe = opt_we && opt_port == 3'd3,
       opt_rxe = opt_we && opt_port == 3'd4;

  reg [15:0] tx_opt, rx_opt, tx_ext, rx_ext;
  reg        e_under, e_stuff, e_frame;       // sticky error flags
  assign err_any = e_under | e_stuff | e_frame;
  always @(*) begin
    case (opt_rport)
      3'd0:    opt_rd = tx_opt;
      3'd1:    opt_rd = rx_opt;
      3'd2:    opt_rd = {13'd0, e_under, e_stuff, e_frame};
      3'd3:    opt_rd = tx_ext;
      3'd4:    opt_rd = rx_ext;
      default: opt_rd = 16'd0;
    endcase
  end

  // =====================================================================
  // TX engine
  // =====================================================================
  reg [15:0] tx_cfg, tx_div, tx_buf, tx_sr, tx_tmr;
  reg [4:0]  tx_cnt;          // data bits still in tx_sr
  reg        tx_full_r;       // tx_buf holds a word
  reg        tx_act;          // a symbol is on the line (external clock: the gate is active)
  reg        tx_half;         // Manchester: 1 = second half (or idle)
  reg        tx_bit;          // Manchester: data bit of the current cell
  reg        tx_line;
  reg [3:0]  tx_run;          // consecutive 1 data bits (kind 0) or equal bits (kind 1), for stuffing
  reg        tx_last;         // the bit before (kind 1)
  reg        tx_pre_done;     // external clock: a bit has gone out since the gate became active
  reg        txe_clk_q;       // the external clock pin a cycle ago

  wire        tx_en   = tx_cfg[13];
  wire        tx_lsb  = tx_cfg[7];
  wire [1:0]  tx_mode = tx_cfg[5:4];
  wire [4:0]  tx_w    = {1'b0, tx_cfg[11:8]} + 5'd1;
  wire [15:0] tx_dm1  = (tx_div == 16'd0) ? 16'd0 : tx_div - 16'd1;

  // stuffing (isa.md 6.9): n in TX_OPT[3:0] (0: the classic rule of TX_CFG bit 6, n = 6),
  // kind in TX_OPT[4]: 0 = after n ones insert a 0, 1 = after n equal bits insert the opposite
  wire [3:0]  tx_nopt = tx_opt[3:0];
  wire        tx_stf  = tx_cfg[6] || (tx_nopt != 4'd0);
  wire [3:0]  tx_nn   = (tx_nopt != 4'd0) ? tx_nopt : 4'd6;
  wire        tx_kind = tx_opt[4];
  wire        tx_stream = tx_opt[5];

  wire        tx_have  = (tx_cnt != 5'd0);
  wire [15:0] tx_bufal = tx_lsb ? tx_buf : (tx_buf << (5'd16 - tx_w));
  wire [15:0] tx_src   = tx_have ? tx_sr : tx_bufal;
  wire        tx_dbit  = tx_lsb ? tx_src[0] : tx_src[15];
  wire [15:0] tx_srcsh = tx_lsb ? (tx_src >> 1) : (tx_src << 1);
  wire        tx_bound = !tx_act || (tx_tmr == 16'd0);
  wire        tx_stuff = tx_stf && (tx_run == tx_nn);
  wire        tx_emit_b = tx_stuff ? (tx_kind ? ~tx_last : 1'b0) : tx_dbit;
  wire        tx_data  = tx_stuff || tx_have || tx_full_r;     // a bit can go out

  // line level for an emitted bit (NRZ, NRZI, Manchester first half)
  reg tx_lvl;
  always @(*) begin
    case (tx_mode)
      2'd1:    tx_lvl = tx_emit_b ? tx_line : ~tx_line;   // NRZI: 0 toggles
      2'd2:    tx_lvl = ~tx_emit_b;                       // Manchester 1st half
      default: tx_lvl = tx_emit_b;                        // NRZ
    endcase
  end

  // external clock (TX_EXT, isa.md 6.9): shift on an edge of a pin, while a gate pin is active
  wire        tx_xe    = tx_ext[5];
  wire        txe_clk  = tx_ext[3] ? ui_in[tx_ext[2:0]] : uio_in[tx_ext[2:0]];
  wire        txe_gpin = tx_ext[11] ? ui_in[tx_ext[10:8]] : uio_in[tx_ext[10:8]];
  wire        txe_gate = !tx_ext[13] || (txe_gpin == tx_ext[12]);
  wire        txe_edge = tx_ext[4] ? (txe_clk_q & ~txe_clk) : (~txe_clk_q & txe_clk);
  wire        txe_go   = txe_gate && (txe_edge || (tx_ext[6] && !tx_pre_done));

  // which kind of bit goes out this cycle
  wire        m2nd     = tx_en && !tx_xe && tx_bound && (tx_mode == 2'd2) && tx_act && !tx_half;
  wire        emit_div = tx_en && !tx_xe && tx_bound && !m2nd && tx_data;
  wire        emit_ext = tx_en &&  tx_xe && txe_go && tx_data;
  wire        emit     = emit_div | emit_ext;

  // The line level and the symbol-on-the-line flag as they will be next cycle: the
  // same priorities as the register updates below, which load them. With the next
  // TX_CFG they give the pads' next values, so the pads can be registered without
  // an extra cycle (plan-v4 B4).
  wire        tx_cfg_wr = per_we && per_sub == X_TXCFG;
  wire [15:0] tx_cfg_n  = tx_cfg_wr ? per_wd : tx_cfg;
  reg         tx_line_n, tx_act_n;
  always @(*) begin
    tx_line_n = tx_line;
    tx_act_n  = tx_act;
    if (tx_cfg_wr) begin
      tx_line_n = per_wd[12]; tx_act_n = 1'b0;
    end else if (tx_en && tx_xe) begin
      tx_act_n = txe_gate;                    // external clock: the engine is "on" while gated in
      if (emit_ext) tx_line_n = tx_emit_b;    // NRZ
    end else if (tx_en && tx_bound) begin
      if (m2nd) begin
        tx_line_n = tx_bit;                   // Manchester second half
      end else if (tx_data) begin
        tx_line_n = tx_lvl; tx_act_n = 1'b1;
      end else begin
        tx_act_n = 1'b0;                      // out of data: the line holds
      end
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      tx_cfg <= 16'h0; tx_div <= 16'd1; tx_buf <= 16'h0; tx_sr <= 16'h0;
      tx_tmr <= 16'h0; tx_cnt <= 5'd0; tx_full_r <= 1'b0; tx_act <= 1'b0;
      tx_half <= 1'b1; tx_bit <= 1'b0; tx_line <= 1'b0; tx_run <= 4'd0; tx_last <= 1'b0;
      tx_opt <= 16'h0; tx_ext <= 16'h0; e_under <= 1'b0; tx_pre_done <= 1'b0; txe_clk_q <= 1'b0;
    end else if (tx_cfg_wr) begin
      tx_cfg <= per_wd;
      tx_cnt <= 5'd0; tx_full_r <= 1'b0; tx_act <= tx_act_n; tx_half <= 1'b1;
      tx_run <= 4'd0; tx_last <= 1'b0; tx_tmr <= 16'h0; tx_line <= tx_line_n;
      e_under <= 1'b0; tx_pre_done <= 1'b0;
      txe_clk_q <= txe_clk;
    end else begin
      tx_line <= tx_line_n;
      tx_act  <= tx_act_n;
      txe_clk_q <= txe_clk;
      if (per_we && per_sub == X_TXDIV) tx_div <= per_wd;
      if (per_we && per_sub == X_TXPUSH) begin
        tx_buf    <= per_wd;
        tx_full_r <= 1'b1;
      end
      if (opt_txo) tx_opt <= opt_wd;
      if (opt_txe) begin
        tx_ext <= opt_wd; tx_pre_done <= 1'b0;
        txe_clk_q <= opt_wd[3] ? ui_in[opt_wd[2:0]] : uio_in[opt_wd[2:0]];   // no edge from the change
      end
      if (opt_clr && opt_wd[2]) e_under <= 1'b0;
      // divider mode: the symbol timer
      if (tx_en && !tx_xe) begin
        if (!tx_bound) begin
          tx_tmr <= tx_tmr - 16'd1;
        end else if (m2nd) begin
          tx_half <= 1'b1;                  // Manchester second half (the level: tx_line_n)
          tx_tmr  <= tx_dm1;
        end else if (emit_div) begin
          tx_half <= (tx_mode != 2'd2);
          tx_tmr  <= tx_dm1;
        end
      end
      // external clock: a gate that is not active clears the shifter (the buffer stays)
      if (tx_en && tx_xe && !txe_gate) begin
        tx_cnt <= 5'd0; tx_pre_done <= 1'b0; tx_run <= 4'd0;
      end
      // a bit goes out (either mode): stuffing counters, the shifter, the buffer
      if (emit) begin
        tx_bit <= tx_emit_b;
        if (emit_ext) tx_pre_done <= 1'b1;
        if (tx_kind) begin
          tx_run  <= (tx_run != 4'd0 && tx_emit_b == tx_last) ? tx_run + 4'd1 : 4'd1;
          tx_last <= tx_emit_b;
        end else begin
          tx_run  <= (!tx_stuff && tx_dbit) ? tx_run + 4'd1 : 4'd0;
        end
        if (!tx_stuff) begin
          tx_sr <= tx_srcsh;
          if (tx_have) begin
            tx_cnt <= tx_cnt - 5'd1;
          end else begin
            tx_cnt    <= tx_w - 5'd1;
            tx_full_r <= 1'b0;
          end
        end
      end
      // E_UNDER: a streaming transmitter finds nothing to send (isa.md 6.9)
      if (tx_en && tx_stream) begin
        if (!tx_xe && tx_bound && tx_act && !m2nd && !tx_data) e_under <= 1'b1;
        if (tx_xe && txe_gate && txe_edge && !tx_data)        e_under <= 1'b1;
      end
    end
  end

  wire [2:0] tx_p0 = tx_cfg[2:0];
  wire [2:0] tx_p1 = tx_cfg[2:0] + 3'd1;
  wire [7:0] tx_b0 = 8'd1 << tx_p0;
  wire [7:0] tx_b1 = tx_cfg[3] ? (8'd1 << tx_p1) : 8'd0;

  assign tx_full = tx_full_r;
  assign tx_busy = tx_act | tx_have | tx_full_r;
  // release mode (cfg[14]): the engine owns its pins only while a symbol is
  // on the line, so before the first and after the last symbol the pads
  // show OUT -- with exact timing, on the symbol boundaries.
  wire tx_own = tx_en && (!tx_cfg[14] || tx_act);
  wire [7:0] txa_mask = tx_own ? (tx_b0 | tx_b1) : 8'd0;
  wire [7:0] txa_val  = (tx_line ? tx_b0 : 8'd0) | (tx_line ? 8'd0 : tx_b1);
  assign tx_mask  = tx_cfg[15] ? 8'd0 : txa_mask;      // uio, or ...
  assign tx_val   = txa_val;
  assign txu_mask = tx_cfg[15] ? txa_mask : 8'd0;      // ... uo
  assign txu_val  = txa_val;

  // the same, one cycle on, from the next TX_CFG, line level and symbol flag
  wire [2:0] tx_p0n = tx_cfg_n[2:0];
  wire [2:0] tx_p1n = tx_cfg_n[2:0] + 3'd1;
  wire [7:0] tx_b0n = 8'd1 << tx_p0n;
  wire [7:0] tx_b1n = tx_cfg_n[3] ? (8'd1 << tx_p1n) : 8'd0;
  wire       tx_ownn = tx_cfg_n[13] && (!tx_cfg_n[14] || tx_act_n);
  wire [7:0] txa_mask_n = tx_ownn ? (tx_b0n | tx_b1n) : 8'd0;
  wire [7:0] txa_val_n  = (tx_line_n ? tx_b0n : 8'd0) | (tx_line_n ? 8'd0 : tx_b1n);
  assign tx_mask_n  = tx_cfg_n[15] ? 8'd0 : txa_mask_n;
  assign tx_val_n   = txa_val_n;
  assign txu_mask_n = tx_cfg_n[15] ? txa_mask_n : 8'd0;
  assign txu_val_n  = txa_val_n;

  // =====================================================================
  // RX engine
  // =====================================================================
  reg [15:0] rx_cfg, rx_div, rx_sr, rx_buf, rx_tmr;
  reg [4:0]  rx_cnt;
  reg        rx_av, rx_ovr, rx_run, rx_prev, rx_ref;
  reg        rx_mw;                  // Manchester: past the blanking, a
                                     // transition now is a bit centre
  reg [2:0]  rx_ones;                // Manchester mode 3: the lock's centres
  reg [3:0]  rx_srun;                // stuffing: consecutive 1s (kind 0) or equal bits (kind 1)
  reg        rx_slast;               //   and the bit before (kind 1)
  reg        rx_stop;                // character mode, stop bit checked: the word waits for it
  reg        rxe_clk_q;              // external clock pin a cycle ago

  wire        rx_pin  = rx_cfg[3] ? ui_in[rx_cfg[2:0]] : uio_in[rx_cfg[2:0]];   // bit 3: ui
  wire        rx_en   = rx_cfg[13];
  wire        rx_char = rx_cfg[12];
  wire        rx_rs   = rx_cfg[14];
  wire        rx_xe   = rx_ext[5];           // external clock: NRZ, no divider
  wire        rx_nrzi = (rx_cfg[5:4] == 2'd1) && !rx_xe;
  wire        rx_man  = (rx_cfg[5:4] == 2'd2) && !rx_xe;   // half-bit = RX_DIV
  wire        rx_lsb  = rx_cfg[7];
  wire [4:0]  rx_w    = {1'b0, rx_cfg[11:8]} + 5'd1;
  wire [15:0] rx_half = {1'b0, rx_div[15:1]};
  wire [15:0] rx_hm1  = (rx_half == 16'd0) ? 16'd0 : rx_half - 16'd1;
  wire [15:0] rx_dm1  = (rx_div == 16'd0) ? 16'd0 : rx_div - 16'd1;

  // de-stuffing (isa.md 6.9): n in RX_OPT[3:0] (0: the classic rule of RX_CFG bit 6, n = 6)
  wire [3:0]  rx_nopt = rx_opt[3:0];
  wire        rx_stf  = rx_cfg[6] || (rx_nopt != 4'd0);
  wire [3:0]  rx_nn   = (rx_nopt != 4'd0) ? rx_nopt : 4'd6;
  wire        rx_kind = rx_opt[4];
  wire        rx_schk = rx_opt[5] && rx_char;   // check the stop bit

  wire        rx_edge  = rx_pin ^ rx_prev;
  wire        rx_start = rx_char ? (rx_prev & ~rx_pin) : rx_edge;
  wire        rx_samp  = rx_run && (rx_tmr == 16'd0);
  wire        rx_b     = rx_nrzi ? ~(rx_pin ^ rx_ref) : rx_pin;
  wire        rx_drop  = rx_stf && !rx_man && (rx_srun == rx_nn);
  wire        rx_sbad  = rx_drop && (rx_kind ? (rx_b == rx_slast) : rx_b);   // not what the kind requires
  wire [15:0] rx_srn   = rx_lsb ? {rx_b, rx_sr[15:1]} : {rx_sr[14:0], rx_b};
  wire        rx_done  = rx_samp && !rx_drop && (rx_cnt + 5'd1 == rx_w);
  // Manchester: a transition is taken as a bit centre when the receiver is
  // idle (the first of a frame) or past the 1.5 half-bit blanking after the
  // last centre (which hides the boundary transitions); the bit is the level
  // after it (IEEE 802.3: low->high = 1, as the TX engine sends it)
  wire        m_acc    = rx_man && rx_edge && (!rx_run || rx_mw);
  wire        m_done   = m_acc && (rx_cnt + 5'd1 == rx_w);
  // Phase-tracking Manchester (mode 3, v3): rx_tmr is the phase within the bit
  // (65536 a bit) and advances RX_DIV a cycle, so the bit period need not be a
  // whole number of cycles (10BASE-T at 50 MHz: 5 cycles a bit, 13107). The first
  // transition of a frame is a centre (phase 0.5), and so are the next six,
  // whatever their phase (a preamble of alternating bits has no others), each
  // pulling the phase half way to 0.5: the lock. After that a transition is a
  // centre only in the bit's middle half (phase 0.25-0.75) and pulls the phase a
  // sixteenth of the way, so the phase follows the centres' average, not the last
  // one; a transition nearer the boundary (between equal bits) is ignored. A
  // centre gives the bit, the level after it. A bit that ends with no centre ends
  // the frame. rx_ones counts the lock's centres.
  wire        rx_pll   = (rx_cfg[5:4] == 2'd3) && !rx_xe;
  wire        p_lock   = (rx_ones == 3'd7);
  wire        p_win    = rx_tmr[15] ^ rx_tmr[14];
  wire        p_acc    = rx_pll && rx_edge && (!rx_run || !p_lock || p_win);
  wire        p_done   = p_acc && (rx_cnt + 5'd1 == rx_w);
  wire [16:0] p_next   = {1'b0, rx_tmr} + {1'b0, rx_div};       // carry: the bit ends
  wire [16:0] p_half   = {1'b0, rx_tmr} + 17'h08000;            // 2 x (half way to 0.5)
  wire [16:0] p_err    = 17'h08000 - {1'b0, rx_tmr};            // 0.5 - phase, signed
  wire [15:0] p_frac   = rx_tmr + {{4{p_err[16]}}, p_err[15:4]}; // a sixteenth of the way
  wire [16:0] rx_mask  = (17'd1 << rx_w) - 17'd1;
  wire [15:0] rx_wordn = rx_lsb ? (rx_srn >> (5'd16 - rx_w)) : (rx_srn & rx_mask[15:0]);
  wire [15:0] rx_wordc = rx_lsb ? (rx_sr >> (5'd16 - rx_w)) : (rx_sr & rx_mask[15:0]);   // the finished shifter

  // external clock (RX_EXT): the data pin is taken on every active edge of a clock pin
  wire        rxe_clk  = rx_ext[3] ? ui_in[rx_ext[2:0]] : uio_in[rx_ext[2:0]];
  wire        rxe_gpin = rx_ext[11] ? ui_in[rx_ext[10:8]] : uio_in[rx_ext[10:8]];
  wire        rxe_gate = !rx_ext[13] || (rxe_gpin == rx_ext[12]);
  wire        rxe_edge = rx_ext[4] ? (rxe_clk_q & ~rxe_clk) : (~rxe_clk_q & rxe_clk);

  wire        rx_cfg_wr = per_we && per_sub == X_RXCFG;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rx_cfg <= 16'h0; rx_div <= 16'd1; rx_sr <= 16'h0; rx_buf <= 16'h0;
      rx_tmr <= 16'h0; rx_cnt <= 5'd0; rx_av <= 1'b0; rx_ovr <= 1'b0;
      rx_run <= 1'b0; rx_prev <= 1'b0; rx_ref <= 1'b0; rx_ones <= 3'd0;
      rx_mw <= 1'b0; rx_srun <= 4'd0; rx_slast <= 1'b0; rx_stop <= 1'b0;
      rx_opt <= 16'h0; rx_ext <= 16'h0; rxe_clk_q <= 1'b0; e_stuff <= 1'b0; e_frame <= 1'b0;
    end else begin
      rx_prev <= rx_pin;
      rxe_clk_q <= rxe_clk;
      if (rx_cfg_wr) begin
        rx_cfg  <= per_wd;
        rx_run  <= 1'b0; rx_cnt <= 5'd0; rx_av <= 1'b0; rx_ovr <= 1'b0;
        rx_ones <= 3'd0; rx_tmr <= 16'h0; rx_mw <= 1'b0;
        rx_srun <= 4'd0; rx_slast <= 1'b0; rx_stop <= 1'b0; e_stuff <= 1'b0; e_frame <= 1'b0;
        rx_ref  <= per_wd[3] ? ui_in[per_wd[2:0]] : uio_in[per_wd[2:0]];
        rx_prev <= per_wd[3] ? ui_in[per_wd[2:0]] : uio_in[per_wd[2:0]];
      end else begin
        if (per_we && per_sub == X_RXDIV) rx_div <= per_wd;
        if (opt_rxo) rx_opt <= opt_wd;
        if (opt_rxe) begin
          rx_ext <= opt_wd; rx_cnt <= 5'd0;
          rxe_clk_q <= opt_wd[3] ? ui_in[opt_wd[2:0]] : uio_in[opt_wd[2:0]];   // no edge from the change
        end
        if (opt_clr) begin
          if (opt_wd[0]) e_frame <= 1'b0;
          if (opt_wd[1]) e_stuff <= 1'b0;
        end
        if (rx_pop) rx_av <= 1'b0;
        if (rx_en && rx_xe) begin
          // external clock: one data bit per active edge while gated in; a partial word is
          // dropped when the gate goes inactive
          if (!rxe_gate) begin
            rx_cnt <= 5'd0;
          end else if (rxe_edge) begin
            rx_sr <= rx_srn;
            if (rx_cnt + 5'd1 == rx_w) begin
              rx_buf <= rx_wordn;
              rx_av  <= 1'b1;
              rx_ovr <= rx_ovr | (rx_av & ~rx_pop);
              rx_cnt <= 5'd0;
            end else begin
              rx_cnt <= rx_cnt + 5'd1;
            end
          end
        end else if (rx_en && rx_man) begin
          if (m_acc) begin
            rx_run <= 1'b1; rx_mw <= 1'b0;
            rx_tmr <= rx_div + rx_hm1;       // 1.5 half-bits of blanking
            rx_sr  <= rx_srn;
            if (m_done) begin
              rx_buf <= rx_wordn;
              rx_av  <= 1'b1;
              rx_ovr <= rx_ovr | (rx_av & ~rx_pop);
              rx_cnt <= 5'd0;
            end else begin
              rx_cnt <= (rx_run ? rx_cnt : 5'd0) + 5'd1;
            end
          end else if (rx_run) begin
            if (rx_tmr != 16'd0) rx_tmr <= rx_tmr - 16'd1;
            else if (!rx_mw) begin
              rx_mw <= 1'b1; rx_tmr <= rx_dm1; // the window: one half-bit
            end else begin
              rx_run <= 1'b0; rx_cnt <= 5'd0;  // no centre: the frame ended
            end
          end
        end else if (rx_en && rx_pll) begin
          if (p_acc) begin
            rx_run <= 1'b1; rx_mw <= 1'b1;   // this bit has its centre
            // the first centre: phase 0.5; the lock: half way to 0.5; then a sixteenth
            rx_tmr <= (!rx_run ? 16'h8000 : !p_lock ? p_half[16:1] : p_frac) + rx_div;
            if (!rx_run)     rx_ones <= 3'd1;
            else if (!p_lock) rx_ones <= rx_ones + 3'd1;
            rx_sr  <= rx_srn;
            if (p_done) begin
              rx_buf <= rx_wordn;
              rx_av  <= 1'b1;
              rx_ovr <= rx_ovr | (rx_av & ~rx_pop);
              rx_cnt <= 5'd0;
            end else begin
              rx_cnt <= (rx_run ? rx_cnt : 5'd0) + 5'd1;
            end
          end else if (rx_run) begin
            rx_tmr <= p_next[15:0];
            if (p_next[16]) begin              // a bit boundary
              if (rx_mw) rx_mw <= 1'b0;
              else begin                        // a bit with no centre: the frame ended
                rx_run <= 1'b0; rx_cnt <= 5'd0; rx_ones <= 3'd0;
              end
            end
          end
        end else if (rx_en) begin
          if (!rx_run) begin
            if (rx_start) begin
              rx_run <= 1'b1;
              rx_tmr <= rx_char ? rx_div + rx_hm1 : rx_hm1;
            end
          end else begin
            if (rx_samp && rx_stop) begin
              // the stop bit's centre (RX_OPT bit 5): the word is delivered now, and a
              // stop bit that is not 1 is a framing error
              rx_buf <= rx_wordc;
              rx_av  <= 1'b1;
              rx_ovr <= rx_ovr | (rx_av & ~rx_pop);
              rx_cnt <= 5'd0; rx_run <= 1'b0; rx_stop <= 1'b0;
              if (!rx_pin) e_frame <= 1'b1;
            end else if (rx_samp) begin
              rx_tmr <= rx_dm1;
              rx_ref <= rx_pin;
              if (rx_drop) begin
                rx_srun <= rx_kind ? 4'd1 : 4'd0;     // kind 1: the stuff bit starts a run
                rx_slast <= rx_b;
                if (rx_sbad) e_stuff <= 1'b1;
              end else begin
                rx_sr   <= rx_srn;
                rx_srun <= rx_kind ? ((rx_srun != 4'd0 && rx_b == rx_slast) ? rx_srun + 4'd1 : 4'd1)
                                   : (rx_b ? rx_srun + 4'd1 : 4'd0);
                rx_slast <= rx_b;
                if (rx_done) begin
                  if (rx_schk) begin
                    rx_stop <= 1'b1;                  // wait one more bit, for the stop bit
                  end else begin
                    rx_buf <= rx_wordn;
                    rx_av  <= 1'b1;
                    rx_ovr <= rx_ovr | (rx_av & ~rx_pop);
                    rx_cnt <= 5'd0;
                    if (rx_char) rx_run <= 1'b0;
                  end
                end else begin
                  rx_cnt <= rx_cnt + 5'd1;
                end
              end
            end else begin
              rx_tmr <= rx_tmr - 16'd1;
            end
            if (rx_rs && rx_edge) rx_tmr <= rx_hm1;
          end
        end
      end
    end
  end

  assign rx_avail   = rx_av;
  assign rx_overrun = rx_ovr;
  assign rx_word    = rx_buf;
endmodule

`default_nettype wire
