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

1. In Vivado 2024.1, add the files in `fpga/rtl/sources.f` (never
   `fpga/tests/sim_models/BUFGCE.v`) and `fpga/board/constraints/msf_aup_zu3.xdc`.
2. `source fpga/vivado/msf_bd.tcl` in the Tcl console.
3. Create the HDL wrapper, set it as top, generate the bitstream, export
   the XSA. Check the reports: timing at 12 MHz including the `BUFGCE`
   path, the SRAM mapped to block RAM, no unresolved IHP cells.
4. Build and run the PS apps as in `fpga/ps/README.md`: the protocol tests
   first, then the loader (`loader/blink.s` blinks LED0 at 1 Hz).

## Verification

| Check | Command | Status |
|---|---|---|
| cocotb regression on `fpga/rtl` | `run_tests.py` with `SRC` pointed at `fpga/rtl` | 130/130 |
| FPGA SRAM | `sh fpga/tests/run_sram_test.sh` | pass |
| Wrapper: ownership, reset, status, step mode | `sh fpga/tests/run_aup_zu3_io_test.sh` | pass |
| PS apps compile (Vitis stubs, classic and SDT drivers) | `sh fpga/tests/run_ps_loader_compile_test.sh` | pass |
| PS C protocol tests against the RTL | `python fpga/tests/run_ps_hil.py` | 14/14 |

First Vivado run (19 Oct, Vivado 2024.1): `msf_bd.tcl` created the block
design, it validated, and its HDL wrapper generated. Out-of-context
synthesis of the processor system reset then crashed inside Vivado
(`TclStackFree: incorrect freePtr`), a tool fault rather than a design
error. Things to try: one thread (`set_param general.maxThreads 1`), a
fresh project at a short ASCII-only path, `-jobs 1`.

Not yet done (needs Vivado, Vitis and the board): synthesis and timing,
Vitis build against the real drivers, and every on-board check. Confirm
first that each step request gives exactly one core edge.

## Open items

* `fpga/rtl/tt_um_msf_bootrom_data.v` is unused (the ROM is pasted into
  `tt_um_msf_bootrom.v`).
* The XDC header refers to `fpga/board/constraints/zu3.xdc`, which isn't in
  the repository; check the pins against the board schematic.
