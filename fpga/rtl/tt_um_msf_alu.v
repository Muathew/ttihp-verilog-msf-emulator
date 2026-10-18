// SPDX-License-Identifier: Apache-2.0
// msfcore ALU (combinational). op[3:0]: 0 ADD 1 SUB 2 AND 3 OR 4 XOR 5 SHL
// 6 SHR 7 INCR 8 DECR 9 MOV (pass a). SHL/SHR shift by b[2:0] (0..7).
// z/n are computed for ADD/SUB/INCR/DECR only (0 otherwise); tt_um_msf_ctrl
// writes them to the flags only for ADDS/SUBS/INCR/DECR (isa.md section 1).
`default_nettype none

module tt_um_msf_alu (
    input  wire [15:0] a,
    input  wire [15:0] b,
    input  wire [3:0]  op,
    output wire [15:0] y,
    output wire        z,
    output wire        n
);
  wire [15:0] add_r = a + b;
  wire [15:0] sub_r = a - b;
  wire [15:0] shl_r = a << b[2:0];
  wire [15:0] shr_r = a >> b[2:0];

  reg [15:0] y_r;
  always @(*) begin
    case (op)
      4'd0:    y_r = add_r;
      4'd1:    y_r = sub_r;
      4'd2:    y_r = a & b;
      4'd3:    y_r = a | b;
      4'd4:    y_r = a ^ b;
      4'd5:    y_r = shl_r;
      4'd6:    y_r = shr_r;
      4'd7:    y_r = a + 16'd1;
      4'd8:    y_r = a - 16'd1;
      default: y_r = a;  // MOV
    endcase
  end

  wire flag_en = (op == 4'd0) || (op == 4'd1) || (op == 4'd7) || (op == 4'd8);
  assign y = y_r;
  assign z = flag_en ? (y_r == 16'h0) : 1'b0;
  assign n = flag_en ? y_r[15] : 1'b0;
endmodule
