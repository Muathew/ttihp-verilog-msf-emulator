// SPDX-License-Identifier: Apache-2.0
// msfcore input glitch filter (isa.md §6.7), opt-in per uio pin. An enabled
// pin's level changes only once the synchronised input has held the new
// level for `len` cycles in a row: shorter pulses are removed, and every
// change it passes arrives `len` cycles later. A disabled pin (the reset
// state) is passed straight through, with no added latency.
// Configured by PW #6: rb[7:0] pins to filter, rb[11:8] len (0 = 1).
`default_nettype none

module tt_um_msf_filt (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  raw,         // synchronised uio inputs
    input  wire        pw_we,
    input  wire [2:0]  pw_port,
    input  wire [15:0] pw_data,
    output wire [7:0]  pins         // to the core and the SERDES
);
  localparam [2:0] P_FILT = 3'd6;

  reg [7:0] en;
  reg [3:0] len;
  wire [7:0] f;                     // filtered levels

  wire cfg = pw_we && pw_port == P_FILT;

  genvar i;
  generate
    for (i = 0; i < 8; i = i + 1) begin : g_pin
      reg       fi;                 // this pin's filtered level
      reg [3:0] ci;                 // cycles its input has differed from it
      wire [3:0] cn = ci + 4'd1;
      always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          fi <= 1'b0; ci <= 4'd0;
        end else if (cfg || raw[i] == fi) begin
          fi <= raw[i]; ci <= 4'd0;               // (re)start in step
        end else if (cn >= len) begin
          fi <= raw[i]; ci <= 4'd0;               // held long enough
        end else begin
          ci <= cn;
        end
      end
      assign f[i] = fi;
    end
  endgenerate

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      en <= 8'h00; len <= 4'd0;
    end else if (cfg) begin
      en <= pw_data[7:0]; len <= pw_data[11:8];
    end
  end

  assign pins = (en & f) | (~en & raw);
endmodule

`default_nettype wire
