// SPDX-License-Identifier: Apache-2.0
// msfcore control: fetch/decode/execute, stall logic, shift datapath, cycle counter.
// Complete implementation of 16-bit ISA v1.0 with registered-read IMEM 2-stage pipeline.

`default_nettype none

module tt_um_msf_ctrl (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,
    input  wire [7:0]  ui_in,
    input  wire [15:0] imem_rd,

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

  // ============================================================
  // State Registers
  // ============================================================
  reg [8:0]  pc_p;
  reg        flush;
  wire [15:0] ir = flush ? 16'h7000 : imem_rd;
  reg [15:0] cnt;
  reg [15:0] osr;
  reg        ser_out;
  reg [8:0]  waddr;
  reg [15:0] timeout;
  reg        stall;
  reg        wait_pin_init;
  reg        z;
  reg        n;

  // ============================================================
  // Instruction Fields
  // ============================================================
  wire [4:0]  op      = ir[15:11];
  wire [2:0]  f_rd    = ir[10:8];
  wire [2:0]  f_rs    = ir[7:5];
  wire [2:0]  f_rt    = ir[4:2];
  wire [7:0]  imm8    = ir[7:0];
  wire [15:0] imm8e   = {8'h00, imm8};
  wire [10:0] imm11   = ir[10:0];
  wire [2:0]  sel_pin = imm8[2:0];

  // ============================================================
  // Branch & PC Redirection Logic
  // ============================================================
  wire [8:0] jmp_tgt = imm11[8:0];
  wire [8:0] rel9    = ir[10:2];
  // pc_p is PC+1 during execution; adding rel9 (9-bit signed two's complement)
  // yields exact PC+1+rel9:
  wire [8:0] br_tgt  = pc_p + rel9;

  wire br_taken = !flush && !stall && (
      (op == 5'h0A && z)  ||   // JZ
      (op == 5'h0B && !z) ||   // JNZ
      (op == 5'h0C && !n) ||   // JP
      (op == 5'h0D && n)       // JN
  );

  wire jmp_taken = !flush && !stall && (op == 5'h09);
  wire ret_taken = !flush && !stall && (op == 5'h19);
  wire pc_redirect = br_taken || jmp_taken || ret_taken;

  reg [8:0] next_pc;
  always @(*) begin
    if (jmp_taken)
      next_pc = jmp_tgt;
    else if (br_taken)
      next_pc = br_tgt;
    else if (ret_taken)
      next_pc = ra_data[8:0]; // Return address in R7
    else
      next_pc = pc_p + 9'd1;
  end

  // ============================================================
  // Instruction Fetch & Pipeline Control
  //
  // STALL-ENTRY PC-HOLD (M2.7 fix): a WAIT_* instruction at address W
  // executes in the cycle where pc_p == W+1 and ir == mem[W].  At that
  // posedge the stall FSM sets stall=1; if pc_p also advanced to W+2
  // (the default next_pc), the registered read would latch mem[W+2]
  // during the stall and mem[W+1] would be SKIPPED after release.  So
  // on the stall-entry cycle we HOLD pc_p at W+1: the read latches
  // mem[W+1], which sits in `ir` for the whole stall and executes as
  // the first instruction after release (pc_p = W+1, then W+2 next
  // cycle).  No flush needed: `ir` is combinational on imem_rd, which
  // stays stable while pc_p is frozen.
  // ============================================================
  wire stall_enter = !stall && !flush && ((op == 5'h1A) || (op == 5'h1B));

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pc_p  <= 9'd0;
      flush <= 1'b1;     // Initial bubble out of reset
    end else if (!stall) begin
      if (pc_redirect) begin
        pc_p  <= next_pc;
        flush <= 1'b1;     // NOP bubble while target instruction is fetched
      end else if (stall_enter) begin
        pc_p  <= pc_p;      // Hold at W+1 so mem[W+1] re-executes after release
        flush <= 1'b0;
      end else begin
        pc_p  <= next_pc;
        flush <= 1'b0;
      end
    end
  end

  assign imem_raddr = pc_p;

  // ============================================================
  // Register File Read Port Muxing
  // ============================================================
  reg [2:0] ra_mux;
  reg [2:0] rb_mux;

  always @(*) begin
    case (op)
      // R-format arith/logic (ADDS..MOV): rs -> ra, rt -> rb
      5'h01, 5'h02, 5'h03, 5'h04, 5'h05, 5'h06, 5'h07, 5'h08: begin
        ra_mux = f_rs;
        rb_mux = f_rt;
      end
      // RET: return address in R7
      5'h19: begin
        ra_mux = 3'b111;
        rb_mux = 3'b000;
      end
      // I-format reg-operand (PIN_W/CNT_R/INCR/DECR/IN_RD/WAIT_CMP/SHIFT_OUT/MEM_W)
      default: begin
        ra_mux = f_rd;
        rb_mux = 3'b000;
      end
    endcase
  end

  wire [15:0] ra_data;
  wire [15:0] rb_data;

  tt_um_msf_regfile u_rf (
      .clk   (clk),
      .rst_n (rst_n),
      .ra    (ra_mux),
      .rb    (rb_mux),
      .we    (rf_we),
      .wa    (rf_waddr),
      .wd    (rf_wdata),
      .qa    (ra_data),
      .qb    (rb_data)
  );

  // ============================================================
  // ALU
  // ============================================================
  reg [3:0]  alu_op;
  reg [15:0] alu_a;
  reg [15:0] alu_b;

  wire [15:0] alu_y;
  wire        alu_z;
  wire        alu_n;

  always @(*) begin
    alu_op = 4'd9;
    alu_a  = ra_data;
    alu_b  = 16'd0;

    case (op)
      5'h01: begin alu_op = 4'd0; alu_a = ra_data; alu_b = rb_data; end        // ADDS
      5'h02: begin alu_op = 4'd1; alu_a = ra_data; alu_b = rb_data; end        // SUBS
      5'h03: begin alu_op = 4'd2; alu_a = ra_data; alu_b = rb_data; end        // AND
      5'h04: begin alu_op = 4'd3; alu_a = ra_data; alu_b = rb_data; end        // OR
      5'h05: begin alu_op = 4'd4; alu_a = ra_data; alu_b = rb_data; end        // XOR
      5'h06: begin alu_op = 4'd5; alu_a = ra_data; alu_b = {13'd0, f_rt}; end  // SHL
      5'h07: begin alu_op = 4'd6; alu_a = ra_data; alu_b = {13'd0, f_rt}; end  // SHR
      5'h08: begin alu_op = 4'd9; alu_a = ra_data; alu_b = 16'd0; end          // MOV
      5'h15: begin alu_op = 4'd7; alu_a = ra_data; alu_b = 16'd0; end          // INCR
      5'h16: begin alu_op = 4'd8; alu_a = ra_data; alu_b = 16'd0; end          // DECR
      default: begin alu_op = 4'd9; alu_a = ra_data; alu_b = 16'd0; end
    endcase
  end

  tt_um_msf_alu u_alu (
      .a  (alu_a),
      .b  (alu_b),
      .op (alu_op),
      .y  (alu_y),
      .z  (alu_z),
      .n  (alu_n)
  );

  // ============================================================
  // Register File Write Mux
  // ============================================================
  reg        rf_we_w;
  reg [15:0] rf_wdata_w;

  always @(*) begin
    rf_we_w    = 1'b0;
    rf_wdata_w = 16'd0;

    case (op)
      5'h00: begin rf_we_w = 1'b1; rf_wdata_w = imm8e; end           // LDR
      5'h01, 5'h02, 5'h03, 5'h04, 5'h05, 5'h06, 5'h07, 5'h08,
      5'h15, 5'h16:
             begin rf_we_w = 1'b1; rf_wdata_w = alu_y; end           // ALU ops
      5'h11: begin rf_we_w = 1'b1; rf_wdata_w = {8'h00, uio_in}; end // PIN_R
      5'h14: begin rf_we_w = 1'b1; rf_wdata_w = cnt; end             // CNT_R
      5'h17: begin rf_we_w = 1'b1; rf_wdata_w = {8'h00, ui_in}; end  // IN_RD
      5'h18: begin rf_we_w = 1'b1; rf_wdata_w = {7'd0, pc_p + 9'd1}; end // CALL saves return addr
      default: begin rf_we_w = 1'b0; rf_wdata_w = 16'd0; end
    endcase
  end

  assign rf_we    = rf_we_w & ~stall & ~flush;
  assign rf_wdata = rf_wdata_w;
  assign rf_waddr = (op == 5'h18) ? 3'b111 : f_rd; // CALL -> R7
  assign rf_ra    = ra_mux;
  assign rf_rb    = rb_mux;

  // ============================================================
  // Flags: Z and N
  // Affected ONLY by ADDS, SUBS, INCR, DECR (and WAIT timeout)
  // ============================================================
  wire flag_we = !stall && !flush && (op == 5'h01 || op == 5'h02 || op == 5'h15 || op == 5'h16);
  wire wait_start = !stall && !flush && ((op == 5'h1A) || (op == 5'h1B));

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      z <= 1'b0;
      n <= 1'b0;
    end else if (stall && timeout == 16'd0) begin
      z <= 1'b1; // Timeout sets Z (hang-safe)
    end else if (wait_start) begin
      z <= 1'b0; // Clear Z at start of WAIT so Z=1 uniquely flags timeout
    end else if (flag_we) begin
      z <= alu_z;
      n <= alu_n;
    end
  end

  // ============================================================
  // Free-running 16-bit Cycle Counter
  // ============================================================
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n)
      cnt <= 16'd0;
    else
      cnt <= cnt + 16'd1;
  end

  // ============================================================
  // Shift Datapath (OSR, MSB-first)
  // ============================================================
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      osr     <= 16'd0;
      ser_out <= 1'b0;
    end else if (!stall && !flush) begin
      if (op == 5'h1D) begin
        osr <= {osr[14:0], uio_in[7]};      // SHIFT_IN (from bit 7)
      end else if (op == 5'h1C) begin
        ser_out <= osr[15];                 // SHIFT_OUT: emit old MSB
        osr     <= {osr[14:0], ra_data[0]};
      end else if (op == 5'h10) begin
        ser_out <= ra_data[7];              // PIN_W also updates bit 7 driver
      end
    end
  end

  assign serial_out = ser_out;

  // ============================================================
  // Event-driven Stalls (WAIT_EDGE, WAIT_CMP) + 16-bit HW Timeout
  // ============================================================
  reg wait_done;
  reg [2:0]  wait_sel;    // latched pin number at WAIT_EDGE entry
  reg [15:0] wait_tgt;    // latched compare target at WAIT_CMP entry

  // During a stall, pc_p is frozen so imem_rd feeds the *next* instruction
  // into ir; op is no longer the WAIT opcode.  The match conditions must
  // therefore use latched fields (wait_sel / wait_tgt) and be gated on
  // `stall`, not on `op`.
  wire edge_match = stall && (uio_in[wait_sel] != wait_pin_init);
  wire cmp_match  = stall && (cnt == wait_tgt);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      stall         <= 1'b0;
      wait_done     <= 1'b0;
      timeout       <= 16'd0;
      wait_pin_init <= 1'b0;
      wait_sel      <= 3'd0;
      wait_tgt      <= 16'd0;
    end else if (stall) begin
      timeout <= timeout - 16'd1;
      if (edge_match || cmp_match || (timeout == 16'd0)) begin
        stall     <= 1'b0;
        wait_done <= 1'b1;
      end
    end else if (!flush && !wait_done && ((op == 5'h1A) || (op == 5'h1B))) begin
      stall         <= 1'b1;
      timeout       <= 16'hFFFF;
      wait_pin_init <= uio_in[sel_pin];
      wait_sel      <= sel_pin;
      wait_tgt      <= ra_data;
    end else begin
      stall     <= 1'b0;
      wait_done <= 1'b0;
      timeout   <= 16'd0;
    end
  end

  // ============================================================
  // Pin I/O Control
  // ============================================================
  assign pin_w     = ra_data[7:0];
  assign pin_w_en  = (op == 5'h10) & ~stall & ~flush;
  assign oe_set    = imm8;
  assign oe_set_en = (op == 5'h12) & ~stall & ~flush;
  assign oe_clr    = imm8;
  assign oe_clr_en = (op == 5'h13) & ~stall & ~flush;
  assign uo_w      = 8'h00;
  assign uo_w_en   = 1'b0;

  // ============================================================
  // IMEM Write (MEM_W rs: auto-increment 128..511, wraps)
  // ============================================================
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      waddr <= 9'h080;
    end else if ((op == 5'h1E) && !stall && !flush) begin
      if (waddr == 9'h1FF)
        waddr <= 9'h080;
      else
        waddr <= waddr + 9'd1;
    end
  end

  assign sram_we    = (op == 5'h1E) & ~stall & ~flush;
  assign sram_waddr = waddr;
  assign sram_wd    = ra_data;

endmodule

`default_nettype wire
