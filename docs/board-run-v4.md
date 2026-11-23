# Board run of v4: the checklist

For Muath, for the next session on the AUP-ZU3 with the LP-MSPM0G3519. It runs `main` as
signed off (fine mode and the boot ROM's registered address are new since the 17 Nov
bitstream), first at 12 MHz, then at 48 MHz. Part C is optional.

What it should show, against the 17 Nov run:

| | 17 Nov | This run |
|---|---|---|
| Bitstream | v4 before fine mode | `main` as signed off at 50 MHz |
| HIL C tests | 60 of 67 (the 7 `cap_*` were a harness fault, fixed in 8ffa5a2) | 67 of 67, at 12 and at 48 MHz |
| v3 board test against the MCU | 304 of 304 at 12 MHz | the same at 12 MHz, and at 48 MHz |

The logs (`*.log.txt`) are git-ignored, so send them to Sam rather than committing them.

## 0. Before you start

1. `git pull` on `main`, then check `python tools/check_fpga_sync.py` prints `fpga/rtl matches src`.
2. Wiring as on 17 Nov (`external-mcu_testing.md`, J16 and J17 included). Nothing changes
   between 12 and 48 MHz.
3. The MCU keeps the build that ran on 17 Nov: its code is the same at both clocks.

## A. 12 MHz

1. **Bitstream.** `vivado -mode batch -source fpga/vivado/build.tcl`
   - `fpga/build/vivado/build_info.txt` must show the commit you pulled, `pl_clk0 12.0000 MHz`,
     and WNS and WHS of 0 or more (the script stops otherwise).
2. **Platform from the new XSA.** This is the step that went wrong on 14 Nov: Vitis
   programs the bitstream inside the platform, and an old platform programs an old
   bitstream.
   - From a Vitis command prompt at the repository root: `vitis -s fpga/ps/build_platform.py`
   - If the script fails (it is new and has not run on a Vitis install yet), do it by hand:
     delete `fpga/build/vitis/workspace/msf_aup_zu3_platform`, then in Vitis make a platform
     component named `msf_aup_zu3_platform` from `fpga/build/vivado/msf_aup_zu3.xsa`,
     standalone, `psu_cortexa53_0`, domain `a53_standalone`, and build it.
3. **HIL.** `python fpga/ps/hil/gen_images.py`, then rebuild the `hil` app against the new
   platform. If the app holds copies of the sources rather than links, import
   `fpga/ps/hil/msf_tests.c` again: the `cap_*` fix is in it.
   - Expect `ALL PASSED`, 67 of 67. Keep the whole log: Sam compares its digest lines with
     the simulation's.
4. **v3 board test.**
   - Delete `fpga/build/vitis/workspace_mcu_v3`, so the app is made again against the new
     platform, then `vitis -s fpga/ps/mcu_v3/build_vitis.py`.
   - The Zynq console must print `Loading the v3 board peer (278 words, for a 12 MHz core clock)`.
   - Reset the MCU and let it run at least 20 passes. Expect every check to pass, with
     `LATE=8011`, `8010`, `8021` in guard states B, C and D.
5. **`gapless()`.** Commit the `gapless()` (`mcu_ext/protocol_tests/v3_test.c`) that your MCU
   build actually uses, with a line on why it needs the slack. On 17 Nov every stream read
   `3647/3456 cycles -> PASS`, which the repository's version calls NOT VALID.
   - If you would rather not, rebuild the MCU from the repository and send that log, so
     the 304 of 304 in the README can be reproduced.

## B. 48 MHz

At 48 MHz the core runs 4 cycles for every one it ran at 12 MHz, and the peer's constants
are scaled to match. A pass therefore takes the same microseconds at both clocks: the UART
stays at 115200, and each guard state is still held for 20 ms. The MCU's code is unchanged.

One thing differs: the line signals are 20 and 16 core cycles a bit instead of 5 and 4. The
MCU's SPI places edges on a 62.5 ns grid, which at 5 cycles a bit of 48 MHz is more spread
than any receiver takes. So this run shows the core running free at 48 MHz against a real
MCU. The 5- and 4-cycle ratios stay the 12 MHz run's evidence.

1. **Bitstream.** `vivado -mode batch -source fpga/vivado/build.tcl -tclargs --mhz 48`
   - This writes `fpga/build/vivado_48mhz` and leaves the 12 MHz build alone.
   - `build_info.txt` must show `pl_clk0 48.0000 MHz`, and WNS and WHS of 0 or more.
   - The clock comes from the PS's DPLL ÷ 25: the IOPLL, which makes the 12 MHz, can only
     give 48.39 or 46.88 MHz. If Vivado refuses the DPLL as `pl_clk0`'s source, or the
     build stops on `pl_clk0 is ... MHz, not 48`, send Sam the log rather than picking
     another clock: the firmware's timing assumes 48.000.
2. **Platform from the 48 MHz XSA.** In the Vitis command prompt:
   `set MSF_CORE_MHZ=48`, then `vitis -s fpga/ps/build_platform.py`. By hand as in A2,
   with `fpga/build/vivado_48mhz/msf_aup_zu3.xsa`.
3. **HIL at 48 MHz.** Rebuild the `hil` app against this platform and run it.
   - Expect 67 of 67 with **the same digest lines as in A3**. The C tests step the core one
     clock at a time, and the boot loader's free-run polls are left out of the digests, so
     the clock should change nothing.
   - A digest that differs is a real finding: send both logs.
4. **v3 board test at 48 MHz.**
   - With `MSF_CORE_MHZ=48` still set: delete `fpga/build/vitis/workspace_mcu_v3`, then
     `vitis -s fpga/ps/mcu_v3/build_vitis.py`.
   - The console must print `for a 48 MHz core clock`, and its pass summaries
     `cal  T=... cycles of 12 MHz`.
   - Same MCU build, at least 20 passes, the same verdicts as in A4.
   - The MCU's own log still says `against the FPGA's 12 MHz`. That is expected: the peer
     sends T in 12 MHz cycles, so the MCU code needs no change.

## C. If time is left: msfcore as an SWD probe (about 15 minutes)

The `swd_mcu` HIL test reads a Cortex-M's ID over SWD with msfcore as the probe. It is
written but has never run on a board; it would be our first protocol demo against a real
chip beyond UART, SPI and I2C.

- Wiring and the build switch (`MSF_HIL_MCU`) are in `fpga/ps/README.md`, "SWD with a
  microcontroller on PMOD JA". It needs the XDS110's SWDIO and SWCLK jumpers off, and
  different pins from the board test.
- Either bitstream will do.

## What to send back

| File | From |
|---|---|
| `build_info.txt` from `fpga/build/vivado` and `fpga/build/vivado_48mhz` | A1, B1 |
| HIL console logs, whole: `hil_12mhz.log.txt`, `hil_48mhz.log.txt` | A3, B3 |
| v3 test, both consoles, whole: `v3_12mhz_fpga.log.txt`, `v3_12mhz_mcu.log.txt`, and the same for 48 MHz | A4, B4 |
| The `gapless()` commit, or the log of the repository's build | A5 |
| SWD log, and a photo of the wiring | C |

## If something goes wrong

| Seen | Points at |
|---|---|
| No v3 banner on the MCU after switching clocks; v2 passes that time out | The app was built for the other clock. Its first line says which: rebuild it with the matching `MSF_CORE_MHZ` |
| MCU `cal ... FAIL`, T near 3072 or 49152 | The same: the app and the bitstream are for different clocks |
| HIL 60 of 67, `cap_*` failing | The `hil` app was built from the old `msf_tests.c` |
| HIL passes but the v3 test behaves like an older core (`LATE=0001`) | The old bitstream is still loaded: do the platform step again (A2 or B2) |
| Anything else in the v3 test | The table in `external-mcu_testing.md`, "Reading them" |
