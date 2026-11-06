# FPGA port: AUP-ZU3

The msfcore runs in the programmable logic (PL) of the AUP-ZU3 V3I1 board
(XCZU3EG). The Zynq processing system (PS) loads its firmware and runs the
protocol tests. The ISA, cycle timing, boot protocol and pin behaviour are
the same as the ASIC; the ASIC sources and flow in `src/` are untouched.

PS software (loader, protocol tests, EMIO bit map, how to build in Vitis):
[`fpga/ps/README.md`](../fpga/ps/README.md).

## Board mapping

| AUP-ZU3 | Core | Use |
|---|---|---|
| `PL_USER_SW[7:0]` | `ui_in[7:0]` | inputs while the PS doesn't own the pins |
| `PL_USER_LED[7:0]` | `uo_out[7:0]` | status / firmware output |
| `PL_USER_PB[0]` | reset | manual reset while pressed; the board's buttons are active high (reference manual: "All GPIO inputs and outputs are active high"; `BUTTON_ACTIVE_LOW` = 0 in the wrapper) |
| PMOD JA `[7:0]` | `uio[7:0]` | protocol pins (3.3 V), high-Z while the PS owns the pins |
| PS EMIO GPIO bank 3 (32 bits) | everything above | loading, single-step, pin read-back |

The core clock is `pl_clk0` at 12 MHz (the boot ROM's 115200 baud timing).
It reaches the core through a `BUFGCE`, so the PS can also step it one
cycle at a time. The core is held in reset until the PS releases it.

## Sources

| Path | Contents |
|---|---|
| `fpga/rtl/sources.f` | the FPGA source list |
| `fpga/rtl/aup_zu3_io.v` | board wrapper: pin ownership, reset, step mode, EMIO |
| `fpga/rtl/tt_um_msf_sram.v` | 1024 x 16 inferred block RAM instead of the two IHP macros (same ports, registered reads, read-data hold) |
| `fpga/rtl/tt_um_msf_bootrom.v` | boot ROM with the 128 words pasted in (no `include`) |
| `fpga/rtl/*` (others) | byte-identical copies of `src/` |
| `fpga/board/constraints/msf_aup_zu3.xdc` | switch, button, LED and PMOD JA pins |
| `fpga/vivado/msf_bd.tcl` | Vivado 2024.1 block design |
| `fpga/vivado/build.tcl` | batch build: project, block design, bitstream, XSA, reports |

`fpga/rtl` is a copy: **a core fix in `src/` must be copied there too.**
Check with `git diff --no-index --ignore-cr-at-eol --stat src fpga/rtl`
before an FPGA build: of the files on both sides, only `tt_um_msf_sram.v`
and `tt_um_msf_bootrom.v` may differ (the rest of the list exists on one
side only). `cmp` is no use on a Windows checkout, where line endings can
differ.

## Block design

`msf_bd.tcl` creates `msf_zynq_system`: the PS (`zynq_ultra_ps_e` 3.5) with
32 EMIO GPIO bits and `pl_clk0` = 12 MHz, the `aup_zu3_io` module reference
and a processor system reset. There is no AXI. EMIO `o`/`t` go to the
wrapper's `ps_gpio_o`/`ps_gpio_t` and its `ps_gpio_i` drives `emio_gpio_i`.

The script only creates and validates the block design. If no project is
open it makes `myproj/project_1` (part `xczu3eg-sfvc784-2-e`, board part
`realdigital.org:aup-zu3-8gb:part0:1.0`); if one is open it uses it without
checking the part.

## Build

From the repository root, with Vivado 2024.1:

    vivado -mode batch -source fpga/vivado/build.tcl

It makes a fresh project in `fpga/build/vivado` (or the directory given
after `-tclargs`), adds `fpga/rtl/sources.f` and the XDC, sources
`msf_bd.tcl`, synthesises the block design in one run (no out-of-context
IP runs), and writes `msf_aup_zu3.xsa` with the bitstream, plus
`utilization.rpt`, `timing.rpt` and `build_info.txt` (commit, WNS, cell
counts: the figures §2.1 of the plan asks for). After synthesis it stops if
an IHP cell or a black box is left or the SRAM is not in block RAM, and at
the end if timing is not met. Expected size: about 3,400 LUTs, 1,700
flip-flops and one RAMB18 for the core (Yosys `synth_xilinx` estimate), a
few per cent of the XCZU3EG.

By hand: add the files in `fpga/rtl/sources.f` (never
`fpga/tests/sim_models/BUFGCE.v`, never anything from `src/`) and the XDC,
`source fpga/vivado/msf_bd.tcl`, create the HDL wrapper, set it as top,
generate the bitstream and export the XSA.

Then build and run the PS apps as in `fpga/ps/README.md`: the protocol
tests first, then the loader (`loader/blink.s` blinks LED0 at 1 Hz).

### SRAM "not connected to power", routing congestion

Both mean the project holds the chip's sources (`src/`). Vivado always
defines `SYNTHESIS`, so `src/tt_um_msf_sram.v` instantiates the two IHP
SRAM macros: hard blocks for the chip, whose supply pins only the ASIC flow
wires (`src/config.json`, `PDN_MACRO_CONNECTIONS`). An FPGA has no such
pins and Vivado has no logic for the macro. Remove every `src/` file and
use `fpga/rtl` (or run `build.tcl`, which takes nothing else); its
`tt_um_msf_sram.v` is one inferred block RAM. A memory built from flip-flops
and multiplexers instead (8,192 bits a macro) is the likely source of the
congestion, since the right sources use a few per cent of the device.

## Verification

| Check | Command | Status |
|---|---|---|
| cocotb regression on `fpga/rtl` | `run_tests.py` with `SRC` pointed at `fpga/rtl` | 191/191 |
| FPGA SRAM | `sh fpga/tests/run_sram_test.sh` | pass |
| Wrapper: ownership, reset, status, step mode | `sh fpga/tests/run_aup_zu3_io_test.sh` | pass |
| PS apps compile (Vitis stubs, classic and SDT drivers) | `sh fpga/tests/run_ps_loader_compile_test.sh` | pass |
| PS C protocol tests against the RTL | `python fpga/tests/run_ps_hil.py` | 66/66 (28 Oct; 52 of them not yet run on the board) |
| **PS C protocol tests on the AUP-ZU3 board** | the `hil` app on the PS | **14/14 (23 Oct 2026)** |
| **Loader on the board** | `loader/blink.s` | **LED0 blinks** |
| **UART, I2C, SPI against an LP-MSPM0G3519, core running free** | `fpga/ps/mcu_test` and `mcu_ext/protocol_tests` | **6 Nov, firmware v3: UART and SPI pass both ways, I2C right on the wires**; one MCU test fix still to run: see [`external-mcu_testing.md`](../external-mcu_testing.md) |

First Vivado run (19 Oct, Vivado 2024.1): `msf_bd.tcl` created the block
design, it validated, and its HDL wrapper generated. Out-of-context
synthesis of the processor system reset then crashed inside Vivado
(`TclStackFree: incorrect freePtr`), a tool fault rather than a design
error. Things to try: one thread (`set_param general.maxThreads 1`), a
fresh project at a short ASCII-only path, `-jobs 1`.

On the board (23 Oct, after `c68bd0c` made the reset button active high):
all 14 C protocol tests passed, as in simulation: `crc_check_values`,
`boot_parallel_go`, `boot_bad_crc_then_retry`, `restart_direct_boot`,
`uart_tx_drift_free`, `spi_all_modes_full_duplex`, `i2c_write_ack`,
`i2c_address_nack`, `i2c_write_then_read_repeated_start`,
`i2c_constrained_random_with_stretching`, `usb_ls_tx_packet`,
`usb_ls_rx_packet`, `eth_10baset_frame_fcs_tpidl_nlp`,
`threads_blink_and_echo`; and the loader's 1 Hz blink drove LED0. The
board's core is the four-thread v2.1 RTL: `fpga/rtl` matches `src/` except
the SRAM wrapper and the boot ROM (`tools/check_fpga_sync.py`, run in CI).

The PS steps the core one clock at a time, so this checks logic and
protocol behaviour on real hardware, not timing at speed. Still to record:
the commit that was built, and Vivado's timing and utilisation.

The MCU test is different: the core runs free from `pl_clk0` at 12 MHz with
a real LP-MSPM0G3519 as controller, and the PS only watches `uo_out`. With
firmware v3 (6 Nov), UART and SPI passed at both ends on all 39 passes and the
FPGA counted no bad bytes. I2C is right on the wires, but the MCU's test read
each byte a pass late, which failed pass 1 after each reset. Its fix has not yet
run on the board, and the same Vivado figures are still to record.
[`external-mcu_testing.md`](../external-mcu_testing.md) has the details.

## Open items

* `fpga/rtl/tt_um_msf_bootrom_data.v` is unused (the ROM is pasted into
  `tt_um_msf_bootrom.v`).
* The XDC header refers to `fpga/board/constraints/zu3.xdc`, which isn't in
  the repository; check the pins against the board schematic.
