// F16 thread non-interference (isa.md section 7), on two copies of the core:
// formal/run_formal.py, target ni. Both copies get the same inputs. T0 and one
// more thread k (any of the four; k = 0 means T0 alone) run the same program in
// both: every word fetched for them is the same. The other threads run anything
// that touches no shared state (pins, SR, MEM_W pointer, FIFO, TX/RX/CRC,
// capture and peripheral ports, TSTART/TSTOP), each copy its own program. Then
// in every cycle the slot owner is the same in both copies, and T0 and k issue
// in the same cycles, the same words from the same addresses, with the same
// operands, and the pins are the same: what the other threads run changes
// nothing T0 and k do. Excluded on purpose: LATE_RD, which reads every
// thread's LATE flag; T0 and k do not run it here. Memory is not modelled: a
// load gets the same word in both copies, as if no other thread stored to it.
// tt_um_msf_ctrl_ni is tt_um_msf_ctrl with its state on ports and its own
// assertions removed (target ctrl checks those). Besides the property, the
// state that makes it inductive is asserted equal: the shared state, all of T0's
// and k's (registers included), the decode stage when it holds their word and
// the forwarding registers when they hold their write.
// v4: a thread's selector byte (ESEL), its timed-pin slot and its TS are part of its
// state, and CNT's high half of the shared state. The other threads cannot write a pin
// (SETM and CLRM are not quiet), so they can neither change their page nor arm a slot.
`default_nettype none

module ni_miter (
    input wire        clk,
    input wire        rst_n,
    input wire [7:0]  uio_in,
    input wire [7:0]  ui_in,
    input wire [15:0] rd_a,          // the words each copy fetches
    input wire [15:0] rd_b,
    input wire [15:0] b_rd,          // data RAM: the same word for both copies
    input wire        tx_full,
    input wire        tx_busy,
    input wire        rx_avail,
    input wire        rx_overrun,
    input wire [15:0] rx_word,
    input wire        crc_busy,
    input wire [15:0] crc_lo,
    input wire [15:0] crc_hi,
    // v4: the second engine set's status and both sets' option ports and error flags,
    // the same for both copies like the first set's
    input wire        tx_full1,
    input wire        tx_busy1,
    input wire        rx_avail1,
    input wire        rx_overrun1,
    input wire [15:0] rx_word1,
    input wire        crc_busy1,
    input wire [15:0] crc_lo1,
    input wire [15:0] crc_hi1,
    input wire [15:0] opt_rd,
    input wire [15:0] opt_rd1,
    input wire        err_any,
    input wire        err_any1,
    input wire [15:0] cap_stat,
    input wire [10:0] cap_tpos,
    input wire [6:0]  dbg_sel
);
  (* anyconst *) reg [1:0] k;

  // the pin registers (tt_um_msf_pio), one set per copy
  reg [7:0] out_a = 8'd0, oe_a = 8'd0, uo_a = 8'd0;
  reg [7:0] out_b = 8'd0, oe_b = 8'd0, uo_b = 8'd0;
  wire       out_we_a, oe_we_a, uo_we_a, out_we_b, oe_we_b, uo_we_b;
  wire [7:0] out_d_a, oe_d_a, uo_d_a, out_d_b, oe_d_b, uo_d_b;
  always @(posedge clk) begin
    if (!rst_n) begin
      out_a <= 8'd0; oe_a <= 8'd0; uo_a <= 8'd0;
      out_b <= 8'd0; oe_b <= 8'd0; uo_b <= 8'd0;
    end else begin
      if (out_we_a) out_a <= out_d_a;
      if (oe_we_a)  oe_a  <= oe_d_a;
      if (uo_we_a)  uo_a  <= uo_d_a;
      if (out_we_b) out_b <= out_d_b;
      if (oe_we_b)  oe_b  <= oe_d_b;
      if (uo_we_b)  uo_b  <= uo_d_b;
    end
  end

  wire [1:0]  fth_a, fth_b, cur_a, cur_b;
  wire        exec_a, exec_b;
  wire [10:0] pc_a, pc_b;
  wire [15:0] ir_a, ir_b, qa_a, qa_b, qb_a, qb_b;
  wire [3:0]  run_a, run_b;
  wire [276:0] t0_a, t1_a, t2_a, t3_a, t0_b, t1_b, t2_b, t3_b;
  wire [123:0] sh_a, sh_b;
  wire        bub_a, bub_b;
  wire [53:0] dst_a, dst_b;
  wire [1:0]  fwthr_a, fwthr_b;
  wire [19:0] fw_a, fw_b;

  tt_um_msf_ctrl_ni a (
      .clk(clk), .rst_n(rst_n), .uio_in(uio_in), .ui_in(ui_in),
      .imem_raddr(), .imem_rd(rd_a),
      .b_en(), .b_we(), .b_addr(), .b_wd(), .b_rd(b_rd),
      .out_q(out_a), .oe_q(oe_a), .uo_q(uo_a),
      .out_we(out_we_a), .out_d(out_d_a), .oe_we(oe_we_a), .oe_d(oe_d_a),
      .uo_we(uo_we_a), .uo_d(uo_d_a),
      .per_we(), .per_sub(), .per_wd(), .per_set(), .rx_pop(),
      .opt_we(), .opt_port(), .opt_wd(), .opt_rport(),
      .opt_rd(opt_rd), .opt_rd1(opt_rd1), .err_any(err_any), .err_any1(err_any1),
      .tx_full(tx_full), .tx_busy(tx_busy), .rx_avail(rx_avail), .rx_overrun(rx_overrun),
      .rx_word(rx_word), .crc_busy(crc_busy), .crc_lo(crc_lo), .crc_hi(crc_hi),
      .tx_full1(tx_full1), .tx_busy1(tx_busy1), .rx_avail1(rx_avail1), .rx_overrun1(rx_overrun1),
      .rx_word1(rx_word1), .crc_busy1(crc_busy1), .crc_lo1(crc_lo1), .crc_hi1(crc_hi1),
      .pw_we(), .pw_port(), .pw_data(), .oe_off(), .oe_off_n(), .cap_stat(cap_stat), .cap_tpos(cap_tpos),
      .dbg_sel(dbg_sel), .dbg_q(),
      .ni_dthr(fth_a), .ni_cur(cur_a), .ni_exec(exec_a), .ni_pc(pc_a), .ni_ir(ir_a),
      .ni_qa(qa_a), .ni_qb(qb_a), .ni_run(run_a),
      .ni_t0(t0_a), .ni_t1(t1_a), .ni_t2(t2_a), .ni_t3(t3_a), .ni_sh(sh_a), .ni_bub(bub_a),
      .ni_dst(dst_a), .ni_fwthr(fwthr_a), .ni_fw(fw_a));

  tt_um_msf_ctrl_ni b (
      .clk(clk), .rst_n(rst_n), .uio_in(uio_in), .ui_in(ui_in),
      .imem_raddr(), .imem_rd(rd_b),
      .b_en(), .b_we(), .b_addr(), .b_wd(), .b_rd(b_rd),
      .out_q(out_b), .oe_q(oe_b), .uo_q(uo_b),
      .out_we(out_we_b), .out_d(out_d_b), .oe_we(oe_we_b), .oe_d(oe_d_b),
      .uo_we(uo_we_b), .uo_d(uo_d_b),
      .per_we(), .per_sub(), .per_wd(), .per_set(), .rx_pop(),
      .opt_we(), .opt_port(), .opt_wd(), .opt_rport(),
      .opt_rd(opt_rd), .opt_rd1(opt_rd1), .err_any(err_any), .err_any1(err_any1),
      .tx_full(tx_full), .tx_busy(tx_busy), .rx_avail(rx_avail), .rx_overrun(rx_overrun),
      .rx_word(rx_word), .crc_busy(crc_busy), .crc_lo(crc_lo), .crc_hi(crc_hi),
      .tx_full1(tx_full1), .tx_busy1(tx_busy1), .rx_avail1(rx_avail1), .rx_overrun1(rx_overrun1),
      .rx_word1(rx_word1), .crc_busy1(crc_busy1), .crc_lo1(crc_lo1), .crc_hi1(crc_hi1),
      .pw_we(), .pw_port(), .pw_data(), .oe_off(), .oe_off_n(), .cap_stat(cap_stat), .cap_tpos(cap_tpos),
      .dbg_sel(dbg_sel), .dbg_q(),
      .ni_dthr(fth_b), .ni_cur(cur_b), .ni_exec(exec_b), .ni_pc(pc_b), .ni_ir(ir_b),
      .ni_qa(qa_b), .ni_qb(qb_b), .ni_run(run_b),
      .ni_t0(t0_b), .ni_t1(t1_b), .ni_t2(t2_b), .ni_t3(t3_b), .ni_sh(sh_b), .ni_bub(bub_b),
      .ni_dst(dst_b), .ni_fwthr(fwthr_b), .ni_fw(fw_b));

  // a word another thread may run: nothing that touches shared state
  function quiet(input [15:0] w);
    reg [4:0] op, sub;
    reg [2:0] p;
    begin
      op = w[15:11]; sub = w[4:0]; p = w[7:5];
      quiet = 1'b1;
      if (op == 5'h10 || op == 5'h12 || op == 5'h13)       // PIN_W, SETM, CLRM
        quiet = 1'b0;
      if (op == 5'h1C || op == 5'h1D || op == 5'h1E)       // SHIFT_OUT, SHIFT_IN, MEM_W
        quiet = 1'b0;
      if (op == 5'h0F) begin                                // EXT
        if (sub == 5'd0 || sub == 5'd2 || sub == 5'd3 || sub == 5'd4 || sub == 5'd5 ||
            sub == 5'd7)                                    // SR_LD XFER SER_CFG OUT_W MEM_A ST
          quiet = 1'b0;
        if (sub >= 5'd12 && sub <= 5'd28)                   // TX, RX, CRC, TSTART, TSTOP
          quiet = 1'b0;
        if (sub == 5'd30 || (sub == 5'd31 && p == 3'd0))    // PW, FIFO_GET
          quiet = 1'b0;
      end
    end
  endfunction

  function late_rd(input [15:0] w);
    late_rd = w[15:11] == 5'h0F && w[4:0] == 5'd31 && w[7:5] == 3'd7;
  endfunction

  wire same_a = fth_a == 2'd0 || fth_a == k;
  wire same_b = fth_b == 2'd0 || fth_b == k;
  always @(*) begin
    if (same_a || same_b) assume(rd_a == rd_b);
    if (same_a) assume(!late_rd(rd_a));
    if (!same_a) assume(quiet(rd_a));
    if (!same_b) assume(quiet(rd_b));
  end

  reg f_init = 1'b1;
  always @(posedge clk) f_init <= 1'b0;
  always @(*) assume(rst_n == !f_init);

  wire in_e = cur_a == 2'd0 || cur_a == k;
  wire [276:0] tk_a = k == 2'd1 ? t1_a : k == 2'd2 ? t2_a : k == 2'd3 ? t3_a : t0_a;
  wire [276:0] tk_b = k == 2'd1 ? t1_b : k == 2'd2 ? t2_b : k == 2'd3 ? t3_b : t0_b;
  always @(*)
    if (!f_init) begin
      // the property
      assert(cur_a == cur_b && run_a == run_b);         // the same slots
      if (in_e) begin
        assert(exec_a == exec_b);
        if (exec_a) assert(pc_a == pc_b && ir_a == ir_b && qa_a == qa_b && qb_a == qb_b);
      end
      assert(out_a == out_b && oe_a == oe_b && uo_a == uo_b);
      // what makes it inductive
      assert(sh_a == sh_b && bub_a == bub_b && fth_a == fth_b);
      assert(t0_a == t0_b && tk_a == tk_b);
      if (in_e && !bub_a) assert(dst_a == dst_b);
      assert(fwthr_a == fwthr_b);
      if (fwthr_a == 2'd0 || fwthr_a == k)             // (its value is only used when enabled)
        assert(fw_a[19] == fw_b[19] && (!fw_a[19] || fw_a[18:0] == fw_b[18:0]));
    end
endmodule
