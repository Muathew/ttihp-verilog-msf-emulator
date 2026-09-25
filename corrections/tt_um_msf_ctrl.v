// SPDX-License-Identifier: Apache-2.0
// msfcore control: fetch/decode/execute, stall logic, shift datapath, cycle counter.
// STATUS: corrected for 5-bit opcode ISA and Verilog compilation.
// INTEGRATION: instantiates regfile + alu; wrapped by tt_um_msf.v.
//
// Fetch pipeline (IMEM has a REGISTERED read - 1 cycle latency):
//   pc_p  = fetch address (drives imem_raddr)
//   ir    = instruction latched last cycle
//
// Opcode map, 5-bit op = ir[15:11]:
//   0x00 LDR        0x01 ADDS       0x02 SUBS       0x03 AND
//   0x04 OR         0x05 XOR        0x06 SHL        0x07 SHR
//   0x08 MOV        0x09 JMP        0x0A JZ        0x0B JNZ
//   0x0C JP         0x0D JN        0x0E NOP        0x0F reserved
//   0x10 PIN_W      0x11 PIN_R     0x12 OE_SET     0x13 OE_CLR
//   0x14 CNT_R      0x15 INCR      0x16 DECR      0x17 IN_RD
//   0x18 CALL       0x19 RET       0x1A WAIT_EDGE  0x1B WAIT_CMP
//   0x1C SHIFT_OUT  0x1D SHIFT_IN  0x1E MEM_W      0x1F reserved

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
  // State
  // ============================================================

  reg [8:0]  pc_p;
  reg [15:0] ir;
  reg [15:0] cnt;
  reg [15:0] osr;
  reg [8:0]  waddr;
  reg [15:0] timeout;
  reg        stall;
  reg [7:0]  pin_prev;
  reg        z;
  reg        n;

  // ============================================================
  // Instruction fields
  //
  // 5-bit opcode:
  //   ir[15:11]
  //
  // Remaining fields:
  //   rd = [10:8]
  //   rs = [7:5]
  //   rt = [4:2]
  // ============================================================

  wire [4:0] op   = ir[15:11];
  wire [2:0] f_rd = ir[10:8];
  wire [2:0] f_rs = ir[7:5];
  wire [2:0] f_rt = ir[4:2];

  wire [7:0]  imm8  = ir[7:0];
  wire [15:0] imm8e = {8'h00, imm8};

  wire [10:0] imm11 = ir[10:0];

  // Pin number is only 3 bits because uio_in is 8 bits.
  wire [2:0] sel_pin = imm8[2:0];

  // ============================================================
  // Next fetch address
  // ============================================================

  reg [8:0] next_pc;

  wire [8:0] jmp_tgt = imm11[8:0];

  // Relative branch offset.
  wire [8:0] rel9 = ir[10:2];

  wire [8:0] br_tgt =
      pc_p + 9'h1 +
      (rel9[8] ? (9'h100 - rel9) : rel9);

  wire br_en =
      (op == 5'h0A && z)  ||   // JZ
      (op == 5'h0B && !z) ||   // JNZ
      (op == 5'h0C && !n) ||   // JP
      (op == 5'h0D && n);      // JN

  always @(*) begin
    case (op)
      5'h09:
        next_pc = jmp_tgt;                  // JMP

      5'h0A,
      5'h0B,
      5'h0C,
      5'h0D:
        next_pc = br_en ? br_tgt : (pc_p + 9'h1);

      5'h19:
        next_pc = ra_data[8:0];             // RET via R7

      default:
        next_pc = pc_p + 9'h1;
    endcase
  end

  // ============================================================
  // Register file
  // ============================================================

  reg [2:0] ra_mux;
  reg [2:0] rb_mux;

  always @(*) begin
    case (op)

      // R-format instructions:
      // rs -> A
      // rt -> B
      5'h01,
      5'h02,
      5'h03,
      5'h04,
      5'h05,
      5'h06,
      5'h07,
      5'h08:
        begin
          ra_mux = f_rs;
          rb_mux = f_rt;
        end

      // I-format instructions:
      // rd -> A
      default:
        begin
          ra_mux = f_rd;
          rb_mux = 3'b000;
        end

    endcase
  end

  wire [15:0] ra_data;
  wire [15:0] rb_data;

  tt_um_msf_regfile u_rf (
      .clk(clk),
      .rst_n(rst_n),

      .ra(ra_mux),
      .rb(rb_mux),

      .we(rf_we),
      .wa(rf_waddr),
      .wd(rf_wdata),

      .qa(ra_data),
      .qb(rb_data)
  );

  // ============================================================
  // ALU
  //
  // These are REG because they are assigned inside always @(*).
  // ============================================================

  reg [3:0]  alu_op;
  reg [15:0] alu_a;
  reg [15:0] alu_b;

  wire [15:0] alu_y;
  wire        alu_z;
  wire        alu_n;

  always @(*) begin

    // Safe defaults
    alu_op = 4'd9;
    alu_a  = ra_data;
    alu_b  = 16'h0000;

    case (op)

      5'h01:
        begin
          alu_op = 4'd0;
          alu_a  = ra_data;
          alu_b  = rb_data;
        end

      5'h02:
        begin
          alu_op = 4'd1;
          alu_a  = ra_data;
          alu_b  = rb_data;
        end

      5'h03:
        begin
          alu_op = 4'd2;
          alu_a  = ra_data;
          alu_b  = rb_data;
        end

      5'h04:
        begin
          alu_op = 4'd3;
          alu_a  = ra_data;
          alu_b  = rb_data;
        end

      5'h05:
        begin
          alu_op = 4'd4;
          alu_a  = ra_data;
          alu_b  = rb_data;
        end

      5'h06:
        begin
          alu_op = 4'd5;
          alu_a  = ra_data;
          alu_b  = {13'h0000, f_rt};
        end

      5'h07:
        begin
          alu_op = 4'd6;
          alu_a  = ra_data;
          alu_b  = {13'h0000, f_rt};
        end

      5'h08:
        begin
          alu_op = 4'd9;
          alu_a  = ra_data;
          alu_b  = 16'h0000;
        end

      5'h15:
        begin
          alu_op = 4'd7;
          alu_a  = ra_data;
          alu_b  = 16'h0000;
        end

      5'h16:
        begin
          alu_op = 4'd8;
          alu_a  = ra_data;
          alu_b  = 16'h0000;
        end

      default:
        begin
          alu_op = 4'd9;
          alu_a  = ra_data;
          alu_b  = 16'h0000;
        end

    endcase
  end

  tt_um_msf_alu u_alu (
      .a(alu_a),
      .b(alu_b),
      .op(alu_op),
      .y(alu_y),
      .z(alu_z),
      .n(alu_n)
  );

  // ============================================================
  // Register file write mux
  // ============================================================

  reg        rf_we_w;
  reg [15:0] rf_wdata_w;

  always @(*) begin

    rf_we_w    = 1'b0;
    rf_wdata_w = 16'h0000;

    case (op)

      5'h00:
        begin
          rf_we_w    = 1'b1;
          rf_wdata_w = imm8e;
        end

      5'h01,
      5'h02,
      5'h03,
      5'h04,
      5'h05,
      5'h06,
      5'h07,
      5'h08,
      5'h15,
      5'h16:
        begin
          rf_we_w    = 1'b1;
          rf_wdata_w = alu_y;
        end

      5'h11:
        begin
          rf_we_w    = 1'b1;
          rf_wdata_w = {8'h00, uio_in};
        end

      5'h14:
        begin
          rf_we_w    = 1'b1;
          rf_wdata_w = cnt;
        end

      5'h17:
        begin
          rf_we_w    = 1'b1;
          rf_wdata_w = {8'h00, ui_in};
        end

      5'h18:
        begin
          rf_we_w    = 1'b1;
          rf_wdata_w = {7'h00, next_pc};
        end

      5'h1E:
        begin
          rf_we_w    = 1'b0;
          rf_wdata_w = ra_data;
        end

      default:
        begin
          rf_we_w    = 1'b0;
          rf_wdata_w = 16'h0000;
        end

    endcase
  end

  assign rf_we = rf_we_w & ~stall;

  assign rf_wdata = rf_wdata_w;

  // CALL saves return address in R7.
  assign rf_waddr =
      (op == 5'h18) ? 3'b111 : f_rd;

  assign rf_ra = ra_mux;
  assign rf_rb = rb_mux;

  // ============================================================
  // Instruction fetch
  // ============================================================

  always @(posedge clk or negedge rst_n) begin

    if (!rst_n) begin
      pc_p <= 9'h000;
      ir   <= 16'h0000;
    end

    else if (!stall) begin
      pc_p <= next_pc;
      ir   <= imem_rd;
    end

  end

  assign imem_raddr = pc_p;

  // ============================================================
  // Flags
  // ============================================================

  reg prev_stall;

  always @(posedge clk or negedge rst_n) begin

    if (!rst_n) begin
      z          <= 1'b0;
      n          <= 1'b0;
      prev_stall <= 1'b0;
    end

    else begin

      prev_stall <= stall;

      if (stall && timeout == 16'h0000) begin
        z <= 1'b1;
      end

      else if (stall || prev_stall) begin
        // Hold flags during WAIT and resume cycle.
      end

      else begin
        z <= alu_z;
        n <= alu_n;
      end

    end

  end

  // ============================================================
  // Cycle counter
  // ============================================================

  always @(posedge clk or negedge rst_n) begin

    if (!rst_n)
      cnt <= 16'h0000;

    else
      cnt <= cnt + 16'h0001;

  end

  // ============================================================
  // Shift datapath
  // ============================================================

  reg ser_out;

  always @(posedge clk or negedge rst_n) begin

    if (!rst_n) begin
      osr     <= 16'h0000;
      ser_out <= 1'b0;
    end

    else if (!stall) begin

      if (op == 5'h1D) begin
        // SHIFT_IN
        osr <= {osr[14:0], uio_in[7]};
      end

      else if (op == 5'h1C) begin
        // SHIFT_OUT
        // Output old MSB.
        ser_out <= osr[15];

        osr <= {
          osr[14:0],
          ra_data[0]
        };
      end

    end

  end

  assign serial_out = ser_out;

  // ============================================================
  // Stall logic
  // ============================================================

  wire edge_event =
      (uio_in[sel_pin] != pin_prev[sel_pin]);

  wire cmp_match =
      (cnt == ra_data);

  always @(posedge clk or negedge rst_n) begin

    if (!rst_n) begin
      stall   <= 1'b0;
      timeout <= 16'h0000;
    end

    else if (stall) begin

      timeout <= timeout - 16'h0001;

      if ((op == 5'h1A) && edge_event) begin
        stall <= 1'b0;
      end

      else if ((op == 5'h1B) && cmp_match) begin
        stall <= 1'b0;
      end

      else if (timeout == 16'h0000) begin
        stall <= 1'b0;
      end

    end

    else begin

      if ((op == 5'h1A) || (op == 5'h1B)) begin
        stall   <= 1'b1;
        timeout <= 16'hFFFF;
      end

      else begin
        stall   <= 1'b0;
        timeout <= 16'h0000;
      end

    end

  end

  // ============================================================
  // Previous input pins
  // ============================================================

  always @(posedge clk or negedge rst_n) begin

    if (!rst_n)
      pin_prev <= 8'h00;

    else
      pin_prev <= uio_in;

  end

  // ============================================================
  // Pin I/O control
  // ============================================================

  assign pin_w =
      ra_data[7:0];

  assign pin_w_en =
      (op == 5'h10) & ~stall;

  assign oe_clr =
      imm8;

  assign oe_clr_en =
      (op == 5'h13) & ~stall;

  assign oe_set =
      imm8;

  assign oe_set_en =
      (op == 5'h12) & ~stall;

  assign uo_w =
      8'h00;

  assign uo_w_en =
      1'b0;

  // ============================================================
  // IMEM write
  // ============================================================

  always @(posedge clk or negedge rst_n) begin

    if (!rst_n)
      waddr <= 9'h080;

    else if ((op == 5'h1E) && !stall) begin

      if (waddr == 9'h1FF)
        waddr <= 9'h080;

      else
        waddr <= waddr + 9'h001;

    end

  end

  assign sram_we =
      (op == 5'h1E) & ~stall;

  assign sram_waddr =
      waddr;

  assign sram_wd =
      rf_wdata_w;

endmodule

`default_nettype wire