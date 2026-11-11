// SPDX-License-Identifier: Apache-2.0
// msfcore capture engine (isa.md §6.6): an 8-channel logic analyser that
// records the synchronised uio (or ui) pins into an SRAM ring buffer.
//   sample mode: every DIV cycles a sample; two samples per word
//                (the earlier one in the low byte).
//   change mode: a word {pins, ticks since the last word} whenever the pins
//                change, or after 255 ticks without a change.
// It records continuously from arming (so the buffer holds what came before
// the trigger), and after the trigger word writes POST more words, then
// stops. Trigger: (pins & mask) == value, as a level or as an edge (true now,
// false at the previous tick).
// It writes SRAM port B only in cycles the core does not use it, through a
// two-word queue; a word that finds the queue full is lost and sets the
// overflow flag. So capturing never changes the core's timing.
`default_nettype none

module tt_um_msf_cap (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,      // synchronised
    input  wire [7:0]  ui_in,       // synchronised
    // configuration: PW #2..#5 from the core
    input  wire        pw_we,
    input  wire [2:0]  pw_port,
    input  wire [15:0] pw_data,
    // SRAM port B: the core has priority
    input  wire        core_b,      // the core uses port B this cycle
    output wire        req,         // capture writes port B this cycle
    output wire [10:0] addr,
    output wire [15:0] wd,
    // status: PR #2 / #3
    output wire [15:0] stat,
    output wire [10:0] tpos
);
  localparam [2:0] P_BUF = 3'd2, P_DIV = 3'd3, P_TRIG = 3'd4, P_CTL = 3'd5;

  // ---- configuration ----
  reg [10:0] base;                  // v4: 11 bits (2,048 words of SRAM)
  reg [3:0]  n;                     // buffer: 2 << n words, size-aligned
  reg [15:0] div;
  reg [7:0]  tmask, tval;
  reg [9:0]  post;
  reg        edge_t, src_ui, chg;

  // ---- state ----
  reg        run, trig, done, ovf, first;
  reg [15:0] tick;                  // cycles to the next sample
  reg [7:0]  last;                  // change mode: last pins recorded;
                                    // sample mode: first sample of a pair
  reg        half;                  // sample mode: a first sample is waiting
  reg        thit;                  // sample mode: that sample met the trigger
  reg [7:0]  delta;                 // change mode: ticks since the last word
  reg        prev_c;                // trigger condition at the previous tick
  reg [10:0] wcnt;                  // words produced since arming
  reg [9:0]  left;                  // words still to produce after the trigger
  reg [10:0] tp;

  wire [10:0] mask  = (11'd2 << n) - 11'd1;   // n >= 10: all 2048 words
  wire [10:0] waddr = (base & ~mask) | (wcnt & mask);

  wire [7:0] pins  = src_ui ? ui_in : uio_in;
  wire       now   = run && (tick == 16'd0);           // a sample this cycle
  wire       cond  = ((pins & tmask) == tval);
  wire       hit   = cond && (!edge_t || !prev_c);
  wire [7:0] dnext = delta + 8'd1;

  // a word produced this cycle
  reg        prod;
  reg [15:0] pword;
  reg        ptrig;                 // it holds the trigger
  always @(*) begin
    prod = 1'b0; pword = 16'h0; ptrig = 1'b0;
    if (now) begin
      if (chg) begin
        if (first || pins != last || dnext == 8'd255 || (hit && !trig)) begin
          prod = 1'b1; pword = {pins, dnext}; ptrig = hit && !trig;
        end
      end else if (half) begin
        prod = 1'b1; pword = {pins, last}; ptrig = !trig && (thit || hit);
      end
    end
  end

  // ---- two-word write queue ----
  reg [26:0] q0, q1;                // {addr, data}
  reg [1:0]  qn;
  wire       pop   = (qn != 2'd0) && !core_b;
  wire       push  = prod && (qn != 2'd2 || pop);
  assign req  = pop;
  assign addr = q0[26:16];
  assign wd   = q0[15:0];

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      base <= 11'd512; n <= 4'd7; div <= 16'd1; tmask <= 8'h0; tval <= 8'h0;
      post <= 10'd0; edge_t <= 1'b0; src_ui <= 1'b0; chg <= 1'b0;
      run <= 1'b0; trig <= 1'b0; done <= 1'b0; ovf <= 1'b0; first <= 1'b0;
      tick <= 16'd0; last <= 8'h0; half <= 1'b0; thit <= 1'b0; delta <= 8'd0;
      prev_c <= 1'b0; wcnt <= 11'd0; left <= 10'd0; tp <= 11'd0;
      q0 <= 27'd0; q1 <= 27'd0; qn <= 2'd0;
    end else begin
      // queue
      if (pop && push) begin
        if (qn == 2'd1) q0 <= {waddr, pword};
        else begin q0 <= q1; q1 <= {waddr, pword}; end
      end else if (pop) begin
        q0 <= q1; qn <= qn - 2'd1;
      end else if (push) begin
        if (qn == 2'd0) q0 <= {waddr, pword}; else q1 <= {waddr, pword};
        qn <= qn + 2'd1;
      end
      if (prod && !push) ovf <= 1'b1;

      // sampling
      if (run) begin
        tick <= (tick == 16'd0) ? ((div == 16'd0) ? 16'd0 : div - 16'd1) : tick - 16'd1;
        if (now) begin
          prev_c <= cond;
          first  <= 1'b0;
          if (chg) begin
            delta <= prod ? 8'd0 : dnext;
            if (prod) last <= pins;
          end else if (!half) begin
            last <= pins; half <= 1'b1; thit <= hit && !trig;
          end else begin
            half <= 1'b0;
          end
        end
        if (prod) begin
          wcnt <= wcnt + 11'd1;
          if (ptrig) begin
            trig <= 1'b1; tp <= waddr; left <= post;
            if (post == 10'd0) begin run <= 1'b0; done <= 1'b1; end
          end else if (trig) begin
            if (left == 10'd1) begin run <= 1'b0; done <= 1'b1; end
            left <= left - 10'd1;
          end
        end
      end

      // configuration (wins over the above)
      if (pw_we) case (pw_port)
        P_BUF:  begin base <= {pw_data[14], pw_data[9:0]}; n <= pw_data[13:10]; end
        P_DIV:  div <= pw_data;
        P_TRIG: begin tmask <= pw_data[7:0]; tval <= pw_data[15:8]; end
        P_CTL:  begin
          post <= pw_data[9:0]; edge_t <= pw_data[12]; src_ui <= pw_data[13];
          chg <= pw_data[14]; run <= pw_data[15];
          trig <= 1'b0; done <= 1'b0; ovf <= 1'b0; first <= 1'b1;
          tick <= 16'd0; half <= 1'b0; thit <= 1'b0; delta <= 8'd0;
          prev_c <= 1'b1;           // an edge needs the condition false first
          wcnt <= 11'd0; left <= 10'd0;
        end
        default: ;
      endcase
    end
  end

  assign stat = {run, trig, done, ovf, 1'b0, (base & ~mask) | (wcnt & mask)};
  assign tpos = tp;

`ifdef FORMAL
  // F9 (also checked in project.v): a capture write takes port B only in a
  // cycle the core leaves it free. Here core_b is a free input, so every
  // state and cycle is covered.
  always @(*) assert(!(core_b && req));
`endif
endmodule

`default_nettype wire
