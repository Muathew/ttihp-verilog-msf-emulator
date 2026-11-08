// SPDX-License-Identifier: Apache-2.0
// msfcore control unit (ISA v2.1, docs/isa.md): fetch / decode / execute
// for four hardware threads, blocking-op (stall) engine, shift register,
// data-RAM port, pin writes and the peripheral bus.
//
// Pipeline (v3). IMEM has a registered read. The word that issues in cycle
// N+1 arrives in cycle N: a decode stage (D) registers it with the operands it
// names, read from the register file of the thread it was fetched for, and the
// instruction issues from those registers in N+1 (E), with the one register E
// wrote in cycle N forwarded. To deliver words a cycle early, the SRAM is given
// the fetch pointers' next values (pcs_n, own_n, run_n), not their registers:
// the state machine below is v2.2's, unchanged, so every instruction issues on
// the same cycle as in v2.2 and costs what isa.md says (docs/plan-v3.md section 1).
// In v2.2's terms, which the rest of this header uses: the word fetched with
// imem_raddr in cycle N is decoded in cycle N+1.
//  * Single-thread mode (only T0 runs): T0 owns every cycle. During execution
//    pc0 = address+1 (fetch-ahead). Taken redirects insert one bubble (bub).
//    Blocking op at W: pc0 is held at W+1 on entry so mem[W+1] is re-fetched
//    every stall cycle; on the release cycle pc0 advances to W+2, so mem[W+1]
//    executes exactly once with pc0 == its address+1.
//  * Multi-thread mode (T0 and any of T1-T3 running): each cycle has an
//    owner, the next running thread after the last (T0..T3, cyclically).
//    The word for the next owner is fetched in the current cycle, so pc_t is
//    simply the address of t's next instruction and redirects cost nothing.
//    When TSTART / TSTOP changes the running set, the next cycle belongs to
//    the thread whose word is in flight if it still runs and was not
//    restarted; otherwise to the next running thread, as a bubble.
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
    input  wire [6:0]  dbg_sel,      // {page, view}
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
  localparam [2:0] P_FIFO = 3'd0, P_FCFG = 3'd1, P_LATE = 3'd7;

  localparam [2:0] K_EDGE = 3'd0, K_CMP = 3'd1, K_LD = 3'd2, K_TXPUSH = 3'd3,
                   K_TXWAIT = 3'd4, K_RXPOP = 3'd5, K_CRCW = 3'd6, K_CRCRD = 3'd7;

  // ------------------------------------------------------------------
  // Thread scheduling state
  // ------------------------------------------------------------------
  reg [3:0]  run;             // running threads (T0 always)
  reg [1:0]  own;             // thread that owns this cycle (T0 in single mode)
  reg        bub;             // this cycle's decode slot is a bubble
  reg [39:0] pcs;             // per-thread PC (10 bits each)
  reg [3:0]  zf, nf;          // per-thread flags

  wire       multi = |run[3:1];
  wire [1:0] cur   = own;
  wire       fl_c  = bub;
  wire [9:0] pc0   = pcs[9:0];

  // the next running thread after a, in the order T0..T3 (a if alone)
  function [1:0] next_after(input [1:0] a, input [3:0] r);
    reg [1:0] x;
    integer   k;
    begin
      next_after = a;
      for (k = 3; k >= 1; k = k - 1) begin
        x = a + k;
        if (r[x]) next_after = x;
      end
    end
  endfunction

  wire [1:0] fth = next_after(own, run);   // multi mode: whose word is fetched now

  // per-thread blocking-op state (index = thread)
  reg  [3:0]  st;             // stalled
  reg  [11:0] sk;             // kind          (3 bits per thread)
  reg  [11:0] srd;            // dest register (3 bits per thread)
  reg  [63:0] sop;            // latched operand (CMP target / push data)
  reg  [15:0] wsel;           // {bank, pin}   (4 bits per thread)
  reg  [11:0] wcond;          // edge/level condition (3 bits per thread)
  reg  [3:0]  wprev;          // pin value last cycle
  reg  [3:0]  whit;           // sticky event seen
  reg  [63:0] sto;            // timeout counters
  reg  [3:0]  sto_en;
  reg  [63:0] ldh;            // LD data captured the cycle after issue
  reg  [3:0]  ldq;            // LD issued by this thread last cycle
  reg  [63:0] tmo;            // blocking-op timeout reload (0 = none)
  reg  [3:0]  fge;            // the pending LD is a FIFO pop of an empty FIFO
  reg  [3:0]  wlate;          // the pending WAIT_CMP's target had passed at issue
  reg  [3:0]  late;           // sticky LATE flags (STAT bit 14, port 7)

  wire        st_c   = st[cur];
  wire [15:0] tmo_c  = tmo[16*cur +: 16];
  wire        fge_c  = fge[cur];
  wire        wlate_c = wlate[cur];
  wire [2:0]  sk_c   = sk[3*cur +: 3];
  wire [2:0]  srd_c  = srd[3*cur +: 3];
  wire [15:0] sop_c  = sop[16*cur +: 16];
  wire [15:0] ldh_c  = ldh[16*cur +: 16];
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
  wire [15:0] ir    = fl_c ? 16'h7000 : dir;          // bubble decodes as NOP
  wire        exec  = !st_c && !fl_c;                 // an instruction issues
  wire [9:0]  pc_c  = pcs[10*cur +: 10];
  wire [9:0]  apc1  = multi ? pc_c + 10'd1 : pc0;     // issuing address + 1

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
  // Decode stage: the registers the word in imem_rd names, from the thread it was
  // fetched for (T0 in single mode, else the next owner, fth), read this cycle.
  wire [4:0] d_op  = imem_rd[15:11];
  wire [1:0] d_thr = multi ? fth : 2'd0;
  reg  [2:0] ra_i, rb_i;
  always @(*) begin
    ra_i = imem_rd[10:8];             // I format / EXT ra
    rb_i = imem_rd[7:5];              // EXT rb
    if (d_op >= OP_ADDS && d_op <= OP_MOV) begin
      ra_i = imem_rd[7:5];            // R format rs
      rb_i = imem_rd[4:2];            // R format rt
    end else if (d_op == OP_RET) begin
      ra_i = 3'd7;
    end
  end

  wire        wb_en;
  wire [2:0]  wb_a;
  wire [15:0] wb_d;
  // TSTART / TSTOP name a thread in the ra field (0 = T1, as in ISA v2.0)
  wire [1:0]  tt        = (f_a[1:0] == 2'd0) ? 2'd1 : f_a[1:0];
  wire        do_tstart = exec && x_tstrt;
  wire [63:0] qa_w, qb_w, qc_w;

  tt_um_msf_regfile u_rf (
      .clk(clk), .rst_n(rst_n), .clr(1'b0),
      .ra(ra_i), .rb(rb_i), .rc(dbg_sel[3:1]), .we(wb_en && cur == 2'd0), .wa(wb_a), .wd(wb_d),
      .qa(qa_w[15:0]), .qb(qb_w[15:0]), .qc(qc_w[15:0]));

  tt_um_msf_regfile u_rf1 (
      .clk(clk), .rst_n(rst_n), .clr(do_tstart && tt == 2'd1),
      .ra(ra_i), .rb(rb_i), .rc(dbg_sel[3:1]), .we(wb_en && cur == 2'd1), .wa(wb_a), .wd(wb_d),
      .qa(qa_w[31:16]), .qb(qb_w[31:16]), .qc(qc_w[31:16]));

  tt_um_msf_regfile u_rf2 (
      .clk(clk), .rst_n(rst_n), .clr(do_tstart && tt == 2'd2),
      .ra(ra_i), .rb(rb_i), .rc(dbg_sel[3:1]), .we(wb_en && cur == 2'd2), .wa(wb_a), .wd(wb_d),
      .qa(qa_w[47:32]), .qb(qb_w[47:32]), .qc(qc_w[47:32]));

  tt_um_msf_regfile u_rf3 (
      .clk(clk), .rst_n(rst_n), .clr(do_tstart && tt == 2'd3),
      .ra(ra_i), .rb(rb_i), .rc(dbg_sel[3:1]), .we(wb_en && cur == 2'd3), .wa(wb_a), .wd(wb_d),
      .qa(qa_w[63:48]), .qb(qb_w[63:48]), .qc(qc_w[63:48]));

  // D -> E registers, and this cycle's write-back, for forwarding
  reg [15:0] dir, dqa, dqb;
  reg [2:0]  dra, drb;
  reg        fw_en;
  reg [1:0]  fw_thr;
  reg [2:0]  fw_a;
  reg [15:0] fw_d;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      dir <= 16'h7000; dqa <= 16'd0; dqb <= 16'd0; dra <= 3'd0; drb <= 3'd0;
      fw_en <= 1'b0; fw_thr <= 2'd0; fw_a <= 3'd0; fw_d <= 16'd0;
    end else begin
      dir <= imem_rd;
      dqa <= qa_w[16*d_thr +: 16];  dra <= ra_i;
      dqb <= qb_w[16*d_thr +: 16];  drb <= rb_i;
      fw_en <= wb_en; fw_thr <= cur; fw_a <= wb_a; fw_d <= wb_d;
    end
  end
  // E's operands: as D read them, unless E wrote that register of this thread in
  // the cycle D read it (single mode: the instruction just before)
  wire        fwd_a = fw_en && fw_thr == cur && fw_a == dra;
  wire        fwd_b = fw_en && fw_thr == cur && fw_a == drb;
  wire [15:0] qa = fwd_a ? fw_d : dqa;
  wire [15:0] qb = fwd_b ? fw_d : dqb;

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
  // WAIT_CMP whose target is 0-255 cycles at or before CNT: the deadline has
  // passed, so release in the first stall slot (Z=1, LATE) instead of waiting
  // for CNT to wrap. Latched at entry, so the subtract ends at a flop.
  wire [15:0] cmp_behind = cnt - qa;
  wire        ent_late   = (op == OP_WCMP) && (cmp_behind[15:8] == 8'd0);

  // per-thread release conditions, evaluated every cycle
  wire [3:0] cond_now;       // condition true this cycle (incl. sticky event)
  wire [3:0] evt;            // event to latch (edge / CNT match)
  wire [3:0] tmo_now;        // timeout reached
  wire [3:0] pin_t;
  genvar t;
  generate
    for (t = 0; t < 4; t = t + 1) begin : g_cond
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

  wire rel    = st_c && (cond_now[cur] || tmo_now[cur] || wlate_c);  // release this cycle
  wire rel_ok = cond_now[cur];                             // released by its condition
  wire [15:0] ld_data = ldq[cur] ? b_rd : ldh_c;

  // ------------------------------------------------------------------
  // Register write-back (always for the thread that owns the cycle)
  // ------------------------------------------------------------------
  wire [15:0] stat = {1'b0, late[cur], cur[1], run[3], run[2], cap_stat[13], cap_stat[15], ffull,
                      !fempty, cur[0], run[1], crc_busy, rx_overrun, rx_avail, tx_busy,
                      tx_full};

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
          X_TID:    wbx_d = {14'd0, cur};
          X_PR:     begin                       // port reads (#0 = pop: LD path)
            wbx_en = !x_get;
            case (f_b)
              P_FCFG:  wbx_d = {fdrop, fcount};
              P_LATE:  wbx_d = {12'd0, late};
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

  wire       do_tstop = exec && x_tstop && run[tt];
  wire [3:0] tbit     = 4'd1 << tt;
  wire [3:0] run_n    = do_tstart ? (run | tbit) : do_tstop ? (run & ~tbit) : run;
  // multi mode: the word fetched now still belongs to the next cycle's owner
  wire       f_ok     = run_n[fth] && !(do_tstart && tt == fth);

  reg [39:0] pcs_n;
  reg        bub_n;
  reg [1:0]  own_n;
  always @(*) begin
    pcs_n = pcs; bub_n = bub; own_n = own;
    if (!multi) begin
      // ---- single-thread mode: T0 owns every cycle ----
      if (st[0]) begin
        if (rel) pcs_n[9:0] = pc0 + 10'd1;       // release: mem[W+1] issues next
      end else if (redirect) begin
        pcs_n[9:0] = tgt; bub_n = 1'b1;
      end else if (enter || do_tstart) begin
        bub_n = 1'b0;                            // hold pc0 = W+1
      end else begin
        pcs_n[9:0] = pc0 + 10'd1; bub_n = 1'b0;
      end
      if (do_tstart) pcs_n[10*tt +: 10] = qb[9:0];  // T0 issues next cycle, the new thread after
    end else begin
      // ---- multi-thread mode: the owner advances its own PC ----
      if (!st[cur] && !bub) begin
        if (redirect) pcs_n[10*cur +: 10] = tgt;
        else          pcs_n[10*cur +: 10] = pc_c + 10'd1;
      end
      if (do_tstart) pcs_n[10*tt +: 10] = qb[9:0];  // (also a thread restarting itself)
      if (f_ok) begin
        own_n = fth; bub_n = 1'b0;
        // back to single mode with T0's word in flight: fetch-ahead again
        if (run_n[3:1] == 3'd0 && !st[0]) pcs_n[9:0] = pc0 + 10'd1;
      end else begin
        own_n = next_after(cur, run_n); bub_n = 1'b1;
      end
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pcs <= 40'd0; bub <= 1'b1; run <= 4'b0001; own <= 2'd0;
    end else begin
      run <= run_n; pcs <= pcs_n; bub <= bub_n; own <= own_n;
    end
  end

  // v2.2 fetched with the pointers' registers; v3 fetches the same word a cycle
  // earlier, with the values those registers are about to take
  wire       multi_n = |run_n[3:1];
  wire [1:0] fth_n   = next_after(own_n, run_n);
  // In reset these are word 1's (T0, PC 1): the boot ROM's read register resets
  // to word 0, which the decode stage takes in the first cycle after reset, as
  // v2.2 decodes it. No path from rst_n to the address: a reset released on a
  // clock edge cannot fetch word 0 twice.
  assign imem_raddr = multi_n ? pcs_n[10*fth_n +: 10] : pcs_n[9:0];

  // ------------------------------------------------------------------
  // Flags and per-thread blocking state
  // ------------------------------------------------------------------
  integer i;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      zf <= 4'b0; nf <= 4'b0;
      st <= 4'b0; sk <= 12'd0; srd <= 12'd0; sop <= 64'd0; wsel <= 16'd0;
      wcond <= 12'd0; wprev <= 4'b0; whit <= 4'b0; sto <= 64'd0; sto_en <= 4'b0;
      ldh <= 64'd0; ldq <= 4'b0; fge <= 4'b0; wlate <= 4'b0; late <= 4'b0;
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

      for (i = 0; i < 4; i = i + 1) begin
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
          wlate[i]            <= ent_late;
        end
      end

      // LATE: set by a late WAIT_CMP's release, cleared by PW #7 (one bit per thread)
      if (exec && x_pw && f_a == P_LATE) late <= late & ~qb[3:0];
      if (rel && wlate_c) late[cur] <= 1'b1;

      if (do_tstart) begin               // a fresh thread (wins over the above)
        zf[tt] <= 1'b0; nf[tt] <= 1'b0; st[tt] <= 1'b0; whit[tt] <= 1'b0; ldq[tt] <= 1'b0;
        late[tt] <= 1'b0;
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
      waddr <= 10'd128; tmo <= {4{16'hFFFF}};
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
        if (x_tstrt) tmo[16*tt +: 16] <= tmo_c; // a thread starts with its starter's TMO
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
  wire [1:0] dbg_thr   = cur;
  wire [9:0] dbg_pc    = apc1 - 10'd1;
  wire       dbg_rel   = rel;

  // ------------------------------------------------------------------
  // Debug view: one byte of state chosen by dbg_sel (isa.md §8). The last
  // issued instruction is held so the view can be read between clock steps.
  // ------------------------------------------------------------------
  reg [9:0]  lpc;
  reg [15:0] lir;
  reg [1:0]  lthr;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      lpc <= 10'd0; lir <= 16'h0; lthr <= 2'd0;
    end else if (exec) begin
      lpc <= dbg_pc; lir <= ir; lthr <= cur;
    end
  end

  // dbg_sel[6] picks a page: 0 = T0/T1 registers and the core, 1 = T2/T3
  wire [15:0] dbg_reg = qc_w[16*{dbg_sel[6], dbg_sel[4]} +: 16];
  always @(*) begin
    if (!dbg_sel[5]) dbg_q = dbg_sel[0] ? dbg_reg[15:8] : dbg_reg[7:0];
    else if (dbg_sel[6]) case (dbg_sel[4:0])
      5'h00: dbg_q = {run, st};
      5'h01: dbg_q = {nf[3], zf[3], nf[2], zf[2], nf[1], zf[1], nf[0], zf[0]};
      5'h02: dbg_q = pcs[7:0];
      5'h03: dbg_q = pcs[17:10];
      5'h04: dbg_q = pcs[27:20];
      5'h05: dbg_q = pcs[37:30];
      5'h06: dbg_q = {pcs[39:38], pcs[29:28], pcs[19:18], pcs[9:8]};
      default: dbg_q = 8'h00;
    endcase
    else case (dbg_sel[4:0])
      5'h00: dbg_q = lpc[7:0];
      5'h01: dbg_q = {1'b0, lthr[1], st[1], st[0], multi, lthr[0], lpc[9:8]};
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

  // ---- ghost: where did the word issuing now come from? (v3: fetched two
  //      cycles back, for the thread that was then next in line) ----
  reg [9:0] f_faddr1, f_faddr;
  reg [1:0] f_fthr1, f_fthr;
  always @(posedge clk) begin
    f_faddr1 <= rst_n ? imem_raddr : 10'd0;     // in reset the ROM register holds word 0
    f_fthr1  <= multi_n ? fth_n : 2'd0;
    f_faddr  <= f_faddr1;
    f_fthr   <= f_fthr1;
  end

  // ---- ghost: a shadow of the four register files, kept from the writes alone ----
  reg [15:0] f_rf [0:31];
  integer fj;
  always @(posedge clk) begin
    if (f_init) begin
      for (fj = 0; fj < 32; fj = fj + 1) f_rf[fj] <= 16'd0;
    end else begin
      if (wb_en) f_rf[{cur, wb_a}] <= wb_d;
      if (do_tstart) for (fj = 0; fj < 8; fj = fj + 1) f_rf[{tt, fj[2:0]}] <= 16'd0;
    end
  end
  reg [2:0] f_ra, f_rb;                 // the registers the issuing word names
  always @(*) begin
    f_ra = f_a; f_rb = f_b;
    if (op >= OP_ADDS && op <= OP_MOV) begin f_ra = f_b; f_rb = f_c; end
    else if (op == OP_RET) f_ra = 3'd7;
  end

  // ---- ghost: per-thread expected next issue address ----
  reg [39:0] f_next;
  reg [3:0]  f_has;
  wire [9:0] f_succ = redirect ? tgt : dbg_pc + 10'd1;
  always @(posedge clk) begin
    if (f_init) begin
      f_has <= 4'b0;
    end else begin
      if (exec)      begin f_next[10*cur +: 10] <= f_succ; f_has[cur] <= 1'b1; end
      if (do_tstart) begin f_next[10*tt +: 10] <= qb[9:0]; f_has[tt] <= 1'b1; end
    end
  end

  // ---- ghost: per-thread blocking-op witnesses (independent re-statement
  //      of isa.md section 5, not shared with the design's own logic) ----
  wire [3:0]  f_seen_w, f_evt_w, f_single_w, f_late_w;
  wire [67:0] f_cnt_w;       // 17 bits per thread
  wire [63:0] f_tmo_w;
  generate
    for (t = 0; t < 4; t = t + 1) begin : g_fv
      reg [15:0] tgt, tm;
      reg [16:0] n;
      reg        seen, prev, single;   // single: issued in single-thread mode
      reg        glate;                // WAIT_CMP target at or up to 255 before CNT
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
          kind <= kind_i; tgt <= qa; tm <= tmo_c; n <= 17'd0; seen <= 1'b0; single <= !multi;
          glate <= (kind_i == K_CMP) &&
                   ((cnt >= qa) ? ({1'b0, cnt} - {1'b0, qa} <= 17'd255)
                                : ({1'b0, cnt} + 17'h10000 - {1'b0, qa} <= 17'd255));
          sel <= imm8[3:0]; cond <= imm8[6:4]; prev <= ent_pin;
        end else if (st[t]) begin
          n <= n + 17'd1;
          prev <= p;
          if (ev) seen <= 1'b1;
        end
      end
      assign f_seen_w[t]        = seen;
      assign f_single_w[t]      = single;
      assign f_late_w[t]        = glate;
      assign f_evt_w[t]         = ev;
      assign f_cnt_w[17*t +: 17] = n;
      assign f_tmo_w[16*t +: 16] = tm;
    end
  endgenerate
  wire [16:0] f_cnt_c = f_cnt_w[17*cur +: 17];
  wire [15:0] f_tmo_c = f_tmo_w[16*cur +: 16];
  wire        f_late_c = f_late_w[cur];

  always @(*) begin
    if (!f_init) begin
      // F1 fetch/issue alignment: the issuing word was fetched last cycle
      //    from the issuing thread's address (catches B1-type pc bugs)
      if (exec) assert(f_faddr == dbg_pc && f_fthr == cur);
      // F14 (v3) the decode stage and forwarding give the issuing instruction its
      //     registers as they are now: the operands equal the shadow register file
      if (exec) assert(qa == f_rf[{cur, f_ra}] && qb == f_rf[{cur, f_rb}]);
      // F2 no skipped / repeated instruction per thread
      if (exec && f_has[cur]) assert(dbg_pc == f_next[10*cur +: 10]);
      // F3 WAIT release causes (catches B2): a WAIT completes by its own
      //    event, or by timeout after exactly TMO+1 stalled cycles
      if (rel && (sk_c == K_EDGE || sk_c == K_CMP) && rel_ok)
        assert(f_seen_w[cur] || f_evt_w[cur]);
      // F10 a WAIT_CMP whose target had passed at issue releases in the
      //     thread's first own stall slot with Z=1 (never after waiting)
      if (rel && f_late_c) begin
        assert(!rel_ok && sk_c == K_CMP);
        if (f_single_w[cur]) assert(f_cnt_c == 17'd0);
        else assert(f_cnt_c <= 17'd3);
      end
      // ... and no running thread stays stalled on a late target (no wait for
      // the wrap). A thread stopped by TSTOP gets no slots; only TSTART runs
      // it again, and that clears the stall.
      if (st[0] && run[0] && f_late_w[0]) assert(f_cnt_w[16:0]  <= 17'd3);
      if (st[1] && run[1] && f_late_w[1]) assert(f_cnt_w[33:17] <= 17'd3);
      if (st[2] && run[2] && f_late_w[2]) assert(f_cnt_w[50:34] <= 17'd3);
      if (st[3] && run[3] && f_late_w[3]) assert(f_cnt_w[67:51] <= 17'd3);
      if (rel && !rel_ok && !f_late_c) begin
        assert(f_tmo_c != 16'd0);
        // f_cnt = stalled cycles before this one; release in stall cycle TMO+1
        // exactly when the whole stall is in single-thread mode (a single-mode
        // stall cannot see multi mode: only a running thread can TSTART), and
        // in the thread's first own slot after expiry otherwise (a running
        // thread owns at least one cycle in any four)
        assert(f_cnt_c >= {1'b0, f_tmo_c});
        if (f_single_w[cur]) assert(f_cnt_c == {1'b0, f_tmo_c});
        else assert(f_cnt_c <= {1'b0, f_tmo_c} + 17'd3);
      end
      // F4 single mode runs only T0, and only running threads own cycles
      if (!multi) assert(cur == 2'd0);
      assert(run[cur] && run[0]);
      // F5 side effects only from an issuing (or completing) instruction
      if (out_we || oe_we || uo_we || b_en) assert(exec);
      if (wb_en)  assert(exec || rel);
      if (per_we) assert(exec || rel);
      if (exec && rel) assert(0);
    end
  end

  // F11 a LATE flag rises only at a late WAIT_CMP's release in its thread,
  //     and falls only by PW #7 or TSTART of that thread
  generate
    for (t = 0; t < 4; t = t + 1) begin : g_f11
      always @(posedge clk)
        if (!f_init && !$past(f_init)) begin
          if (late[t] && !$past(late[t]))
            assert($past(rel) && $past(cur) == t && $past(f_late_c));
          if (!late[t] && $past(late[t]))
            assert(($past(exec && x_pw) && $past(f_a) == P_LATE && $past(qb[t])) ||
                   ($past(do_tstart) && $past(tt) == t));
        end
    end
  endgenerate

  // F12 the rotation skips no running thread: each owns at least one cycle in
  //     any four (F3's multi-thread timeout bound relies on it)
  generate
    for (t = 0; t < 4; t = t + 1) begin : g_f12
      reg [1:0] gap;               // cycles in a row t has run without owning one
      always @(posedge clk)
        if (f_init || !run[t] || cur == t) gap <= 2'd0;
        else if (gap != 2'd3)               gap <= gap + 2'd1;
      always @(*)
        if (!f_init && run[t] && cur != t) assert(gap != 2'd3);
    end
  endgenerate

  // F13 a thread's timing depends only on which threads run, never on what they do
  //     (isa.md section 7): in a cycle with no TSTART or TSTOP, the running set stays
  //     as it is, and in multi-thread mode the next owner follows from this owner and
  //     that set alone, with no bubble, whatever this cycle's instruction, stall or
  //     pin event. It says nothing about shared memory, pins or peripherals.
  always @(posedge clk)
    if (!f_init && !$past(f_init) && !$past(do_tstart) && !$past(do_tstop)) begin
      assert(run == $past(run));
      if ($past(multi)) begin
        assert(own == $past(fth));
        assert(!bub);
      end
    end

  // F6 flags change only through the owning thread's flag ops / blocking ops
  wire f_flagev = exec && flag_op || blk_now || rel || exec && x_put;
  generate
    for (t = 0; t < 4; t = t + 1) begin : g_f6
      always @(posedge clk)
        if (!f_init && !$past(f_init))
          if (zf[t] != $past(zf[t]) || nf[t] != $past(nf[t]))
            assert(($past(cur) == t && $past(f_flagev)) ||
                   ($past(do_tstart) && $past(tt) == t));
    end
  endgenerate

  always @(posedge clk) begin
    if (!f_init && !$past(f_init)) begin
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
    cover(rel && f_late_c);
    cover(rel && !rel_ok);
    cover(rel && sk_c == K_LD && cur != 2'd0);
    cover(multi && exec && cur == 2'd1);
    cover(do_tstop && cur != 2'd0);
    cover(run == 4'b1111 && exec && cur == 2'd3);
    cover(exec && x_put && ffull);
    cover(rel && sk_c == K_LD && fge_c);
  end
`endif
endmodule

`default_nettype wire
