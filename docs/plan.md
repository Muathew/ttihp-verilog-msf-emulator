# Plan: branch `fixes` (23 Oct 2026 to 18 Jan 2027)

Branch `fixes` starts at `c68bd0c`, the head of `rtl-changes` and the build
that passed on the AUP-ZU3 board on 23 Oct. It covers what has to be fixed
before msfcore can be taped out, the evidence still to record, the ideas
worth taking from the 23 Oct look at the other entries, the firmware the
brief names that we still lack, and a schedule to the 18 Jan 2027 deadline.

Order of work: blockers (§1) first, because none of the rest counts if the
design can't go on the shuttle. Then evidence (§2), then new work (§3–§4),
then the write-up (§5).

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
* `test/dbg_i2c.py` is still untracked: move it to `tools/` or delete it.

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
   Muath owns the block design and wrapper.

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
| `swd_master.s` | line reset and JTAG-to-SWD switch, read DPIDR, debug power-up, MEM-AP read and write, parity, WAIT and FAULT ACKs, turnaround | pins, TX/RX engines | SWD DP/AP model | an STM32 board |
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

## 6. Schedule

Twelve weeks to 18 Jan 2027. Dates are suggestions.

| Weeks | Dates | Work |
|---|---|---|
| 1 | 23 Oct – 2 Nov | §1.1 CMOS5L port, §1.3 formal CI, §2.1–§2.2 records |
| 2 | 3 – 9 Nov | §1.2 timing on CMOS5L, §1.4 decision, §2.4 sync check |
| 3–5 | 10 – 30 Nov | §3.2 LATE flag, §3.1 timing checker, §4 JTAG, SWD, CAN, PS/2, 1-Wire |
| 6–7 | 1 – 14 Dec | §3.3 unbounded proofs, §3.4 netlist equivalence, §2.3 board tests with real parts, §2.5 mutation. **RTL freeze 14 Dec** |
| 8–10 | 15 Dec – 4 Jan | device emulation, 10BASE-T receive, §3.5 AI write-up, final GDS on the frozen RTL |
| 11–12 | 5 – 18 Jan | write-up, video, submission (aim for 11 Jan, a week early) |

Every RTL change (§3.2, any RTL timing fix, an optional CAN mode) lands
before the freeze.

## 7. Decisions needed

1. The tape-out branch, and whether `fixes` may be merged into `main`
   (§1.4).
2. The fallback clock if 50 MHz doesn't close: 48 or 40 MHz (§1.2).
3. LATE flag semantics: per thread or global, and how it clears (§3.2).
4. Real parts to buy for the board: DS18B20, PS/2 keyboard, two CAN
   transceivers and a second CAN node, an SPI flash, an I2C EEPROM, an
   STM32 board for SWD (§2.3, §4).
5. The RTL freeze date (§1.4).
6. `test/dbg_i2c.py`: keep or delete (§2.2).
