# FPGA implementation summary

## Target and integration

The FPGA setup now targets the AUP-ZU3 (XCZU3EG) instead of the Tiny Tapeout
ICE40 flow. The detailed board, programming, and remaining validation
instructions are in [the AUP-ZU3 porting plan](./fpga-porting-plan.md).

The emulator remains in the PL. The current generated block design connects
the Zynq PS's 32-bit EMIO GPIO directly to the wrapper, which returns
`uo_out`, `uio_out` and `uio_oe` to the PS. During programming, the wrapper
selects PS-provided parallel boot data/strobe and hands the bidirectional
pins back to PMOD JA after firmware starts. For testing, the PS can keep the
pins, drive `uio_in` itself and single-step the core clock. The board mapping
uses slide switches for `ui_in`, LEDs for `uo_out`, a pushbutton for reset,
and PMOD JA for runtime `uio`.

## Work completed

- Set up the FPGA RTL source manifest and retained the shared core RTL in
  `fpga/rtl`.
- Replaced the FPGA SRAM's ASIC macro dependency with synchronous inferred
  512 x 16 dual-port memory. The port-A instruction read, enabled port-B
  read/write, one-cycle registered reads, and disabled-port data hold are
  preserved.
- Added the AUP-ZU3 PL wrapper with PS/PMOD ownership muxing and synchronized
  reset release. It also has a step mode: the core clock passes through a
  `BUFGCE` and advances exactly one cycle per PS request, after which the
  wrapper captures the pins and acknowledges.
- Added board-specific switch, pushbutton, LED, and PMOD JA constraints based
  on the supplied board XDC.
- Added `fpga/vivado/msf_bd.tcl`, a Vivado 2024.1 generated block-design
  script. It instantiates the PS (32 EMIO GPIO bits), wrapper, and reset IP,
  configures `pl_clk0` to 12 MHz, and validates/saves the BD. It does not add
  sources or constraints, create the HDL wrapper, synthesize, implement,
  generate a bitstream, or export an XSA. If no project is open, it creates a
  project using hard-coded part and board-part values.
- Added a Vitis standalone C loader and firmware-image generator. It uses
  the `XGpioPs` driver on EMIO bank 3 through `fpga/ps/common`, which
  matches the BD.
- Added a Vitis protocol-test application (`fpga/ps/hil`): C ports of the
  cocotb protocol models and of every `test/test_firmware.py` test, run on
  the core in step mode. The same C runs against the RTL in simulation
  through a lockstep EMIO model (`fpga/tests/run_ps_hil.py`).
- Added focused RAM, board-wrapper (including step mode), and PS compile
  checks.

The ASIC RTL and physical-design flow remain separate and unchanged.

## Verification

- Existing Cocotb regression using the FPGA RTL source directory: **103 passed,
  0 failed**.
- FPGA SRAM latency/read/write/hold simulation: passed.
- AUP-ZU3 wrapper PS ownership, PMOD handoff, reset, status and step-mode
  simulation: passed.
- PS C protocol tests against the RTL through the EMIO interface
  (`run_ps_hil.py`): **13 passed, 0 failed**. Deliberately broken variants
  (two core edges per step, an SPI slave sampling on the wrong edge, wrong
  I2C read data) each fail, so the checks are live.
- Bare-metal loader and protocol-test compile check against `XGpioPs`
  stubs (classic and SDT driver flows): passed.
- Full FPGA RTL source compile and `git diff --check`: passed.

## Not yet verified

Vivado and Vitis were unavailable in the implementation environment.
Block-design validation in the installed tool, HDL wrapper generation,
bitstream/XSA generation, BRAM utilization/timing reports (including the
`BUFGCE` core clock), Vitis platform integration, and on-board
loading/electrical behavior therefore remain unverified with the matching AMD
tools and physical board revision.
