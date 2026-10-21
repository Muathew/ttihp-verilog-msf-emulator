// SPDX-License-Identifier: Apache-2.0
// msfcore control unit (ISA v2.0, docs/isa.md): fetch / decode / execute
// for two hardware threads, blocking-op (stall) engine, shift register,
// data-RAM port, pin writes and the peripheral bus.
//
// Pipeline. IMEM has a registered read: the word fetched with imem_raddr in
// cycle N is decoded in cycle N+1.
//  * Single-thread mode (dual=0): T0 owns every cycle. During execution pc0 =
//    address+1 (fetch-ahead). Taken redirects insert one bubble (flush0).
//    Blocking op at W: pc0 is held at W+1 on entry so mem[W+1] is re-fetched
//    every stall cycle; on the release cycle pc0 advances to W+2, so mem[W+1]
//    executes exactly once with pc0 == its address+1.
//  * Dual-thread mode (dual=1): cycles alternate T0/T1 (tsel). The word for
//    thread t is fetched in the other thread's cycle, so pc_t is simply the
//    address of t's next instruction and redirects cost nothing.
// Blocking ops (WAIT_EDGE, WAIT_CMP, LD, TX_PUSH, TX_WAIT, RX_POP, CRC_W,
// CRC_RDL) stall their thread; the release (and its transfer) happens only in
// a cycle owned by that thread, so peripheral / register-file / port-B
// traffic of the two threads never collides. Event conditions (edges, CNT
// match) are latched every cycle so none is lost in the other thread's slot.
`default_nettype none

module tt_um_msf_ctrl (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,      // synchronised
    input  wire [7:0]  ui_in,       // synchronised
    // instruction fetch (registered memory)
    output wire [9:0]  imem_raddr,
    input  wire [15:0] imem_rd,
    // SRAM port B (data)
    output wire        b_en,
    output wire        b_we,
    output wire [9:0]  b_addr,
    output wire [15:0] b_wd,
    input  wire [15:0] b_rd,
    // pin registers (tt_um_msf_pio)
    input  wire [7:0]  out_q,
    input  wire [7:0]  oe_q,
    input  wire [7:0]  uo_q,
    output reg         out_we,
    output reg  [7:0]  out_d,
    output reg         oe_we,
    output reg  [7:0]  oe_d,
    output reg         uo_we,
    output reg  [7:0]  uo_d,
    // peripheral bus (tt_um_msf_serdes, tt_um_msf_crc)
    output wire        per_we,
    output wire [4:0]  per_sub,
    output wire [15:0] per_wd,
    output wire        rx_pop,
    input  wire        tx_full,
    input  wire        tx_busy,
    input  wire        rx_avail,
    input  wire        rx_overrun,
    input  wire [15:0] rx_word,
    input  wire        crc_busy,
    input  wire [15:0] crc_lo,
    input  wire [15:0] crc_hi,
    // peripheral ports 2-7 (tt_um_msf_cap): writes out, status in
    output wire        pw_we,
    output wire [2:0]  pw_port,
    output wire [15:0] pw_data,
    input  wire [15:0] cap_stat,
    input  wire [9:0]  cap_tpos,
    // debug view (project.v shows dbg_q on uo_out in debug mode)
    input  wire [5:0]  dbg_sel,
    output reg  [7:0]  dbg_q
);
  // ------------------------------------------------------------------
  // Encodings
  // ------------------------------------------------------------------
  localparam [4:0] OP_LDR = 5'h00, OP_ADDS = 5'h01, OP_SUBS = 5'h02, OP_AND = 5'h03,
                   OP_OR = 5'h04, OP_XOR = 5'h05, OP_SHL = 5'h06, OP_SHR = 5'h07,
                   OP_MOV = 5'h08, OP_JMP = 5'h09, OP_JZ = 5'h0A, OP_JNZ = 5'h0B,
                   OP_JP = 5'h0C, OP_JN = 5'h0D, OP_NOP = 5'h0E, OP_EXT = 5'h0F,
                   OP_PINW = 5'h10, OP_PINR = 5'h11, OP_SETM = 5'h12, OP_CLRM = 5'h13,
                   OP_CNTR = 5'h14, OP_INCR = 5'h15, OP_DECR = 5'h16, OP_INRD = 5'h17,
                   OP_CALL = 5'h18, OP_RET = 5'h19, OP_WEDGE = 5'h1A, OP_WCMP = 5'h1B,
                   OP_SHOUT = 5'h1C, OP_SHIN = 5'h1D, OP_MEMW = 5'h1E, OP_LDH = 5'h1F;

  localparam [4:0] X_SRLD = 5'd0, X_SRRD = 5'd1, X_XFER = 5'd2, X_SERCFG = 5'd3,
                   X_OUTW = 5'd4, X_MEMA = 5'd5, X_LD = 5'd6, X_ST = 5'd7,
                   X_SWAP = 5'd8, X_JR = 5'd9, X_TMO = 5'd10, X_STAT = 5'd11,
                   X_TXCFG = 5'd12, X_TXDIV = 5'd13, X_TXPUSH = 5'd14, X_TXWAIT = 5'd15,
                   X_RXCFG = 5'd16, X_RXDIV = 5'd17, X_RXPOP = 5'd18, X_CRCCFG = 5'd19,
                   X_CRCW = 5'd20, X_CRCRDL = 5'd21, X_CRCRDH = 5'd22, X_CRCSETL = 5'd23,
                   X_CRCSETH = 5'd24, X_POLL = 5'd25, X_POLH = 5'd26, X_TSTART = 5'd27,
                   X_TSTOP = 5'd28, X_TID = 5'd29, X_PW = 5'd30, X_PR = 5'd31;

  // peripheral ports (PW #p, rb / PR ra, #p)
  localparam [2:0] P_FIFO = 3'd0, P_FCFG = 3'd1;

  localparam [2:0] K_EDGE = 3'd0, K_CMP = 3'd1, K_LD = 3'd2, K_TXPUSH = 3'd3,
                   K_TXWAIT = 3'd4, K_RXPOP = 3'd5, K_CRCW = 3'd6, K_CRCRD = 3'd7;

  // ------------------------------------------------------------------
  // Thread scheduling state
  // ------------------------------------------------------------------
  reg        dual;            // T1 running: barrel-interleave T0/T1
  reg        tsel;            // dual mode: thread that owns this cycle
  reg [9:0]  pc0, pc1;
  reg        flush0, flush1;  // next decode slot of that thread is a bubble
  reg [1:0]  zf, nf;          // per-thread flags

  wire       cur  = dual & tsel;
  wire       fl_c = cur ? flush1 : flush0;

  // per-thread blocking-op state (index = thread)
  reg  [1:0]  st;             // stalled
  reg  [5:0]  sk;             // kind          (3 bits per thread)
  reg  [5:0]  srd;            // dest register (3 bits per thread)
  reg  [31:0] sop;            // latched operand (CMP target / push data)
  reg  [7:0]  wsel;           // {bank, pin}   (4 bits per thread)
  reg  [5:0]  wcond;          // edge/level condition (3 bits per thread)
  reg  [1:0]  wprev;          // pin value last cycle
  reg  [1:0]  whit;           // sticky event seen
  reg  [31:0] sto;            // timeout counters
  reg  [1:0]  sto_en;
  reg  [31:0] ldh;            // LD data captured the cycle after issue
  reg  [1:0]  ldq;            // LD issued by this thread last cycle
  reg  [31:0] tmo;            // blocking-op timeout reload (0 = none)
  reg  [1:0]  fge;            // the pending LD is a FIFO pop of an empty FIFO

  wire        st_c   = st[cur];
  wire [15:0] tmo_c  = cur ? tmo[31:16] : tmo[15:0];
  wire        fge_c  = fge[cur];
  wire [2:0]  sk_c   = cur ? sk[5:3]    : sk[2:0];
  wire [2:0]  srd_c  = cur ? srd[5:3]   : srd[2:0];
  wire [15:0] sop_c  = cur ? sop[31:16] : sop[15:0];
  wire [15:0] ldh_c  = cur ? ldh[31:16] : ldh[15:0];
  wire        zc     = zf[cur];
  wire        nc     = nf[cur];

  // shared state
  reg [15:0] cnt;             // free-running cycle counter
  reg [15:0] sr;              // shift register
  reg [2:0]  sr_po, sr_pi;    // SR output / input pin
  reg        sr_lsb;
  reg [9:0]  waddr;           // MEM_W pointer

  // FIFO (port 0): words in SRAM [base, base+size), pointers here. Size is
  // 2 << fn (2..256); the block is size-aligned (base's low bits ignored).
  reg [9:0]  fbase;
  reg [2:0]  fn;
  reg [8:0]  fwp, frp;        // free-running write / read counts
  reg [6:0]  fdrop;           // words a full FIFO dropped (modulo 128)
  wire [8:0] fsize  = 9'd2 << fn;
  wire [8:0] fmask  = fsize - 9'd1;
  wire [8:0] fcount = fwp - frp;
  wire       fempty = (fcount == 9'd0);
  wire       ffull  = (fcount == fsize);
  wire [9:0] fblock  = fbase & ~{1'b0, fmask};
  wire [9:0] faddr_w = fblock | {1'b0, fwp & fmask};
  wire [9:0] faddr_r = fblock | {1'b0, frp & fmask};

  // ------------------------------------------------------------------
  // Decode
  // ------------------------------------------------------------------
  wire [15:0] ir    = fl_c ? 16'h7000 : imem_rd;      // bubble decodes as NOP
  wire        exec  = !st_c && !fl_c;                 // an instruction issues
  wire [9:0]  pc_c  = cur ? pc1 : pc0;
  wire [9:0]  apc1  = dual ? pc_c + 10'd1 : pc0;      // issuing address + 1

  wire [4:0]  op    = ir[15:11];
  wire [2:0]  f_a   = ir[10:8];
  wire [2:0]  f_b   = ir[7:5];
  wire [2:0]  f_c   = ir[4:2];
  wire [7:0]  imm8  = ir[7:0];
  wire [4:0]  sub   = ir[4:0];
  wire [8:0]  rel9  = ir[10:2];
  wire        ext   = (op == OP_EXT);
  wire [2:0]  msel  = ir[10:8];                        // SETM / CLRM selector

  wire x_srld  = ext && sub == X_SRLD,   x_srrd  = ext && sub == X_SRRD;
  wire x_xfer  = ext && sub == X_XFER,   x_sercf = ext && sub == X_SERCFG;
  wire x_outw  = ext && sub == X_OUTW,   x_mema  = ext && sub == X_MEMA;
  wire x_ld    = ext && sub == X_LD,     x_st    = ext && sub == X_ST;
  wire x_swap  = ext && sub == X_SWAP,   x_jr    = ext && sub == X_JR;
  wire x_tmo   = ext && sub == X_TMO,    x_stat  = ext && sub == X_STAT;
  wire x_txp   = ext && sub == X_TXPUSH, x_txw   = ext && sub == X_TXWAIT;
  wire x_rxp   = ext && sub == X_RXPOP,  x_crcw  = ext && sub == X_CRCW;
  wire x_crcrl = ext && sub == X_CRCRDL, x_crcrh = ext && sub == X_CRCRDH;
  wire x_tstrt = ext && sub == X_TSTART, x_tstop = ext && sub == X_TSTOP;
  wire x_tid   = ext && sub == X_TID;
  wire x_pw    = ext && sub == X_PW,     x_pr    = ext && sub == X_PR;
  wire x_put   = x_pw && f_a == P_FIFO,  x_fcfg  = x_pw && f_a == P_FCFG;
  wire x_get   = x_pr && f_b == P_FIFO,  x_fstat = x_pr && f_b == P_FCFG;
  wire x_perw  = ext && (sub == X_TXCFG || sub == X_TXDIV || sub == X_RXCFG ||
                         sub == X_RXDIV || sub == X_CRCCFG || sub == X_CRCSETL ||
                         sub == X_CRCSETH || sub == X_POLL || sub == X_POLH);

  // ------------------------------------------------------------------
  // Register files (one per thread) and operand selection
  // ------------------------------------------------------------------
  reg [2:0] ra_i, rb_i;
  always @(*) begin
    ra_i = f_a;                       // I format / EXT ra
    rb_i = f_b;                       // EXT rb
    if (op >= OP_ADDS && op <= OP_MOV) begin
      ra_i = f_b;                     // R format rs
      rb_i = f_c;                     // R format rt
    end else if (op == OP_RET) begin
      ra_i = 3'd7;
    end
  end

  wire        wb_en;
  wire [2:0]  wb_a;
  wire [15:0] wb_d;
  wire        t1_clr = exec && x_tstrt;
  wire [15:0] qa0, qb0, qa1, qb1, qc0, qc1;

  tt_um_msf_regfile u_rf (
      .clk(clk), .rst_n(rst_n), .clr(1'b0),
      .ra(ra_i), .rb(rb_i), .rc(dbg_sel[3:1]), .we(wb_en && !cur), .wa(wb_a), .wd(wb_d),
      .qa(qa0), .qb(qb0), .qc(qc0));

  tt_um_msf_regfile u_rf1 (
      .clk(clk), .rst_n(rst_n), .clr(t1_clr),
      .ra(ra_i), .rb(rb_i), .rc(dbg_sel[3:1]), .we(wb_en && cur), .wa(wb_a), .wd(wb_d),
      .qa(qa1), .qb(qb1), .qc(qc1));

  wire [15:0] qa = cur ? qa1 : qa0;
  wire [15:0] qb = cur ? qb1 : qb0;

  // ------------------------------------------------------------------
  // ALU
  // ------------------------------------------------------------------
  reg  [3:0]  alu_op;
  reg  [15:0] alu_b;
  wire [15:0] alu_y;
  wire        alu_z, alu_n;
  always @(*) begin
    alu_b = qb;
    case (op)
      OP_ADDS: alu_op = 4'd0;
      OP_SUBS: alu_op = 4'd1;
      OP_AND:  alu_op = 4'd2;
      OP_OR:   alu_op = 4'd3;
      OP_XOR:  alu_op = 4'd4;
      OP_SHL:  begin alu_op = 4'd5; alu_b = {13'd0, f_c}; end
      OP_SHR:  begin alu_op = 4'd6; alu_b = {13'd0, f_c}; end
      OP_INCR: alu_op = 4'd7;
      OP_DECR: alu_op = 4'd8;
      default: alu_op = 4'd9;          // MOV / pass-through
    endcase
  end

  tt_um_msf_alu u_alu (.a(qa), .b(alu_b), .op(alu_op), .y(alu_y), .z(alu_z), .n(alu_n));

  wire flag_op = (op == OP_ADDS) || (op == OP_SUBS) || (op == OP_INCR) || (op == OP_DECR);

  // ------------------------------------------------------------------
  // Blocking ops
  // ------------------------------------------------------------------
  // a FIFO pop is a load from the read slot (an empty FIFO reads nothing)
  wire is_blk = (op == OP_WEDGE) || (op == OP_WCMP) || x_ld || x_get || x_txp ||
                x_txw || x_rxp || x_crcw || x_crcrl;
  wire blk_ready = (x_txp && !tx_full) || (x_txw && !tx_busy) || (x_rxp && rx_avail) ||
                   ((x_crcw || x_crcrl) && !crc_busy);
  wire enter   = exec && is_blk && !blk_ready;   // WAIT_* and LD are never "ready"
  wire blk_now = exec && is_blk &&  blk_ready;   // completes in its issue cycle

  reg [2:0] kind_i;
  always @(*) begin
    if (op == OP_WEDGE)   kind_i = K_EDGE;
    else if (op == OP_WCMP) kind_i = K_CMP;
    else if (x_ld || x_get) kind_i = K_LD;
    else if (x_txp)       kind_i = K_TXPUSH;
    else if (x_txw)       kind_i = K_TXWAIT;
    else if (x_rxp)       kind_i = K_RXPOP;
    else if (x_crcw)      kind_i = K_CRCW;
    else                  kind_i = K_CRCRD;
  end

  wire ent_pin = imm8[3] ? ui_in[imm8[2:0]] : uio_in[imm8[2:0]];

  // per-thread release conditions, evaluated every cycle
  wire [1:0] cond_now;       // condition true this cycle (incl. sticky event)
  wire [1:0] evt;            // event to latch (edge / CNT match)
  wire [1:0] tmo_now;        // timeout reached
  wire [1:0] pin_t;
  genvar t;
  generate
    for (t = 0; t < 2; t = t + 1) begin : g_cond
      wire [3:0]  ws = wsel[4*t +: 4];
      wire [2:0]  wc = wcond[3*t +: 3];
      wire [2:0]  k  = sk[3*t +: 3];
      wire        p  = ws[3] ? ui_in[ws[2:0]] : uio_in[ws[2:0]];
      wire        pv = wprev[t];
      reg         e;
      always @(*) begin
        case (wc)
          3'd1:    e = !pv &&  p;      // rising
          3'd2:    e =  pv && !p;      // falling
          3'd4:    e = !p;             // level 0
          3'd5:    e =  p;             // level 1
          default: e = (p != pv);      // any edge
        endcase
      end
      wire cmp = (cnt == sop[16*t +: 16]);
      assign pin_t[t]   = p;
      assign evt[t]     = (k == K_EDGE) ? e : (k == K_CMP) ? cmp : 1'b0;
      assign tmo_now[t] = sto_en[t] && (sto[16*t +: 16] == 16'd0);
      assign cond_now[t] =
          (k == K_EDGE || k == K_CMP) ? (evt[t] || whit[t]) :
          (k == K_LD)     ? 1'b1 :
          (k == K_TXPUSH) ? !tx_full :
          (k == K_TXWAIT) ? !tx_busy :
          (k == K_RXPOP)  ? rx_avail :
                            !crc_busy;          // K_CRCW, K_CRCRD
    end
  endgenerate

  wire rel    = st_c && (cond_now[cur] || tmo_now[cur]);   // release this cycle
  wire rel_ok = cond_now[cur];                             // released by its condition
  wire [15:0] ld_data = ldq[cur] ? b_rd : ldh_c;

  // ------------------------------------------------------------------
  // Register write-back (always for the thread that owns the cycle)
  // ------------------------------------------------------------------
  wire [15:0] stat = {5'd0, cap_stat[13], cap_stat[15], ffull, !fempty, cur, dual,
                      crc_busy, rx_overrun, rx_avail, tx_busy, tx_full};

  reg        wbx_en;
  reg [15:0] wbx_d;
  always @(*) begin
    wbx_en = 1'b1;
    wbx_d  = alu_y;
    case (op)
      OP_LDR:  wbx_d = {8'h00, imm8};
      OP_ADDS, OP_SUBS, OP_AND, OP_OR, OP_XOR, OP_SHL, OP_SHR, OP_MOV,
      OP_INCR, OP_DECR: wbx_d = alu_y;
      OP_PINR: wbx_d = {8'h00, uio_in};
      OP_CNTR: wbx_d = cnt;
      OP_INRD: wbx_d = {8'h00, ui_in};
      OP_CALL: wbx_d = {6'd0, apc1};
      OP_LDH:  wbx_d = {imm8, qa[7:0]};
      OP_EXT: begin
        case (sub)
          X_SRRD:   wbx_d = sr;
          X_SWAP:   wbx_d = {qb[7:0], qb[15:8]};
          X_STAT:   wbx_d = stat;
          X_CRCRDH: wbx_d = crc_hi;
          X_TID:    wbx_d = {15'd0, cur};
          X_PR:     begin                       // port reads (#0 = pop: LD path)
            wbx_en = !x_get;
            case (f_b)
              P_FCFG:  wbx_d = {fdrop, fcount};
              3'd2:    wbx_d = cap_stat;
              3'd3:    wbx_d = {6'd0, cap_tpos};
              default: wbx_d = 16'd0;
            endcase
          end
          X_RXPOP:  begin wbx_d = rx_word; wbx_en = rx_avail;  end
          X_CRCRDL: begin wbx_d = crc_lo;  wbx_en = !crc_busy; end
          default:  wbx_en = 1'b0;
        endcase
      end
      default: wbx_en = 1'b0;
    endcase
  end

  wire rel_wb = rel && rel_ok && ((sk_c == K_LD && !fge_c) || sk_c == K_RXPOP ||
                                  sk_c == K_CRCRD);
  assign wb_en = (exec && wbx_en) || rel_wb;
  assign wb_a  = rel_wb ? srd_c : (op == OP_CALL) ? 3'd7 : f_a;
  assign wb_d  = !rel_wb ? wbx_d :
                 (sk_c == K_LD)    ? ld_data :
                 (sk_c == K_RXPOP) ? rx_word : crc_lo;

  // ------------------------------------------------------------------
  // Control flow
  // ------------------------------------------------------------------
  wire br_take = (op == OP_JZ && zc) || (op == OP_JNZ && !zc) ||
                 (op == OP_JP && !nc) || (op == OP_JN && nc);
  wire redirect = exec && (op == OP_JMP || op == OP_CALL || op == OP_RET || x_jr || br_take);
  reg [9:0] tgt;
  always @(*) begin
    if (op == OP_JMP || op == OP_CALL) tgt = ir[9:0];
    else if (op == OP_RET)             tgt = qa[9:0];
    else if (x_jr)                     tgt = qb[9:0];
    else                               tgt = apc1 + {rel9[8], rel9};
  end

  wire do_tstart = exec && x_tstrt;
  wire do_tstop  = exec && x_tstop && dual;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pc0 <= 10'd0; pc1 <= 10'd0; flush0 <= 1'b1; flush1 <= 1'b0;
      dual <= 1'b0; tsel <= 1'b0;
    end else if (!dual) begin
      // ---- single-thread mode: T0 owns every cycle ----
      if (st[0]) begin
        if (rel) pc0 <= pc0 + 10'd1;            // release: mem[W+1] issues next
      end else if (redirect) begin
        pc0 <= tgt; flush0 <= 1'b1;
      end else if (enter || do_tstart) begin
        flush0 <= 1'b0;                        // hold pc0 = W+1
      end else begin
        pc0 <= pc0 + 10'd1; flush0 <= 1'b0;
      end
      if (do_tstart) begin
        dual <= 1'b1; tsel <= 1'b0;            // T0 issues next cycle, T1 after
        pc1 <= qb[9:0]; flush1 <= 1'b0;
      end
    end else begin
      // ---- dual-thread mode ----
      tsel <= ~tsel;
      if (!cur) begin
        if (!st[0]) begin
          if (flush0)        flush0 <= 1'b0;
          else if (redirect) pc0 <= tgt;
          else               pc0 <= pc0 + 10'd1;
        end
      end else begin
        if (!st[1]) begin
          if (flush1)        flush1 <= 1'b0;
          else if (redirect) pc1 <= tgt;
          else               pc1 <= pc1 + 10'd1;
        end
      end
      if (do_tstart) begin
        pc1    <= qb[9:0];
        flush1 <= !cur;            // T1's word in flight is stale if T0 restarted it
      end
      if (do_tstop) begin
        dual <= 1'b0; tsel <= 1'b0;
        if (cur) begin
          if (!st[0]) pc0 <= pc0 + 10'd1;       // back to fetch-ahead
        end else begin
          flush0 <= 1'b1;                      // word in flight belongs to T1
        end
      end
    end
  end

  assign imem_raddr = dual ? (tsel ? pc0 : pc1) : pc0;

  // ------------------------------------------------------------------
  // Flags and per-thread blocking state
  // ------------------------------------------------------------------
  integer i;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      zf <= 2'b00; nf <= 2'b00;
      st <= 2'b00; sk <= 6'd0; srd <= 6'd0; sop <= 32'd0; wsel <= 8'd0;
      wcond <= 6'd0; wprev <= 2'b00; whit <= 2'b00; sto <= 32'd0; sto_en <= 2'b00;
      ldh <= 32'd0; ldq <= 2'b00; fge <= 2'b00;
    end else begin
      // flags of the owning thread
      if (exec && flag_op) begin
        zf[cur] <= alu_z;
        nf[cur] <= alu_n;
      end else if (blk_now) begin
        zf[cur] <= 1'b0;
      end else if (exec && x_put) begin
        zf[cur] <= ffull;                      // Z = 1: full, the word was dropped
      end else if (rel) begin
        zf[cur] <= !rel_ok || fge_c;           // a pop of an empty FIFO: Z = 1
      end

      for (i = 0; i < 2; i = i + 1) begin
        ldq[i] <= exec && (x_ld || x_get) && (cur == i);
        if (ldq[i]) ldh[16*i +: 16] <= b_rd;
        if (st[i]) begin
          if (cur == i && rel) st[i] <= 1'b0;
          if (sto_en[i] && sto[16*i +: 16] != 16'd0) sto[16*i +: 16] <= sto[16*i +: 16] - 16'd1;
          wprev[i] <= pin_t[i];
          if (evt[i]) whit[i] <= 1'b1;
        end else if (cur == i && enter) begin
          st[i]               <= 1'b1;
          sk[3*i +: 3]        <= kind_i;
          srd[3*i +: 3]       <= f_a;
          sop[16*i +: 16]     <= (op == OP_WCMP) ? qa : qb;
          wsel[4*i +: 4]      <= imm8[3:0];
          wcond[3*i +: 3]     <= imm8[6:4];
          wprev[i]            <= ent_pin;
          whit[i]             <= 1'b0;
          sto[16*i +: 16]     <= tmo_c;
          sto_en[i]           <= (tmo_c != 16'd0);
          fge[i]              <= x_get && fempty;
        end
      end

      if (do_tstart) begin               // fresh T1 (wins over the above)
        zf[1] <= 1'b0; nf[1] <= 1'b0; st[1] <= 1'b0; whit[1] <= 1'b0; ldq[1] <= 1'b0;
      end
    end
  end

  // ------------------------------------------------------------------
  // Shared datapath state: CNT, SR, MEM_W pointer; per-thread TMO
  // ------------------------------------------------------------------
  wire sr_in  = uio_in[sr_pi];
  wire sr_out = sr_lsb ? sr[0] : sr[15];

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cnt <= 16'd0; sr <= 16'd0; sr_po <= 3'd7; sr_pi <= 3'd7; sr_lsb <= 1'b0;
      waddr <= 10'd128; tmo <= 32'hFFFF_FFFF;
      fbase <= 10'd64; fn <= 3'd5; fwp <= 9'd0; frp <= 9'd0; fdrop <= 7'd0;
    end else begin
      cnt <= cnt + 16'd1;
      if (exec) begin
        if (x_srld) sr <= qb;
        else if (op == OP_SHOUT)
          sr <= sr_lsb ? {qa[0], sr[15:1]} : {sr[14:0], qa[0]};
        else if (op == OP_SHIN || x_xfer)
          sr <= sr_lsb ? {sr_in, sr[15:1]} : {sr[14:0], sr_in};
        if (x_sercf) begin
          sr_po <= qb[2:0]; sr_pi <= qb[5:3]; sr_lsb <= qb[6];
        end
        if (op == OP_MEMW) waddr <= waddr + 10'd1;
        else if (x_mema)   waddr <= qb[9:0];
        if (x_tmo) tmo[16*cur +: 16] <= qb;
        if (x_tstrt) tmo[31:16] <= tmo_c;      // T1 starts with its starter's TMO
        if (x_put) begin
          if (!ffull) fwp   <= fwp + 9'd1;
          else        fdrop <= fdrop + 7'd1;   // modulo 128
        end
        if (x_get && !fempty) frp <= frp + 9'd1;
        if (x_fcfg) begin
          fbase <= qb[9:0]; fn <= qb[12:10];
          fwp <= 9'd0; frp <= 9'd0; fdrop <= 7'd0;
        end
      end
    end
  end

  // ------------------------------------------------------------------
  // Pin writes (one per cycle, executing thread only)
  // ------------------------------------------------------------------
  always @(*) begin
    out_we = 1'b0; out_d = out_q;
    oe_we  = 1'b0; oe_d  = oe_q;
    uo_we  = 1'b0; uo_d  = uo_q;
    if (exec) begin
      case (op)
        OP_PINW: begin out_we = 1'b1; out_d = qa[7:0]; end
        OP_SETM: case (msel)
          3'd0: begin oe_we  = 1'b1; oe_d  = oe_q  | imm8; end
          3'd1: begin out_we = 1'b1; out_d = out_q | imm8; end
          3'd2: begin out_we = 1'b1; out_d = out_q ^ imm8; end
          3'd3: begin uo_we  = 1'b1; uo_d  = uo_q  | imm8; end
          3'd4: begin uo_we  = 1'b1; uo_d  = uo_q  ^ imm8; end
          3'd5: begin oe_we  = 1'b1; oe_d  = oe_q  ^ imm8; end
          default: ;
        endcase
        OP_CLRM: case (msel)
          3'd0: begin oe_we  = 1'b1; oe_d  = oe_q  & ~imm8; end
          3'd1: begin out_we = 1'b1; out_d = out_q & ~imm8; end
          3'd2: begin out_we = 1'b1; out_d = imm8;          end
          3'd3: begin uo_we  = 1'b1; uo_d  = uo_q  & ~imm8; end
          3'd4: begin uo_we  = 1'b1; uo_d  = imm8;          end
          3'd5: begin oe_we  = 1'b1; oe_d  = imm8;          end
          default: ;
        endcase
        OP_SHOUT: begin
          out_we = 1'b1; out_d = out_q; out_d[sr_po] = sr_out;
        end
        OP_EXT: begin
          if (x_xfer) begin out_we = 1'b1; out_d = out_q; out_d[sr_po] = sr_out; end
          if (x_outw) begin uo_we = 1'b1; uo_d = qb[7:0]; end
        end
        default: ;
      endcase
    end
  end

  // ------------------------------------------------------------------
  // SRAM port B and peripheral bus
  // ------------------------------------------------------------------
  wire do_memw = exec && (op == OP_MEMW);
  wire do_st   = exec && x_st;
  wire do_ld   = exec && x_ld;
  wire do_put  = exec && x_put && !ffull;
  wire do_get  = exec && x_get && !fempty;
  assign b_en   = do_memw || do_st || do_ld || do_put || do_get;
  assign b_we   = do_memw || do_st || do_put;
  assign b_addr = do_memw ? waddr : do_st ? qa[9:0] : do_put ? faddr_w :
                  do_get ? faddr_r : qb[9:0];
  assign b_wd   = do_memw ? qa : qb;

  // ports 2-7 belong to the capture engine
  assign pw_we   = exec && x_pw && (f_a >= 3'd2);
  assign pw_port = f_a;
  assign pw_data = qb;

  wire per_rel = rel && rel_ok && (sk_c == K_TXPUSH || sk_c == K_CRCW);
  assign per_we  = (exec && x_perw) || (blk_now && (x_txp || x_crcw)) || per_rel;
  assign per_sub = per_rel ? ((sk_c == K_TXPUSH) ? X_TXPUSH : X_CRCW) : sub;
  assign per_wd  = per_rel ? sop_c : qb;
  assign rx_pop  = (blk_now && x_rxp) || (rel && rel_ok && sk_c == K_RXPOP);

  // ------------------------------------------------------------------
  // Debug / testbench visibility (only the debug view depends on these)
  // ------------------------------------------------------------------
  wire [9:0] pc_p      = pc0;        // v1 name, T0 fetch pointer
  wire       stall     = st[0];
  wire       z         = zf[0];
  wire       n         = nf[0];
  wire       dbg_issue = exec;
  wire       dbg_thr   = cur;
  wire [9:0] dbg_pc    = apc1 - 10'd1;
  wire       dbg_rel   = rel;

  // ------------------------------------------------------------------
  // Debug view: one byte of state chosen by dbg_sel (isa.md §8). The last
  // issued instruction is held so the view can be read between clock steps.
  // ------------------------------------------------------------------
  reg [9:0]  lpc;
  reg [15:0] lir;
  reg        lthr;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      lpc <= 10'd0; lir <= 16'h0; lthr <= 1'b0;
    end else if (exec) begin
      lpc <= dbg_pc; lir <= ir; lthr <= cur;
    end
  end

  wire [15:0] dbg_reg = dbg_sel[4] ? qc1 : qc0;
  always @(*) begin
    if (!dbg_sel[5]) dbg_q = dbg_sel[0] ? dbg_reg[15:8] : dbg_reg[7:0];
    else case (dbg_sel[4:0])
      5'h00: dbg_q = lpc[7:0];
      5'h01: dbg_q = {2'd0, st[1], st[0], dual, lthr, lpc[9:8]};
      5'h02: dbg_q = lir[7:0];
      5'h03: dbg_q = lir[15:8];
      5'h04: dbg_q = {4'd0, nf[1], zf[1], nf[0], zf[0]};
      5'h05: dbg_q = cnt[7:0];
      5'h06: dbg_q = cnt[15:8];
      5'h07: dbg_q = sr[7:0];
      5'h08: dbg_q = sr[15:8];
      5'h09: dbg_q = out_q;
      5'h0A: dbg_q = oe_q;
      5'h0B: dbg_q = uo_q;
      5'h0C: dbg_q = fcount[7:0];
      5'h0D: dbg_q = {fdrop, fcount[8]};
      default: dbg_q = 8'h00;
    endcase
  end

`ifdef FORMAL
  // ==================================================================
  // Formal properties (formal/run_formal.py, yosys `sat` BMC + induction).
  // imem_rd, b_rd, pins and peripheral status are free inputs, so every
  // property holds for every program and every environment.
  // ==================================================================
  reg f_init = 1'b1;
  always @(posedge clk) f_init <= 1'b0;
  always @(*) assume(rst_n == !f_init);          // reset in cycle 0 only

  // ---- ghost: where did the word being decoded come from? ----
  reg [9:0] f_faddr;
  reg       f_fthr;
  always @(posedge clk) begin
    f_faddr <= imem_raddr;
    f_fthr  <= dual ? ~tsel : 1'b0;
  end

  // ---- ghost: per-thread expected next issue address ----
  reg [9:0] f_next0, f_next1;
  reg       f_has0, f_has1;
  wire [9:0] f_succ = redirect ? tgt : dbg_pc + 10'd1;
  always @(posedge clk) begin
    if (f_init) begin
      f_has0 <= 1'b0; f_has1 <= 1'b0;
    end else begin
      if (exec && !cur) begin f_next0 <= f_succ; f_has0 <= 1'b1; end
      if (exec &&  cur) begin f_next1 <= f_succ; f_has1 <= 1'b1; end
      if (do_tstart)    begin f_next1 <= qb[9:0]; f_has1 <= 1'b1; end
    end
  end

  // ---- ghost: per-thread blocking-op witnesses (independent re-statement
  //      of isa.md section 5, not shared with the design's own logic) ----
  wire [1:0]  f_seen_w, f_evt_w, f_single_w;
  wire [33:0] f_cnt_w;       // 17 bits per thread
  wire [31:0] f_tmo_w;
  generate
    for (t = 0; t < 2; t = t + 1) begin : g_fv
      reg [15:0] tgt, tm;
      reg [16:0] n;
      reg        seen, prev, single;   // single: issued in single-thread mode
      reg [2:0]  kind, cond;
      reg [3:0]  sel;
      wire p = sel[3] ? ui_in[sel[2:0]] : uio_in[sel[2:0]];
      reg  ev;
      always @(*) begin
        if (kind == K_CMP) ev = (cnt == tgt);
        else case (cond)
          3'd1:    ev = !prev && p;
          3'd2:    ev = prev && !p;
          3'd4:    ev = !p;
          3'd5:    ev = p;
          default: ev = p != prev;
        endcase
      end
      always @(posedge clk) begin
        if (cur == t && enter) begin
          kind <= kind_i; tgt <= qa; tm <= tmo_c; n <= 17'd0; seen <= 1'b0; single <= !dual;
          sel <= imm8[3:0]; cond <= imm8[6:4]; prev <= ent_pin;
        end else if (st[t]) begin
          n <= n + 17'd1;
          prev <= p;
          if (ev) seen <= 1'b1;
        end
      end
      assign f_seen_w[t]        = seen;
      assign f_single_w[t]      = single;
      assign f_evt_w[t]         = ev;
      assign f_cnt_w[17*t +: 17] = n;
      assign f_tmo_w[16*t +: 16] = tm;
    end
  endgenerate
  wire [16:0] f_cnt_c = cur ? f_cnt_w[33:17] : f_cnt_w[16:0];
  wire [15:0] f_tmo_c = cur ? f_tmo_w[31:16] : f_tmo_w[15:0];

  always @(*) begin
    if (!f_init) begin
      // F1 fetch/issue alignment: the issuing word was fetched last cycle
      //    from the issuing thread's address (catches B1-type pc bugs)
      if (exec) assert(f_faddr == dbg_pc && f_fthr == cur);
      // F2 no skipped / repeated instruction per thread
      if (exec && !cur && f_has0) assert(dbg_pc == f_next0);
      if (exec &&  cur && f_has1) assert(dbg_pc == f_next1);
      // F3 WAIT release causes (catches B2): a WAIT completes by its own
      //    event, or by timeout after exactly TMO+1 stalled cycles
      if (rel && (sk_c == K_EDGE || sk_c == K_CMP) && rel_ok)
        assert(f_seen_w[cur] || f_evt_w[cur]);
      if (rel && !rel_ok) begin
        assert(f_tmo_c != 16'd0);
        // f_cnt = stalled cycles before this one; release in stall cycle TMO+1
        // exactly when the whole stall is in single-thread mode (a single-mode
        // stall cannot see dual mode: only a running thread can TSTART), and
        // in the next own slot after expiry otherwise
        assert(f_cnt_c >= {1'b0, f_tmo_c});
        if (f_single_w[cur]) assert(f_cnt_c == {1'b0, f_tmo_c});
        else assert(f_cnt_c <= {1'b0, f_tmo_c} + 17'd1);
      end
      // F4 single mode never runs T1; T1 only exists in dual mode
      if (!dual) assert(!cur);
      // F5 side effects only from an issuing (or completing) instruction
      if (out_we || oe_we || uo_we || b_en) assert(exec);
      if (wb_en)  assert(exec || rel);
      if (per_we) assert(exec || rel);
      if (exec && rel) assert(0);
    end
  end

  // F6 flags change only through the owning thread's flag ops / blocking ops
  always @(posedge clk) begin
    if (!f_init && !$past(f_init)) begin
      if (zf[0] != $past(zf[0]) || nf[0] != $past(nf[0]))
        assert(!$past(cur) && ($past(exec && flag_op) || $past(blk_now) || $past(rel) ||
                               $past(exec && x_put)));
      if (zf[1] != $past(zf[1]) || nf[1] != $past(nf[1]))
        assert(($past(cur) && ($past(exec && flag_op) || $past(blk_now) || $past(rel) ||
                               $past(exec && x_put)))
               || $past(do_tstart));
      // F8 the FIFO never holds more words than its size, and its pointers
      //    move only by a push that fits, a pop that finds a word, or a reset
      assert(fcount <= fsize);
      if (fwp != $past(fwp))
        assert($past(exec && x_fcfg) || ($past(exec && x_put && !ffull) && fwp == $past(fwp) + 9'd1));
      if (frp != $past(frp))
        assert($past(exec && x_fcfg) || ($past(exec && x_get && !fempty) && frp == $past(frp) + 9'd1));
    end
  end

  // reachability (the properties above are not vacuous)
  always @(*) if (!f_init) begin
    cover(rel && sk_c == K_EDGE && rel_ok);
    cover(rel && sk_c == K_CMP && rel_ok);
    cover(rel && !rel_ok);
    cover(rel && sk_c == K_LD && cur);
    cover(dual && exec && cur);
    cover(do_tstop && cur);
    cover(exec && x_put && ffull);
    cover(rel && sk_c == K_LD && fge_c);
  end
`endif
endmodule

`default_nettype wire
