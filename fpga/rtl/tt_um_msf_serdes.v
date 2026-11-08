// SPDX-License-Identifier: Apache-2.0
// msfcore SERDES (isa.md §6.1/§6.2): generic serialiser + deserialiser that
// run autonomously at a programmable symbol rate while firmware feeds them.
// They implement line codings, not protocols -- framing (start/stop bits,
// SYNC, EOP, preambles, CRC) stays in firmware.
//
// TX: NRZ / NRZI / Manchester, optional bit stuffing, optional differential
//     pin pair, 1..16-bit words, MSB- or LSB-first, double buffered (gapless),
//     optional release mode (the engine owns its pins only while a symbol is
//     on the line -- exact USB SYNC/EOP and Ethernet idle/TP_IDL timing).
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
    output wire        tx_full,
    output wire        tx_busy,
    output wire [7:0]  tx_mask,
    output wire [7:0]  tx_val,
    output wire        rx_avail,
    output wire        rx_overrun,
    output wire [15:0] rx_word
);
  localparam [4:0] X_TXCFG = 5'd12, X_TXDIV = 5'd13, X_TXPUSH = 5'd14,
                   X_RXCFG = 5'd16, X_RXDIV = 5'd17;

  // =====================================================================
  // TX engine
  // =====================================================================
  reg [15:0] tx_cfg, tx_div, tx_buf, tx_sr, tx_tmr;
  reg [4:0]  tx_cnt;          // data bits still in tx_sr
  reg        tx_full_r;       // tx_buf holds a word
  reg        tx_act;          // a symbol is on the line
  reg        tx_half;         // Manchester: 1 = second half (or idle)
  reg        tx_bit;          // Manchester: data bit of the current cell
  reg        tx_line;
  reg [2:0]  tx_ones;         // consecutive 1 data bits (for stuffing)

  wire        tx_en   = tx_cfg[13];
  wire        tx_lsb  = tx_cfg[7];
  wire        tx_stf  = tx_cfg[6];
  wire [1:0]  tx_mode = tx_cfg[5:4];
  wire [4:0]  tx_w    = {1'b0, tx_cfg[11:8]} + 5'd1;
  wire [15:0] tx_dm1  = (tx_div == 16'd0) ? 16'd0 : tx_div - 16'd1;

  wire        tx_have  = (tx_cnt != 5'd0);
  wire [15:0] tx_bufal = tx_lsb ? tx_buf : (tx_buf << (5'd16 - tx_w));
  wire [15:0] tx_src   = tx_have ? tx_sr : tx_bufal;
  wire        tx_dbit  = tx_lsb ? tx_src[0] : tx_src[15];
  wire [15:0] tx_srcsh = tx_lsb ? (tx_src >> 1) : (tx_src << 1);
  wire        tx_bound = !tx_act || (tx_tmr == 16'd0);
  wire        tx_stuff = tx_stf && (tx_ones == 3'd6);
  wire        tx_emit_b = tx_stuff ? 1'b0 : tx_dbit;

  // line level for an emitted bit (NRZ, NRZI, Manchester first half)
  reg tx_lvl;
  always @(*) begin
    case (tx_mode)
      2'd1:    tx_lvl = tx_emit_b ? tx_line : ~tx_line;   // NRZI: 0 toggles
      2'd2:    tx_lvl = ~tx_emit_b;                       // Manchester 1st half
      default: tx_lvl = tx_emit_b;                        // NRZ
    endcase
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      tx_cfg <= 16'h0; tx_div <= 16'd1; tx_buf <= 16'h0; tx_sr <= 16'h0;
      tx_tmr <= 16'h0; tx_cnt <= 5'd0; tx_full_r <= 1'b0; tx_act <= 1'b0;
      tx_half <= 1'b1; tx_bit <= 1'b0; tx_line <= 1'b0; tx_ones <= 3'd0;
    end else if (per_we && per_sub == X_TXCFG) begin
      tx_cfg <= per_wd;
      tx_cnt <= 5'd0; tx_full_r <= 1'b0; tx_act <= 1'b0; tx_half <= 1'b1;
      tx_ones <= 3'd0; tx_tmr <= 16'h0; tx_line <= per_wd[12];
    end else begin
      if (per_we && per_sub == X_TXDIV) tx_div <= per_wd;
      if (per_we && per_sub == X_TXPUSH) begin
        tx_buf    <= per_wd;
        tx_full_r <= 1'b1;
      end
      if (tx_en) begin
        if (!tx_bound) begin
          tx_tmr <= tx_tmr - 16'd1;
        end else if (tx_mode == 2'd2 && tx_act && !tx_half) begin
          tx_line <= tx_bit;                // Manchester second half
          tx_half <= 1'b1;
          tx_tmr  <= tx_dm1;
        end else if (tx_stuff || tx_have || tx_full_r) begin
          tx_line <= tx_lvl;
          tx_bit  <= tx_emit_b;
          tx_half <= (tx_mode != 2'd2);
          tx_act  <= 1'b1;
          tx_tmr  <= tx_dm1;
          if (tx_stuff) begin
            tx_ones <= 3'd0;
          end else begin
            tx_ones <= tx_dbit ? tx_ones + 3'd1 : 3'd0;
            tx_sr   <= tx_srcsh;
            if (tx_have) begin
              tx_cnt <= tx_cnt - 5'd1;
            end else begin
              tx_cnt    <= tx_w - 5'd1;
              tx_full_r <= 1'b0;
            end
          end
        end else begin
          tx_act <= 1'b0;                   // out of data: line holds
        end
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
  assign tx_mask = tx_own ? (tx_b0 | tx_b1) : 8'd0;
  assign tx_val  = (tx_line ? tx_b0 : 8'd0) | (tx_line ? 8'd0 : tx_b1);

  // =====================================================================
  // RX engine
  // =====================================================================
  reg [15:0] rx_cfg, rx_div, rx_sr, rx_buf, rx_tmr;
  reg [4:0]  rx_cnt;
  reg        rx_av, rx_ovr, rx_run, rx_prev, rx_ref;
  reg        rx_mw;                  // Manchester: past the blanking, a
                                     // transition now is a bit centre
  reg [2:0]  rx_ones;

  wire        rx_pin  = uio_in[rx_cfg[2:0]];
  wire        rx_en   = rx_cfg[13];
  wire        rx_char = rx_cfg[12];
  wire        rx_rs   = rx_cfg[14];
  wire        rx_nrzi = (rx_cfg[5:4] == 2'd1);
  wire        rx_man  = (rx_cfg[5:4] == 2'd2);   // half-bit = RX_DIV
  wire        rx_dst  = rx_cfg[6];
  wire        rx_lsb  = rx_cfg[7];
  wire [4:0]  rx_w    = {1'b0, rx_cfg[11:8]} + 5'd1;
  wire [15:0] rx_half = {1'b0, rx_div[15:1]};
  wire [15:0] rx_hm1  = (rx_half == 16'd0) ? 16'd0 : rx_half - 16'd1;
  wire [15:0] rx_dm1  = (rx_div == 16'd0) ? 16'd0 : rx_div - 16'd1;

  wire        rx_edge  = rx_pin ^ rx_prev;
  wire        rx_start = rx_char ? (rx_prev & ~rx_pin) : rx_edge;
  wire        rx_samp  = rx_run && (rx_tmr == 16'd0);
  wire        rx_b     = rx_nrzi ? ~(rx_pin ^ rx_ref) : rx_pin;
  wire        rx_drop  = rx_dst && !rx_man && (rx_ones == 3'd6);
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
  // the frame. rx_ones (de-stuffing's, unused here) counts the lock's centres.
  wire        rx_pll   = (rx_cfg[5:4] == 2'd3);
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

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rx_cfg <= 16'h0; rx_div <= 16'd1; rx_sr <= 16'h0; rx_buf <= 16'h0;
      rx_tmr <= 16'h0; rx_cnt <= 5'd0; rx_av <= 1'b0; rx_ovr <= 1'b0;
      rx_run <= 1'b0; rx_prev <= 1'b0; rx_ref <= 1'b0; rx_ones <= 3'd0;
      rx_mw <= 1'b0;
    end else begin
      rx_prev <= rx_pin;
      if (per_we && per_sub == X_RXCFG) begin
        rx_cfg  <= per_wd;
        rx_run  <= 1'b0; rx_cnt <= 5'd0; rx_av <= 1'b0; rx_ovr <= 1'b0;
        rx_ones <= 3'd0; rx_tmr <= 16'h0; rx_mw <= 1'b0;
        rx_ref  <= uio_in[per_wd[2:0]];
        rx_prev <= uio_in[per_wd[2:0]];
      end else begin
        if (per_we && per_sub == X_RXDIV) rx_div <= per_wd;
        if (rx_pop) rx_av <= 1'b0;
        if (rx_en && rx_man) begin
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
            if (rx_samp) begin
              rx_tmr <= rx_dm1;
              rx_ref <= rx_pin;
              if (rx_drop) begin
                rx_ones <= 3'd0;
              end else begin
                rx_sr   <= rx_srn;
                rx_ones <= rx_b ? rx_ones + 3'd1 : 3'd0;
                if (rx_done) begin
                  rx_buf <= rx_wordn;
                  rx_av  <= 1'b1;
                  rx_ovr <= rx_ovr | (rx_av & ~rx_pop);
                  rx_cnt <= 5'd0;
                  if (rx_char) rx_run <= 1'b0;
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
