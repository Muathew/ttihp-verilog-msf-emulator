# FPGA implementation summary

## Target and integration

The FPGA setup now targets the AUP-ZU3 (XCZU3EG) instead of the Tiny Tapeout
ICE40 flow. The detailed board, programming, and remaining validation
instructions are in [the AUP-ZU3 porting plan](./fpga-porting-plan.md).

The emulator remains in the PL. The current generated block design connects
the Zynq PS's 11-bit EMIO GPIO directly to the wrapper, and returns the
wrapper's 8-bit core status to the PS over EMIO. During programming, the
wrapper selects PS-provided parallel boot data/strobe and hands the
bidirectional pins back to PMOD JA after firmware starts. The board mapping
uses slide switches for `ui_in`, LEDs for `uo_out`, a pushbutton for reset,
and PMOD JA for runtime `uio`.

## Work completed

- Set up the FPGA RTL source manifest and retained the shared core RTL in
  `fpga/rtl`.
- Replaced the FPGA SRAM's ASIC macro dependency with synchronous inferred
  512 x 16 dual-port memory. The port-A instruction read, enabled port-B
  read/write, one-cycle registered reads, and disabled-port data hold are
  preserved.
- Added the AUP-ZU3 PL wrapper with PS/PMOD ownership muxing, synchronized
  reset release, and PS-domain synchronization for core status.
- Added board-specific switch, pushbutton, LED, and PMOD JA constraints based
  on the supplied board XDC.
- Added `fpga/vivado/msf_bd.tcl`, a Vivado 2024.1 generated block-design
  script. It instantiates the PS, wrapper, and reset/concat/constant IP,
  configures `pl_clk0` to 12 MHz, and validates/saves the BD. It does not add
  sources or constraints, create the HDL wrapper, synthesize, implement,
  generate a bitstream, or export an XSA. If no project is open, it creates a
  project using hard-coded part and board-part values.
- Added a Vitis standalone C loader and firmware-image generator. The C code
  currently uses the `XGpio` AXI GPIO driver and expects two AXI GPIO
  channels. This does **not** match the current BD, which has no AXI GPIO IP
  and uses PS EMIO GPIO directly. The PS firmware loading path is therefore
  not yet integrated end to end.
- Added focused RAM, board-wrapper, and loader compile checks.

The ASIC RTL and physical-design flow remain separate and unchanged.

## Verification

- Existing Cocotb regression using the FPGA RTL source directory: **103 passed,
  0 failed**.
- FPGA SRAM latency/read/write/hold simulation: passed.
- AUP-ZU3 wrapper PS ownership, PMOD handoff, reset, and status simulation:
  passed.
- Bare-metal loader syntax and generated firmware-header check: passed.
- Full FPGA RTL source compile and `git diff --check`: passed.

## Not yet verified

Vivado and Vitis were unavailable in the implementation environment.
Block-design validation in the installed tool, HDL wrapper generation,
bitstream/XSA generation, BRAM utilization/timing reports, loader/BD
integration, Vitis platform integration, and on-board
loading/electrical behavior therefore remain unverified with the matching AMD
tools and physical board revision.
