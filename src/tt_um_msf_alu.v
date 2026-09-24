// SPDX-License-Identifier: Apache-2.0
// msfcore ALU + Z/N flags.
// op[3:0]: 0=ADD 1=SUB 2=AND 3=OR 4=XOR 5=SHL 6=SHR 7=INCR 8=DECR 9=MOV
// Z/N set only for ops {0,1,7,8}; cleared otherwise (MATCH THE ISA - other ops must NOT touch).
// STATUS: pure combinational leaf; never compiled (sim not built).
// KNOWN QUIRK: SHL/SHR use b[2:0] (3-bit shift); shift amount >=8 gives 0 -- spec'd.
// BUDGET: ~300 cells (16-bit adder/sub + logic + shift).
// INTEGRATION: driven once-per-cycle from tt_um_msf_ctrl (alu_a/alu_b muxed from regfile).
// NEXT: unit test with random vectors vs a Python reference model.
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
