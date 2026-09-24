// SPDX-License-Identifier: Apache-2.0
// msfcore control: fetch/decode/execute, stall logic, shift datapath, cycle counter.
// STATUS: fresh rewrite (2-stage fetch pipeline for the registered-read IMEM);
// never compiled -- make -B has NOT been green since the reorg.
// KNOWN BUGS: (a) flags hold on WAIT resume (prev_stall) unproven;
// (b) WAIT_CMP compares cnt vs live r[f_rs] via the ra mux;
// (c) CALL saves next_pc pre-fetch; (d) serial_out (uio bit7) only
// asserts during the same cycle a SHIFT_OUT executes; (e) MEM_W data mux
// feeds sram_wd = rf_wdata_w (ra_data) -- correct per spec, unverified.
// INTEGRATION: instantiates regfile + alu; wrapped by tt_um_msf.v (pins).
// NEXT: get a green compile, blink smoke test (test_core.py), cycle sim.
//
// Fetch pipeline (IMEM has a REGISTERED read — 1 cycle latency):
//   pc_p  = fetch address (drives imem_raddr)
//   ir    = instruction latched last cycle (valid for PC = pc - 1)
// Each non-stall cycle: pc_p <= next(pc, ir), ir <= imem_rd.
//
// Opcode map (isa.md v1.0, 4-bit op = ir[15:12]):
//   0x0 LDR        0x1 ADDS       0x2 SUBS       0x3 AND
//   0x4 OR         0x5 XOR        0x6 SHL        0x7 SHR
//   0x8 MOV        0x9 JMP        0xA JZ         0xB JNZ
//   0xC JP         0xD JN         0xE NOP        0xF reserved
//   0x10 PIN_W     0x11 PIN_R     0x12 OE_SET    0x13 OE_CLR
//   0x14 CNT_R     0x15 INCR      0x16 DECR      0x17 IN_RD
//   0x18 CALL      0x19 RET       0x1A WAIT_EDGE  0x1B WAIT_CMP
//   0x1C SHIFT_OUT 0x1D SHIFT_IN  0x1E MEM_W     0x1F reserved
`default_nettype none

module tt_um_msf_ctrl (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,
    input  wire [7:0]  ui_in,
    input  wire [15:0] imem_rd,   // registered IMEM read data (valid for imem_raddr)
    output wire [8:0]  imem_raddr,
    output wire        sram_we,
    output wire [8:0]  sram_waddr,
    output wire [15:0] sram_wd,
    output wire [7:0]  pin_w,
    output wire        pin_w_en,
    output wire [7:0]  oe_set,
    output wire [7:0]  oe_clr,
    output wire        oe_set_en,
    output wire        oe_clr_en,
    output wire [7:0]  uo_w,
    output wire        uo_w_en,
    output wire        serial_out,
    output wire        rf_we,
    output wire [2:0]  rf_waddr,
    output wire [15:0] rf_wdata,
    output wire [2:0]  rf_ra,
    output wire [2:0]  rf_rb
);
  // ---- state ----
  reg [8:0]  pc_p;   // fetch address (drives imem_raddr)
  reg [15:0] ir;     // instruction registered (valid for PC = pc_p - 1)
  reg [15:0] cnt;    // free-running 16-bit cycle counter
  reg [15:0] osr;    // shift register (serial datapath)
  reg [8:0]  waddr;  // IMEM write pointer (auto-increment, 128..511 wrap)
  reg [15:0] timeout;
  reg        stall;
  reg [7:0]  pin_prev;
  reg        z, n;

  // ---- instruction fields ----
  wire [3:0] op      = ir[15:12];
  wire [2:0] f_rd    = ir[10:8];
  wire [2:0] f_rs    = ir[7:5];
  wire [2:0] f_rt    = ir[4:2];
  wire [7:0] imm8    = ir[7:0];
  wire [15:0] imm8e  = {8'h0, imm8};
  wire [10:0] imm11  = ir[10:0];
  wire [8:0]  sel_pin = imm8[2:0];

  // ---- register file (8 x 16, 2 read ports / 1 write) ----
  // R-format arith (ADDS..MOV): source operands rs,rt -> ports a,b.
  // I-format reg-operand (PIN_W/CNT_R/INCR/DECR/MEM_W/WAIT_CMP/SHIFT_OUT):
  //   the operand register field (b10..b8) -> port a only.
  reg [2:0] ra_mux, rb_mux;
  always @(*) begin
    case (op)
      4'h1, 4'h2, 4'h3, 4'h4, 4'h5, 4'h6, 4'h7, 4'h8:
        begin ra_mux = f_rs; rb_mux = f_rt; end
      default:
        begin ra_mux = f_rd; rb_mux = 3'b000; end
    endcase
  end
  wire [15:0] ra_data, rb_data;
  tt_um_msf_regfile u_rf (
      .clk(clk), .rst_n(rst_n),
      .ra(ra_mux), .rb(rb_mux),
      .we(rf_we), .wa(rf_waddr), .wd(rf_wdata),
      .qa(ra_data), .qb(rb_data)
  );

  // ---- ALU ----
  wire [3:0] alu_op;
  wire [15:0] alu_a, alu_b, alu_y;
  wire alu_z, alu_n;

  always @(*) begin
    case (op)
      4'h1:  begin alu_op = 4'd0; alu_a = ra_data; alu_b = rb_data; end // ADDS
      4'h2:  begin alu_op = 4'd1; alu_a = ra_data; alu_b = rb_data; end // SUBS
      4'h3:  begin alu_op = 4'd2; alu_a = ra_data; alu_b = rb_data; end // AND
      4'h4:  begin alu_op = 4'd3; alu_a = ra_data; alu_b = rb_data; end // OR
      4'h5:  begin alu_op = 4'd4; alu_a = ra_data; alu_b = rb_data; end // XOR
      4'h6:  begin alu_op = 4'd5; alu_a = ra_data; alu_b = {13'h0, f_rt}; end // SHL
      4'h7:  begin alu_op = 4'd6; alu_a = ra_data; alu_b = {13'h0, f_rt}; end // SHR
      4'h8:  begin alu_op = 4'd9; alu_a = ra_data; alu_b = 16'h0; end        // MOV
      4'h15: begin alu_op = 4'd7; alu_a = ra_data; alu_b = 16'h0; end       // INCR
      4'h16: begin alu_op = 4'd8; alu_a = ra_data; alu_b = 16'h0; end       // DECR
      default: begin alu_op = 4'd9; alu_a = ra_data; alu_b = 16'h0; end
    endcase
  end

  tt_um_msf_alu u_alu (
      .a(alu_a), .b(alu_b), .op(alu_op),
      .y(alu_y), .z(alu_z), .n(alu_n)
  );

  // ---- register file write mux (combinational; gated by rf_we) ----
  reg rf_we_w;
  reg [15:0] rf_wdata_w;
  always @(*) begin
    case (op)
      4'h0:  begin rf_we_w = 1'b1; rf_wdata_w = imm8e; end           // LDR
      4'h1, 4'h2, 4'h3, 4'h4, 4'h5, 4'h6, 4'h7, 4'h8, 4'h15, 4'h16:
             begin rf_we_w = 1'b1; rf_wdata_w = alu_y; end
      4'h11: begin rf_we_w = 1'b1; rf_wdata_w = {8'h0, uio_in}; end // PIN_R
      4'h14: begin rf_we_w = 1'b1; rf_wdata_w = cnt; end           // CNT_R
      4'h17: begin rf_we_w = 1'b1; rf_wdata_w = {8'h0, ui_in}; end  // IN_RD
      4'h18: begin rf_we_w = 1'b1; rf_wdata_w = next_pc; end       // CALL (return = next PC)
      4'h1E: begin rf_we_w = 1'b0; rf_wdata_w = ra_data; end       // MEM_W data = r[rd]
      default: begin rf_we_w = 1'b0; rf_wdata_w = 16'h0; end
    endcase
  end
  assign rf_we     = rf_we_w & ~stall;
  assign rf_wdata  = rf_wdata_w;
  assign rf_waddr  = (op == 4'h18) ? 3'b111 : f_rd;  // CALL -> R7
  assign rf_ra     = ra_mux;
  assign rf_rb     = rb_mux;

  // ---- next fetch address (for the instruction in ir) ----
  wire [8:0] jmp_tgt  = imm11[8:0];
  wire [8:0] rel9     = ir[10:2];
  wire [8:0] br_tgt   = pc_p + 9'h1 + (rel9[8] ? (9'h100 - rel9) : rel9);
  wire       br_en    = (op == 4'hA && z) || (op == 4'hB && !z) ||
                         (op == 4'hC && !n) || (op == 4'hD && n);
  wire [8:0]  next_pc;
  always @(*) begin
    case (op)
      4'h9:    next_pc = jmp_tgt;  // JMP imm11
      4'hA, 4'hB, 4'hC, 4'hD: next_pc = br_en ? br_tgt : (pc_p + 9'h1);
      4'h19:   next_pc = ra_data[8:0];  // RET (via R7)
      default: next_pc = pc_p + 9'h1;
    endcase
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pc_p <= 9'h0;
      ir   <= 16'h0;
    end else if (!stall) begin
      pc_p <= next_pc;
      ir   <= imem_rd;
    end
  end
  assign imem_raddr = pc_p;

  // ---- flags ----
  // Updated from the ALU result on normal cycles. While stalled on a WAIT*
  // the flags are held (the WAIT instruction itself reads Z for JZ), and a
  // HW timeout sets Z on the resume cycle.
  reg prev_stall;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      z          <= 1'b0;
      n          <= 1'b0;
      prev_stall <= 1'b0;
    end else begin
      prev_stall <= stall;
      if (stall && timeout == 16'h0) begin
        z <= 1'b1;            // timeout -> Z (hang-safe)
      end else if (stall || prev_stall) begin
        ;                     // hold during stall / the resume cycle
      end else begin
        z <= alu_z;
        n <= alu_n;
      end
    end
  end

  // ---- cycle counter (free-running; resets with rst_n only) ----
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) cnt <= 16'h0;
    else        cnt <= cnt + 16'h1;
  end

  // ---- shift datapath (OSR, MSB-first; serial pin = uio bit 7) ----
  // SHIFT_OUT: the OLD OSR MSB is presented on the serial pin for one cycle.
  reg ser_out;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      osr     <= 16'h0;
      ser_out <= 1'b0;
    end else if (!stall) begin
      if (op == 4'h1D) begin
        osr <= {osr[14:0], uio_in[7]};  // SHIFT_IN
      end else if (op == 4'h1C) begin
        ser_out <= osr[15];             // emit old MSB
        osr     <= {osr[14:0], ra_data[0]}; // SHIFT_OUT
      end
    end
  end
  assign serial_out = ser_out;

  // ---- stall logic (WAIT_EDGE / WAIT_CMP + 16-bit HW timeout) ----
  wire edge_event = (uio_in[sel_pin] != pin_prev[sel_pin]);
  wire cmp_match  = (cnt == ra_data);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      stall   <= 1'b0;
      timeout <= 16'h0;
    end else if (stall) begin
      timeout <= timeout - 16'h1;
      if (op == 4'h1A && edge_event) stall <= 1'b0;
      else if (op == 4'h1B && cmp_match) stall <= 1'b0;
      else if (timeout == 16'h0) begin
        stall <= 1'b0;
        // timeout -> resume (flag latches below on the next cycle)
      end
    end else begin
      if (op == 4'h1A || op == 4'h1B) begin
        stall   <= 1'b1;
        timeout <= 16'hFFFF;
      end else begin
        stall   <= 1'b0;
        timeout <= 16'h0;
      end
    end
  end

  // pin_prev always captures the bus (edge detect for WAIT_EDGE)
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) pin_prev <= 8'h00;
    else        pin_prev <= uio_in;
  end

  // ---- pin I/O control ----
  assign pin_w     = ra_data[7:0];
  assign pin_w_en  = (op == 4'h10) & ~stall;
  assign oe_clr    = imm8;
  assign oe_clr_en = (op == 4'h13) & ~stall;
  assign oe_set    = imm8;
  assign oe_set_en = (op == 4'h12) & ~stall;
  assign uo_w      = 8'h00;
  assign uo_w_en   = 1'b0;  // status byte reserved for M3 firmware use

  // ---- IMEM write (MEM_W: waddr auto-increments 128..511, wraps) ----
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) waddr <= 9'h80;
    else if ((op == 4'h1E) & ~stall)
      waddr <= (waddr == 9'h1FF) ? 9'h80 : (waddr + 9'h1);
  end
  assign sram_we    = (op == 4'h1E) & ~stall;
  assign sram_waddr = waddr;
  assign sram_wd    = rf_wdata_w;  // MEM_W rs -> r[rs] (ra port)
endmodule
