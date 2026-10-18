// SPDX-License-Identifier: Apache-2.0
// msfcore CRC unit (isa.md §6.3): 32-bit CRC and POLY registers, bit-serial
// engine (one bit per cycle). CRC_W loads `len` = cfg[3:0]+1 data bits and
// the unit is busy for exactly `len` cycles.
//   normal    (cfg[4]=0): MSB-first, left-aligned:  fb = crc[31]^bit, crc = (crc<<1) ^ (fb?poly:0)
//   reflected (cfg[4]=1): LSB-first, right-aligned: fb = crc[0]^bit,  crc = (crc>>1) ^ (fb?poly:0)
// Any CRC width 1..32 works in either form (align POLY/init/result accordingly).
`default_nettype none

module tt_um_msf_crc (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        per_we,
    input  wire [4:0]  per_sub,
    input  wire [15:0] per_wd,
    output wire        busy,
    output wire [15:0] crc_lo,
    output wire [15:0] crc_hi
);
  localparam [4:0] X_CRCCFG = 5'd19, X_CRCW = 5'd20, X_CRCSETL = 5'd23,
                   X_CRCSETH = 5'd24, X_POLL = 5'd25, X_POLH = 5'd26;

  reg [31:0] crc, poly;
  reg [3:0]  len_m1;
  reg        refl;
  reg [15:0] dsr;
  reg [4:0]  rem;

  wire       bit_in = refl ? dsr[0] : dsr[15];
  wire       fb     = (refl ? crc[0] : crc[31]) ^ bit_in;
  wire [31:0] stepped = refl ? ((crc >> 1) ^ (fb ? poly : 32'h0))
                             : ((crc << 1) ^ (fb ? poly : 32'h0));

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      crc    <= 32'h0;
      poly   <= 32'h0;
      len_m1 <= 4'd7;
      refl   <= 1'b0;
      dsr    <= 16'h0;
      rem    <= 5'd0;
    end else begin
      if (rem != 5'd0) begin
        crc <= stepped;
        dsr <= refl ? (dsr >> 1) : (dsr << 1);
        rem <= rem - 5'd1;
      end
      if (per_we) begin
        case (per_sub)
          X_CRCCFG:  begin len_m1 <= per_wd[3:0]; refl <= per_wd[4]; end
          X_CRCW: begin
            // MSB-first: put data bit len-1 at dsr[15]
            dsr <= refl ? per_wd : (per_wd << (4'd15 - len_m1));
            rem <= {1'b0, len_m1} + 5'd1;
          end
          X_CRCSETL: crc[15:0]   <= per_wd;
          X_CRCSETH: crc[31:16]  <= per_wd;
          X_POLL:    poly[15:0]  <= per_wd;
          X_POLH:    poly[31:16] <= per_wd;
          default: ;
        endcase
      end
    end
  end

  assign busy   = (rem != 5'd0);
  assign crc_lo = crc[15:0];
  assign crc_hi = crc[31:16];
endmodule
