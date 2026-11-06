# Plan: branch `fixes`, now `main`

`fixes` was merged into `main` on 28 Oct (a fast-forward to `2592db0`), and
`main` is the working version from then on (§6, decisions 1 and 5).

Branch `fixes` starts at `c68bd0c`, the head of `rtl-changes` and the build
that passed on the AUP-ZU3 board on 23 Oct. It covers what has to be fixed
before msfcore can be taped out, the evidence still to record, the ideas
worth taking from the 23 Oct look at the other entries, and the firmware
the brief names that we still lack.

Order of work: blockers (§1) first, because none of the rest counts if the
design can't go on the shuttle. Then evidence (§2), then new work (§3–§4),
then the write-up (§5).

## Status

Updated as work lands. The §6 decisions were taken provisionally as
*Default* so work could continue; Sam decided them all on 28 Oct.

| Item | Status | Notes |
|---|---|---|
| 1.1 CMOS5L port | done | workflows, GL sources, PDN: `Project.ExtendPowerStripes` (`librelane_plugin_msf_pdn.py`, `odb_sram_stripes.py`) moves the Metal4 stripes onto the SRAM supply columns; macros MX along the bottom; density 45. Local LibreLane run (same image as CI): routed with 0 DRC errors. Found and fixed on the way: the slow corner was timed with the *typical* SRAM library (overlapping lib patterns). CI (run 40001810132): GDS, precheck and the gate-level test pass on CMOS5L |
| 1.2 slow-corner timing | done | **Signed off at 30 ns (33 MHz) in every corner.** Place and route aim at 20 ns (`pnr.sdc`), sign-off checks 30 ns (`signoff.sdc`): aiming at 30 the tools optimised less and the slow corner missed (−3.21 ns, path 33.2 ns); aiming at 20 the path is ~27.8 ns. CI 40085473871 (6ea46bd): slow +2.17 ns, typical +10.72, fast +13.27, hold +0.11, no DRC, antenna or LVS errors, 114 max-slew and 32 max-cap warnings; precheck and the gate-level tests pass (run locally: CI jobs were refused, decision 7). Design repair after global routing (300 µm wires, 30 % margins), local run S (44a7d31): +2.49 ns slow, 6 max-slew and 2 max-cap warnings, all on SRAM pins, +1 400 buffers. **Adopted in `config.json` (28 Oct)**: local run F (6ea46bd's RTL with repair) signed off at slow +1.24 ns, typical +11.02, hold +0.07, no DRC, antenna or LVS errors, 2 max-slew and 2 max-cap warnings, all on SRAM pins (the worst path from the overloaded `A_DOUT[11]` has +3.04 ns); precheck and the five gate-level tests pass on it, and its netlist equals the RTL (`netlist_equiv.py`, 2 min 26 s: the repair added 1 729 buffers and no logic). On the same RTL the repair cost 0.93 ns of slow-corner slack (S gained 0.88): noise either way. Decided (decision 2): 33 MHz; typical silicon still makes 50 MHz. `verification.md` §7 |
| 1.3 formal CI | done, CI run pending | SymbiYosys + ABC (`--engine abc`) in CI with OSS CAD Suite: depth-16 BMC, covers and mutants passed in ~11 min (CI 40085473891). The v2.1 properties and their mutants (24 Oct): F12, every running thread owns a cycle in any four (no starvation); F8 and F9 already there, F9 now also in the capture module alone. Each has a mutant that must fail, and does: a push into a full FIFO (caught by F8 alone), capture ignoring the core on port B (F9), the rotation skipping a thread (F1 first, F12 alone too). A mutant stops at its first counterexample. Locally BMC of the three targets passes; the CI run waits for decision 7. Fixed earlier: SBY errors had counted as caught mutants |
| 1.4 tape-out branch | done | Sam (28 Oct): `fixes` merged into `main` the same day (fast-forward to `2592db0`), without waiting for CI, so that Muath can harden `main` locally; `main` is what gets taped out. No RTL freeze: `main` is the working version until the deadline, experiments go on branches (§6, decisions 1 and 5) |
| 2.1 record board pass | done (Vivado figures to come) | `fpga.md`, `verification.md` §5/§9 |
| 2.2 stale metadata | done | `info.yaml`, `info.md` |
| 2.3 wider board tests | done, 67 of 67 on the board | in `msf_tests.c`, passing in the HIL simulation and on the board: `jtag_probe`, `onewire_probe`, `swd_probe` (DPIDR, MEM-AP read/write, WAIT, no target), `spi_target_regs`, `spi_flash`, `i2c_target_eeprom` (100 kHz, and 400 kHz with clock stretching), `four_threads_fifo` (three exact square waves at once, and T0's counts reaching T3 through the FIFO in order), the bus monitors (`mon_uart_*` 3, `mon_i2c_*` 6, `mon_spi_*` 3: the same waves, faults and accuracy checks as `test_monitors.py`), `bridge_modes` (UART-to-SPI, modes 0-3), `fw_logic_analyzer` (capture: trigger, pre/post samples against a reference), `ps2_device_*` 3 (keyboard against a host model: scancodes, commands with ACK and resend, host inhibit), `can_*` 3 (receive and ACK, transmit, arbitration and no ACK, against CAN nodes with CRC-15 and stuffing), `fault_*` 4 (UART, I2C and SPI timing knobs move edges by exact amounts; UART tolerance 99-109 cycles against a 104 receiver), `fw_ws2812`, `filter_*` 4 (short pulses, every pin, delay, a spiky UART), `rx_manchester_*` 2 (jitter at ±2 %, TX-to-RX loopback), `debug_*` 5 (the debug view: registers, PC in bank 1, no strap, single step, page 1) and `tmo_per_thread`, with C models of a TAP chain, 1-Wire devices, an SW-DP with a MEM-AP, a UART host, an SPI controller, an I2C controller, a PS/2 host and CAN nodes (`msf_proto.c`, `msf_tests.c`). Where a Python test reads registers or the SRAM, small firmware in `fpga/ps/hil/firmware/` stores the result in RAM and the C side reads it back (`dump_ram`), or reads it on the debug view. On the board the PS sees the pins only after a step, so each view read is one core cycle; the tests read parked state or count it. `cap_*` 8 (the capture engine against `cap_ref`: sample mode, divisors to 4099, pre-trigger ring, edge against level, change mode, `ui` pins, no effect on core timing, overflow; the bench reads CNT once on the debug view and counts it, so the pins follow CNT exactly; a one-cycle error fails). Step 1 of §2.3 is done: every item on its list has C tests, and all 67 passed on the board in a run after 29 Oct (`fpga.md`). Step 2, free-running against real parts: an LP-MSPM0G3519 as UART, I2C and SPI controller, first run 5 Nov. On 6 Nov, with firmware v3 and the MCU's own I2C test fixed, UART, SPI and I2C pass at both ends on all 148 passes (`external-mcu_testing.md`). `swd_mcu`, the core as SWD probe for a real MCU, has not been run |
| 2.4 `fpga/rtl` sync | done | `tools/check_fpga_sync.py` in the `test` workflow; the repo copies were already identical (CRLF only in this checkout) |
| 2.5 mutation re-run | done | v2.2: 37 of 40 killed, the other 3 equivalent (`verification.md` §8). Three gaps closed by new tests (RX pop on the landing cycle, filter on every pin, capture divisors 9-4099). `mutation.py` now runs the unmutated design first (a failing 1-Wire test had faked six kills) and every module by default |
| 2.6 GL simulation | done | `test_gl_firmware.py` (in the GDS action's gate-level job): firmware loaded through the boot ROM's parallel loader and checked at the pins only, as on silicon: `uart_tx.s` drift-free, `thread_demo.s` (T0 exact while T1 echoes), `spi_flash.s` at its 28-cycle SCK limit. On CI's CMOS5L netlist the last two pass with the same cycle timing as the RTL. They found a boot ROM bug: at handover the loader stopped the TX engine before releasing `uio[0]`, so the pin drove 0 for one cycle, a false start bit on the host's UART after every boot; fixed by swapping the two instructions. All five gate-level tests (`test_gl_smoke` 2, `test_gl_firmware` 3) pass on the netlists of CI 40085473871 and local run F (28 Oct); §3.4 proves the netlist equal to the RTL, which is the stronger evidence |
| 3.1 timing checker | done | `tools/msf_timing.py`: every firmware deadline certified (worst slack 9 cycles, `usb_ls_tx.s`), cross-checked against the model, rejects a too-fast UART; in the `test` workflow. Widening thresholds made 16 apart near 0 (24 Oct): with powers of two it could not certify `can.s` at any bit time |
| 3.2 LATE flag | done | Decided (decision 3): per thread, STAT bit 14, port 7 read/clear, cleared by TSTART; late = 0-255 cycles behind; 2 cycles, Z=1. isa.md v2.2, RTL, model, assembler, random programs, directed tests, F10/F11, mutant B4. Formal: F10 and F11 pass the depth-16 BMC in CI (40085473891); F11 and F10's release bounds are proved for all time, F10's first assertion only to depth 16 (§3.3) |
| 3.3 unbounded proofs | in progress | `run_formal.py --prove [--prove-timeout S] [--top ctrl\|top\|cap]`, ABC `pdr`, with a per-assertion report from SymbiYosys' status database. Controller: 31 of 35 assertions proved for all time (30 in under 7 min, F8's count bound in 3.4 h); after 5 h F3's three timeout bounds and F10's first assertion are still open (they relate the 17-bit ghost stall counter to the 16-bit timeout; need a strengthening invariant). Helper invariants per thread (stall kind, lateness, target, timeout counter + ghost count = TMO, overrun within F12's gap) did not close them: PDR proved 61 of 87 in 1 h, neither the four nor most helpers; k-induction (bitwuzla, depth 4) fails its step even on F3's event release. Left out; the four stay bounded (depth 16). Top level (`--top top`, F7, F9 and the controller in the whole design): running locally, 4 h limit (28 Oct). `verification.md` §3 |
| 3.4 netlist equals RTL | done | **Proved.** `formal/netlist_equiv.py`: one miter of RTL and routed netlist (cells from the liberty, both SRAMs cut out as black boxes), started in the reset state (Yosys `sim -w`), ABC `dprove`: equivalent in 8 min 43 s on CI 40085473871's netlist. From an all-zero start it had failed: the netlist's only flop resets to 0, so reset-to-1 bits are stored inverted and disagree before a reset. Bugs put into the netlist: an AND2 made an OR2 found (29 min), two SRAM address bits swapped found (1.5 min), a NAND2 in the SERDES receiver made a NOR2 left undecided (43 min; only a received byte, thousands of cycles in, shows it, as a gate-level UART boot does) — undecided exits 2, so CI fails safe. CI job `equiv` after `gds`. `verification.md` §7c |
| 3.5 AI write-up | done | `docs/ai.md`: who did what (checked by Sam, 28 Oct: v1 was Sam's, done at home with an AI coding agent), the independent check for each kind of AI work, fifteen cases where the AI was wrong and what caught them |
| 3.6 lower priority | power done | Power per workload (`tools/power.py`, `verification.md` §7b): 1.77 mW sending UART, 2.14 mW two threads, 1.89 mW a `JMP` loop, at 12.5 MHz, typical corner, from the routed netlist and gate-level activity. Execution digest (28 Oct): every C board test prints a CRC-32 of each core cycle's pins as the PS saw them (`msf_digest()`, `msf_link.c`), the same at any PS speed, so a board run compares with the simulation in one number per test; on silicon it would need the same C on the demo board's RP2040. Results page not started (GitHub Pages would need the Actions that billing blocks, decision 7) |
| 4 JTAG, SWD, PS/2, CAN, 1-Wire | done | `jtag.s`: UART-driven IEEE 1149.1 master (reset, IR/DR scans 1-255 bits), tested against modelled TAPs, single and chained. `swd.s`: ADIv5 SWD host (line reset, JTAG-to-SWD, DP/AP transactions with parity and ACK handling), tested against a modelled SW-DP + MEM-AP (DPIDR, IDR, memory read/write, WAIT, no target). Both in `test_debugports.py`. `ps2_device.s`: keyboard emulation (answers host commands, reports them, inhibit and resend), `test_buses.py` against a modelled PS/2 host. `onewire.s`: 1-Wire master (reset/presence, byte read/write, SEARCH ROM), against modelled devices (READ/MATCH/SKIP/SEARCH ROM, scratchpad with CRC-8); 8 deadlines certified. `can.s`: CAN 2.0A node (send with arbitration and retries, receive, ACK, CRC-15, stuffing), against modelled nodes; certified at 480 cycles a bit (50 kbit/s at 24 MHz). C versions of the JTAG, SWD and 1-Wire tests for the board are in `msf_tests.c` (§2.3) |
| 4 next tier | done, but one not feasible | Device emulation: `i2c_target.s` plays a 128-byte I2C EEPROM and `spi_target.s` a 64-register SPI device; `spi_flash.s` (24 Oct) a 25xx SPI flash: JEDEC ID, RDSR, WREN/WRDI, READ/FAST_READ with a 3-byte address, page program, the erases with WIP polled during them; 512 bytes; SCK period >= 28 cycles, measured. 10BASE-T receive is not feasible at the clocks we sign off: the Manchester receiver needs about 4x oversampling of the 10 MHz half-bits, ~80 MHz (`history.md` §6) |
| 5 write-up | in progress | README rewritten as the landing page (core, firmware by role, the verification ladder, honest limits); `research.md` 23 Oct survey added; `ai.md` updated. The signed-off clock (33 MHz in every corner, 50 MHz typical) and the netlist-equals-RTL proof are in README and `info.md` (24 Oct). To do: photos and video of the board (Sam, Muath) |

**Left to finish (6 Nov):**

1. Vivado's timing and utilisation from the existing build (Muath).
2. Photos and a short video of the board, plus the `67 of 67` screenshot (Sam, Muath).
3. One GDS run on the final `main`, so the commit submitted has its own clean run.
4. The submission, when Jane Street publishes the form (deadline 18 Jan 2027).

Extras that nothing depends on: `swd_mcu` against the LP-MSPM0G3519; CAN against
it, which needs a new peer and a shared bus; the top-level formal proof.

## 0. Where we are on 23 Oct

| Area | State |
|---|---|
| RTL | four hardware threads, two SRAM banks (1,024 words, 896 for firmware), hardware FIFO, capture engine, Manchester receive, glitch filter, debug view, per-thread timeout |
| Simulation | 158 cocotb tests pass (at `66dc56d`) |
| FPGA board | **14 of 14 C protocol tests pass on the AUP-ZU3**, and the LED blink works (23 Oct). The board runs the same core as `src/`: in `fpga/rtl` only the SRAM wrapper and the boot ROM really differ (the ALU, CRC, PIO and boot ROM data differ only in line endings). The Zynq PS steps the core one clock at a time, so this checks logic and protocol behaviour on real hardware, not timing at speed |
| GDS | green, but on the wrong process (§1.1), and the slow corner fails timing (§1.2) |
| Formal | red since `66dc56d`: the tool runs out of memory; no property fails (§1.3) |
| Mutation | 38/40 at `fd74b43` (the two survivors are equivalent); not re-run since the capture engine, Manchester receive, filter and four threads went in |

The 14 board tests: `crc_check_values`, `boot_parallel_go`,
`boot_bad_crc_then_retry`, `restart_direct_boot`, `uart_tx_drift_free`,
`spi_all_modes_full_duplex`, `i2c_write_ack`, `i2c_address_nack`,
`i2c_write_then_read_repeated_start`, `i2c_constrained_random_with_stretching`,
`usb_ls_tx_packet`, `usb_ls_rx_packet`, `eth_10baset_frame_fcs_tpidl_nlp`,
`threads_blink_and_echo`.

## 1. Blockers: no tape-out without these

### 1.1 Harden for CMOS5L, not SG13G2

**Problem.** Jane Street targets the March 2027 IHP **CMOS5L** shuttle. Tiny
Tapeout's template moved to a `cmos5l` branch on 24 Jun 2026. We still
harden with `tt-gds-action@ttihp26b` and `pdk: ihp-sg13g2`, so every green
GDS so far is for SG13G2. All five strong rivals checked on 23 Oct
(Abagel-coder, MarcosAsh, Escapement, mrnathanhumphrey, Polyglot) already
build for CMOS5L.

**Evidence.** The template's `main...cmos5l` diff changes: the workflows
(`@ihp-cmos5l`, `pdk: ihp-sg13cmos5l`), `FP_PDN_VPITCH` 38.87 → 50.0 plus
`FP_PDN_VWIDTH` 2.1, and the gate-level simulation sources in `test/Makefile`.
The PDK's `ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram` is a link to the
`sg13g2_sram` folder, so our `RM_IHPSG13_2P_512x16_c2_bm_bist` exists for
CMOS5L.

**Approach.**
1. Workflows: `gds.yaml` (gds, precheck, gl_test, viewer), `docs.yaml` and
   `fpga.yaml` to `@ihp-cmos5l`; `pdk: ihp-sg13cmos5l`. Also check
   `.devcontainer/Dockerfile`.
2. `test/Makefile`: gate-level sources → `sg13cmos5l_io.v`,
   `sg13cmos5l_udp.v`, `sg13cmos5l_stdcell.v`.
3. `src/config.json`: `FP_PDN_VPITCH` 50.0, `FP_PDN_VWIDTH` 2.1. Point the
   macro files at `pdk_dir::libs.ref/sg13cmos5l_sram/...`, or keep our copy
   in `macro/` after checking that it is byte-identical to the PDK's.
4. `src/pdn_cfg.tcl`: check its layers (Metal1, Metal4, TopMetal1,
   TopMetal2) against the CMOS5L stack. Re-place `sram0` and `sram1` for the
   50 µm stripe pitch (the lesson of `54c0e3f`: a macro whose power pins
   aren't near a stripe fails the precheck pin check).
5. Tools that name SG13G2 cells or paths: `tools/synth_check.py`,
   `src/RM_IHPSG13_2P_512x16_c2_bm_bist_sim.v`, and the mutation tool's
   gate-level path if it has one.
6. Run the GDS and add the row to `verification.md` §7 next to the SG13G2
   numbers.

**Fallback.** If the two macros won't place cleanly, the PDK also has
`RM_IHPSG13_2P_1024x16_c2_bm_bist`: one macro holding the same 1,024 words.
Abagel-coder's and MarcosAsh's public configs show SRAM macros hardened on
CMOS5L in 6x4 tiles.

**Done when** gds, precheck and gl_test are green on `ihp-cmos5l`.

### 1.2 Close timing at the slow corner

**Problem.** The four-thread GDS (`66dc56d`) misses 50 MHz at the slow
corner, but the GDS action still reports success, so nobody noticed.

**Evidence** (run 39676274307, SG13G2, 20 ns):

| Corner | Setup slack |
|---|---|
| slow (1.08 V, 125 °C) | **−3.35 ns**, 406 failing endpoints |
| typical | +3.86 ns |
| fast | +8.12 ns |

Hold slack is +0.11 ns. 16,030 cells, 44.8 % utilisation; 32 max-slew, 17
max-cap and 114 max-fanout warnings. The worst path runs from `sram0`
`A_DOUT` (3.86 ns clock-to-output) through decode and the ALU into
`u_ctrl.u_rf2.rf[2][13]`, and every one of the worst endpoints is a
register-file flop. The cells on the path are the weakest size (`_1`), with
1–2 ns slews.

**Cause.** The resizer only loads the typical corner (`RSZ_CORNERS` is
unset), reports "No setup violations found" and never upsizes anything for
the slow corner. `RUN_POST_GRT_RESIZER_TIMING` is false, `PL_TIMING_DRIVEN`
is false and `SYNTH_STRATEGY` is `AREA 0`.

**Approach.** Cheapest first, one GDS run each, all on CMOS5L (after
§1.1, since the corners change anyway):
1. Add the slow corner to `RSZ_CORNERS`, and set
   `RUN_POST_GRT_RESIZER_TIMING` and `PL_TIMING_DRIVEN` to true.
2. Change `SYNTH_STRATEGY` to `DELAY 1` … `DELAY 4`. On its CMOS5L SRAM
   fetch loop, Abagel-coder measured −0.74 ns with `AREA 0` and +1.37 ns
   with `DELAY 4`.
3. RTL, only if 1 and 2 aren't enough: take work off the path from SRAM
   read to register write. For example, decode the destination and thread
   select from fewer bits, or register the result before the register file.
   The register option needs forwarding in single-thread mode, which issues
   every cycle, so it is a real pipeline change with formal and mutation
   re-runs.
4. Sign off at a lower clock: 48 MHz (USB low speed at exactly 32 cycles a
   bit) or 40 MHz. No firmware we ship needs more than 20 MHz: USB low
   speed runs at 12 and 10BASE-T at 20.

Also add a CI step that fails when the slow-corner setup slack is below 0
or the max-slew/max-cap counts are above 0 (`tools/gds_metrics.py` already
reads them), so this can't slip past a green badge again.

**Done when** the slow-corner setup slack is ≥ 0, with no slew or cap
violations, at the clock we sign off on CMOS5L, and `config.json`,
`info.yaml` and the docs all state that clock.

### 1.3 Get formal green again

**Problem.** The formal workflow has failed on every push since `66dc56d`.

**Evidence.** BMC at depth 16 of `tt_um_msf_ctrl` and `tt_um_msf` builds
4.9 M variables and 13.6 M clauses. YoWASP Yosys (32-bit WASM) runs out of
memory after about an hour. Cover reaches 9/9 and mutants B1–B3 are still
caught, so no property fails. It is purely capacity.

**Approach.**
1. CI: native Yosys from OSS CAD Suite (the YosysHQ setup action) instead of
   `pip install yowasp-yosys`; run `run_formal.py --yosys yosys`.
2. Move the BMC to SymbiYosys (`smtbmc` with yices or boolector, or `abc
   bmc3`), which is much faster than `sat` at this size. Keep `sat` as the
   fallback path in `run_formal.py`.
3. If it is still slow: depth 16 for the controller and 12 for the top, or
   per-thread runs with the other threads' run bits fixed.
4. Locally: OSS CAD Suite has Windows builds; YoWASP can't handle this size
   any more.
5. Add properties for what v2.1 added: every running thread gets a slot
   within *n* cycles (no starvation, the next-owner rule), the FIFO pointer
   and count invariants, and that capture writes only when the core is idle.
   Each needs a mutant that must fail, like B1–B3.

**Done when** the formal workflow is green in under 30 minutes, with the new
properties and their mutants.

### 1.4 Decide what gets taped out

`main` is still `a7a9b86` (17 Oct). `steal` and `rtl-changes` were never
merged, and the standing rule is never to merge `rtl-changes`. `fixes`
carries all of it, so it is the natural tape-out candidate. Sam decides:
* whether `fixes` is merged into `main` once §1.1–§1.3 are green, or
  submitted from the branch. Jane Street will add the submission form
  "closer to the deadline";
* an RTL freeze date. Proposed: **14 Dec**. After it, only firmware, tests
  and docs change, which leaves five weeks for the final GDS and the
  write-up.

**Decided (Sam, 28 Oct):** `fixes` is merged into `main`: done the same day
(a fast-forward to `2592db0`), as an exception before CI could run, so that
Muath can harden `main` locally. There is no RTL freeze: `main` is the working version until the
deadline and new ideas go on branches; an RTL change that reaches `main` is
signed off again (GDS, `check_signoff.py`, `netlist_equiv.py`).

## 2. Record and widen the evidence

### 2.1 Record the board pass
* `docs/fpga.md` ("Not yet done …") and `verification.md` §5 and §9
  ("Nothing has run on silicon or on the FPGA board") are out of date. Add
  the 14 tests, the date, the commit built, the Vivado version, the PL
  timing and utilisation, the screenshot and a photo of the board.
* State the limit honestly: the PS steps the core, so the board run checks
  logic and protocols, not at-speed timing (see §2.3).
* Needed from Muath: the commit that was built, Vivado WNS and utilisation,
  and the screenshot.

### 2.2 Stale metadata
* `info.yaml`: the description says 2 threads; `clock_hz` is 12 MHz; check
  that the pin notes match the debug pages (`ui_in[2]` at reset,
  `ui_in[1]`).
* README and `info.md`: thread count, memory sizes and clock.
* `test/dbg_i2c.py` (an untracked throwaway tracer): deleted, 28 Oct (§6).

### 2.3 Widen the board tests
The C tests cover the v2 set. Not yet on the board: SPI and I2C targets,
the three monitors, the bridge, the fault knobs, WS2812, the capture engine
and logic analyser, the FIFO, Manchester receive, the glitch filter, four
threads with `TSTART #t`, the debug view and per-thread timeouts.
1. Port their Python peer models to C and add them to `fpga/ps/hil`, then
   run them stepped, as now.
2. Free-running: clock the core from a PL clock (not stepped) and talk to
   real parts on PMOD JA, e.g. an I2C EEPROM, an SPI flash, UART to a PC, a
   WS2812 strip, and the logic analyser on a live bus. This is the at-speed
   evidence. It needs Vivado timing for the core on the ZU3 at that clock.
   Muath owns the block design and wrapper. **Decided (28 Oct): no parts
   are bought**, so this is SWD against Sam's TI microcontroller and what
   needs no extra hardware; every other bus stays verified against the C
   models, in simulation and stepped on the board. First run (5 Nov): an
   LP-MSPM0G3519 as UART, I2C and SPI controller, in
   `external-mcu_testing.md`; all three pass since 6 Nov. Vivado's timing at
   that clock is still to record.

### 2.4 Keep `fpga/rtl` in step with `src`
`fpga/rtl` is a copy. Only the SRAM wrapper and the boot ROM differ for
real; the ALU, CRC, PIO and boot ROM data differ only in line endings. Either point `sources.f` at
`../../src` for the shared files, or add a CI check that they are identical
(ignoring line endings). Add a `.gitattributes` rule for `*.v` line endings
to stop the drift.

### 2.5 Re-run mutation on the final RTL
Run `tools/mutation.py` on the frozen RTL and kill each surviving
non-equivalent mutant with a test. Record the score in `verification.md`
§8.

### 2.6 Gate-level simulation (optional)
`gl_test` runs only `test_gl_smoke`. §3.4 (netlist equals RTL) gives
stronger evidence for less run time; a few firmware tests on the netlist
are a cheap extra if time allows.

## 3. Ideas from the 23 Oct survey

Since 18 Oct about 20 new repositories have appeared. The old leaders
(PRISM, TeslaCoilerOW, TejasDasa) haven't pushed since. The strong new
entries share one theme we lack: **timing you can prove**. MarcosAsh's
assembler certifies each program's deadlines, with the checker proved
against the RTL. Escapement's hardware refuses an event it can't meet
rather than being late, proved for every program. mrnathanhumphrey bounds
worst-case response with a real-time analysis. These go into
`research.md` as a dated section (§5).

| # | Idea | From | Kind | Value |
|---|---|---|---|---|
| 3.1 | firmware timing checker | MarcosAsh, mrnathanhumphrey | tooling | high |
| 3.2 | LATE flag on `WAIT_CMP` | Escapement | small RTL | high |
| 3.3 | unbounded proofs | MarcosAsh, Escapement, mcranny | formal | high |
| 3.4 | hardened netlist equals RTL | MarcosAsh, Escapement | formal | high |
| 3.5 | how we used AI, written up | 2AMLogic | docs | high (the brief names it) |
| 3.6 | power per workload, execution digest, results page | Escapement, mcranny | various | low |

### 3.1 Firmware timing checker (tooling, no RTL)
Our cycle costs are already fixed and documented (`isa.md` §5 and §7), so a
checker can prove deadlines per program.
* Build the control-flow graph of a firmware image, with costs: 1 cycle; a
  taken branch, `JMP`, `CALL`, `RET`, `JR`, `LD` or `FIFO_GET` 2; in
  multi-thread mode one slot per instruction, *n* cycles a slot with *n*
  threads running (worst case 4, or as declared).
* For each `WAIT_CMP`, find the longest path from the previous release to
  its issue and report the slack against the target (it needs the target ≥
  issue + 2). Loops whose trip count isn't constant need an annotation such
  as `; @loop max 8`.
* Print a listing with the slack per deadline, and exit 1 on a deadline
  that may be missed.
* Soundness test: run every firmware in `isa_model.py` and assert that the
  measured slack is never below the computed slack.
* Run it over `firmware/*.s` in CI.

### 3.2 LATE flag (small RTL change, no new opcode)
Today a `WAIT_CMP` whose target has already passed waits for `CNT` to wrap:
65,536 cycles (1.3 ms at 50 MHz) of silent lateness. Proposed:
* At issue, if the target is less than 2 cycles ahead (mod 2^16, with a
  window decided in the spec), release at once and set a sticky **LATE**
  flag in STAT bit 14 (free today).
* Clearing: on a write to port 7 (free today), or per thread. Decide in
  `isa.md` first.
* Formal: while LATE is clear, every `WAIT_CMP` releases exactly at its
  target, plus a cover and a mutant.
* Monitors and fault tests can report LATE as a firmware overrun.

It goes through the full RTL routine: `isa.md` → `isa_model.py` →
assembler → `rand_prog.py` → formal → directed tests → `fpga/rtl` →
mutation → docs.

### 3.3 Unbounded proofs
Once §1.3 is on SymbiYosys, prove F1–F7 and the new properties for all time
with k-induction (`smtbmc`) or PDR (`abc pdr`). Induction will need
strengthening invariants, e.g. the thread-state encoding and the FIFO
pointers. Claim "for all time" only for the properties actually proved.

### 3.4 The hardened netlist equals the RTL
Take the final netlist from the GDS artifact (`runs/wokwi/final/nl/tt_um_msf.nl.v`)
and prove it equivalent to `src/`, using Yosys `equiv_*` or EQY with the
CMOS5L cell models and the SRAM macros as black boxes. Flop names survive
hardening (e.g. `u_ctrl.u_rf2.rf[2][13]`), so state can be matched by name.
Run it as a CI job after `gds`. Then the formal results apply to what is
fabricated.

### 3.5 How we used AI, written up
The brief lists "AI-assisted verification". We used Claude throughout and
say nothing about it. Add a section (`verification.md` §10 or
`docs/ai.md`) that covers:
* what AI wrote (firmware, tests, models, docs, tools);
* what checks it mechanically (independent model, random differential
  tests, formal with mutants, mutation testing, the board);
* the cases where AI output was wrong and a check caught it (from
  `history.md`);
* which decisions people made.

Keep it honest and specific. 2AMLogic's `docs/submission/ai-use.md` is the
one to beat.

### 3.6 Lower priority
* Power per workload: OpenROAD `report_power` with activity from a
  gate-level run (Escapement quotes 3.0 mW sending UART).
* Execution digest: a CRC of the PC or pin trace to compare silicon against
  the model in one read. Firmware plus the CRC unit could do it.
* Results page: GitHub Pages with the GDS metrics table, mutation score and
  latest CI results.

**Not taking:** 8x4 designs (not approved; the page still says 6x4),
EDF or dynamic scheduling (breaks determinism), latch memories (we have
SRAM), a RISC-V host.

## 4. Firmware the brief names that we don't have

**State.** The 21 Oct update made UART, SPI and I2C complete (controller,
target and monitor). JTAG, SWD, PS/2 and CAN exist only as v1 demos in
`test/test_extensions.py`: straight-line `PIN_W` sequences whose every level
Python computed. The CAN frame has no CRC-15, ACK slot or stuffing, JTAG
never reads TDO, and SWD stops after the ACK. 1-Wire has nothing. Every
strong rival has all five.

**Pattern for each**, the same as the targets and monitors:
`firmware/<name>.s`; an independent Python model as the other end; cocotb
tests; registration in `run_tests.py` and `test/Makefile`; a C port for the
board; a real part on the board; a section in `instruments.md`.

| Firmware | Scope | Hardware used | Model | Real part |
|---|---|---|---|---|
| `jtag_master.s` | TAP reset, IR and DR scans of any length, read IDCODE, count devices on a chain via BYPASS | pins, `WAIT_CMP` | TAP model with IDCODE, BYPASS and a boundary register | any JTAG device |
| `swd_master.s` | line reset and JTAG-to-SWD switch, read DPIDR, debug power-up, MEM-AP read and write, parity, WAIT and FAULT ACKs, turnaround | pins, TX/RX engines | SWD DP/AP model | the TI microcontroller we already have (SWD) |
| `ps2.s` | device: send scancodes, answer host commands (FF → FA AA, ED LEDs), host inhibit; host: read a keyboard. Parity computed on chip | open drain, `WAIT_EDGE` | PS/2 host and device models | PS/2 keyboard |
| `can.s` | CAN 2.0A TX and RX: arbitration (read back while sending), CRC-15 (CRC unit, `POLY` 0x4599 left-aligned), stuffing, ACK slot, error-frame detection | CRC unit | CAN node model, two-node arbitration | two transceivers (SN65HVD230) and a second node |
| `onewire.s` | reset and presence, ROM read and search, CRC-8 (reflected 0x8C), DS18B20 temperature read; target mode emulating a DS18B20 | open drain, `WAIT_EDGE` with timeout | 1-Wire device model | DS18B20 |

CAN stuffing: firmware first. At 500 kbit/s and 50 MHz a bit is 100 cycles
(25 slots with four threads), which is plenty. If it's ever needed, TX mode
3 (now a duplicate of NRZ) and RX mode 3 (unused) could become CAN
stuffing (a complement after five equal bits) at no encoding cost.

**Next tier**, after the five:
* Device emulation: a 24C02 EEPROM on `i2c_target.s` (address pointer, page
  write, ACK polling) and a 25xx SPI flash on `spi_target.s` (JEDEC ID,
  READ, RDSR, WREN, PP). The chip standing in for a real part is the core
  of a reverse-engineering tool.
* 10BASE-T receive: RX mode 2 (Manchester) plus a CRC-32 check, reporting
  the header and FCS result over UART while the frame streams (not stored).
  It needs a comparator as the line receiver. Only PRISM receives 10BASE-T.
* One or two cheap extras that show something new, e.g. NEC infrared
  receive using the glitch filter.

## 5. The write-up

There is no rubric beyond "the most novel designs", "unique functionality"
and "novel approaches to design and verification".
* README as the landing page: the pitch; what we do differently from PIO and
  PRU; the instrument story (emulate, monitor, inject faults, capture); the
  verification ladder (independent model → random differential tests →
  formal with mutants → mutation testing → board tests → netlist
  equivalence); honest limits.
* `research.md`: a dated 23 Oct section with the survey above.
* `verification.md` §9: one row per new claim, with its evidence.
* Photos and a short video of the board running the tests, plus
  logic-analyser captures.
* `info.md` (the Tiny Tapeout datasheet) matching the final pinout, clock
  and memory map.

## 6. Decisions

Decided by Sam on 28 Oct (each was asked and explained one at a time).

1. **Tape-out:** `fixes` is merged into `main`, and `main` is taped out.
   It is a one-off exception to "never merge `rtl-changes`", which `fixes`
   contains. Merged on 28 Oct without waiting for CI, so that Muath can run
   the hardening locally on `main` (§1.4).
2. **Clock:** 33 MHz, signed off at 30 ns in every corner. Typical silicon
   runs at about 50 MHz, and is described only as typical (§1.2).
3. **LATE flag:** as built: per thread, sticky until read and cleared (port
   7) or TSTART; late means 0–255 cycles behind; a late `WAIT_CMP`
   releases in 2 cycles with Z=1 (§3.2).
4. **Parts:** none are bought. SWD runs against Sam's TI microcontroller;
   every other bus stays verified against the C models (§2.3).
5. **No RTL freeze:** development goes on until the deadline. `main` is the
   working version and new ideas go on branches; an RTL change that
   reaches `main` is signed off again (§1.4).
6. `test/dbg_i2c.py`: deleted (§2.2).
7. **CI:** GitHub stopped starting jobs on 24 Oct (about 11:00): the
   repository is private and Muathew's Actions minutes ran out. Until the
   free minutes renew, the GDS flow, precheck, gate-level tests, sign-off
   and equivalence run locally (`verification.md` §7). From 28 Oct the
   GDS workflow runs only on demand (`gh workflow run gds.yaml --ref
   <branch>`, about 2.5 h); test, formal and docs (about 22 min together)
   still run on every push.
8. **AI write-up:** `ai.md` §1 checked: v1 was Sam's, done at home with an
   AI coding agent; the quoted question in §3 removed (§3.5).
