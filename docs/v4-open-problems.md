# v4: problems skipped, and things that could not be checked here

Written while implementing `docs/plan-v4.md` on branch `v4`. Sam's instruction (11 Nov
2026): when stuck on an error, skip it, keep going, and report it at the end for a larger
model to review. This file is that report, kept current as work goes on. Each entry says
what happened, how to reproduce it, what I tried, and what I think. "Not checked" entries
are not errors: they are claims this machine cannot test.

## A. Errors skipped

### A1. Formal verification could not run on this machine (resolved 12 Nov)
* **Resolved 12 Nov:** a native OSS CAD Suite (build 2026-10-01, the version CI uses) is
  installed at `C:\Year3_projects\oss-cad-suite`, and `--engine abc` runs with it. Run
  `bin\environment.bat` first, or put its `bin` on `PATH` and set
  `SSL_CERT_FILE=etc\cacert.pem` and `PYTHON_EXECUTABLE=lib\python3.exe`; then
  `.venv\Scripts\python.exe formal/run_formal.py --engine abc --depth 16 --top ctrl --no-mutants --no-cover`
  (then `--top top`, then the full run with covers and mutants). The controller's BMC to
  depth 16 takes about 27 minutes here; the full run with mutants takes hours, so it is
  left to CI or a quiet evening. The YoWASP failure below still stands but no longer matters.
  What has run locally is in `v4-bugs.md`, "State on 12 Nov".
* **What (11 Nov):** neither engine of `formal/run_formal.py` ran here.
  * `--engine sat` (YoWASP's Yosys 0.70): the `sat` pass aborts with
    `wasm trap ... yosys.wasm!ezSAT::vec_or ... Yosys::SatGen::importCell`, an assertion
    inside Yosys that two vectors have the same width. It does the same on the untouched
    `v3` worktree (`C:\Year3_projects\msf-v3`), so it is not caused by v4's edits.
  * `--engine abc` (SymbiYosys): `engine_0: 'yosys-abc' is not recognized`. YoWASP ships
    `sby` but not a `yosys-abc` executable, and no native Yosys/ABC/SMT solver is installed.
* **Reproduce:** from the repo root,
  `.venv/Scripts/python.exe formal/run_formal.py --engine sat --yosys .venv/Scripts/yowasp-yosys.exe --top ctrl --depth 4 --no-mutants --no-cover`.
* **Consequence:** nothing formal has been run on v4. That covers F1-F15 on the changed RTL,
  the new F17 (timed pins) and F18 (registered pads equal the old combinational pads), the
  edited F5 and F11, and the mutants. The ISA-model/RTL differential tests, the directed tests
  and the full simulation suite are what v4 has been checked with.
* **What was checked instead:** `formal/parse_check.py` (new) runs Yosys `read_verilog -formal`
  and `prep` on the four targets with no solver. All four elaborate (`ctrl` 64 assertion cells,
  `top` 67, `ni` 73, `cap`), so the F17/F18 code is valid Verilog with consistent widths. That
  says nothing about whether the properties hold.
* **What to do:** run `formal/run_formal.py` where a native OSS CAD Suite exists (the
  `formal.yaml` workflow does, on push). Watch first for F17 (new) and F18; a counterexample
  there is more likely a wrong property than wrong RTL, since the RTL passes the model
  comparison. If a property is too strong, the F17 ghost is in `tt_um_msf_ctrl.v` under
  `// F17 (v4)`.

### A2. The artifact store was full (cleared 12 and 15 Nov), then no CI at all (resolved 18 Nov)
* **What:** CI's uploads failed with `Failed to CreateArtifact: Artifact storage quota has
  been hit`, so the `docs` workflow and the GDS job were marked failed after the flow itself
  had finished (the 15 Nov GDS run matched the local hardening: `sram-power-integration.md`
  §8.2). The `test` workflow's uploads cannot fail the job (`v4-bugs.md` I6).
* **Cleared:** 308 old artifacts (6.2 GB) on 12 Nov, the remaining 16 on 15 Nov.
* **Resolved 18 Nov:** from 17 Nov GitHub started no job on the repository (an account limit
  on Actions). The repository went public on 18 Nov, where Actions are free, and CI has run
  since: `docs`, `test` and `formal` pass on `main` at fa8bcfc, and GDS runs upload their
  logs again (CI 44379389831). Still worth doing: each hardening run adds about 230 MB of
  artifacts, so set the artifact retention (Settings, Actions, General) to a week or two.

### A3. Long runs left for later (12 Nov: the session had an hour, Sam asked for no long runs)
* **Formal to depth 16:** done by CI instead, green at e92524b (`v4-bugs.md`, "State on
  12 Nov"), so a local rerun is not needed. **The unbounded proof of the controller ran on
  18 Nov** (CI 44363125574, `--prove --prove-timeout 3600 --top ctrl --no-mutants`): 68 of
  86 assertions proved for all time; F17's timed pins (12), F3's timeouts (5) and F8 (1) are
  open, with no counterexample, when the hour runs out (the job is then marked failed). They
  need helper invariants. **23 Nov: F17 is proved for all time**, on every push:
  `run_formal.py --block F17` keeps only F17's block, six helper invariants per thread
  included, and proves its 41 assertions by k-induction to depth 4 in under a minute; a BMC
  in the same flow finds each of its three planted bugs (`verification.md` §3). Open: F3's
  timeouts (5) and F8 (1). Still not run anywhere on v4: the unmutated `ni` target (F16,
  `formal/run_formal.py --engine abc --depth 16 --top ni`, hours).
* **The full cocotb suite (319) and the pure-Python tests** after the 12 Nov edits: not run
  locally. What ran: the modules each edit touches (`test_nes_pad`, `test_mcu_v3_peer`, the
  new `test_site.py` tests). CI's `test` workflow on the push runs all of it, plus the
  deadline cross-check. **18 Nov:** it passes on `main` at fa8bcfc and on `rom-areg`.

(more entries are added below as they come up)

## B. Not checked here (needs the other machine, CI, a board or a person)

* **Update 19 Nov: 50 MHz.** Two CI runs at sign-off 20 ns, place and route 18 ns, on 18 Nov.
  CI 44351742340 missed at the slow corner by 1.40 ns on 16 paths through the boot ROM's
  lookup; the ROM now registers its address (784a270). Its run, CI 44379389831, missed by
  0.02 ns on one path (`u_ctrl.cur` through `per_we` to serdes 1's receive register, 16 of
  whose bits are within 0.6 ns); typical +7.35, fast +11.74, hold +0.31, LVS and antenna
  clean. **Cause:** every estimate before extraction used the tech LEF's 0.092 fF/um, and
  extraction gives 0.18 (11,423 routed nets: slope 0.179 fF/um). Post-CTS repair therefore
  saw no setup violation at 18 ns, added 5,505 hold buffers (1.33 ns of them on that path)
  and no setup buffers; the routed path came to 20.02 ns. `PL_TIMING_DRIVEN` and
  `RUN_POST_GRT_RESIZER_TIMING` were also still off (`plan.md` 1.2 step 1 asked for them).
  dfa4c86 (`rom-areg`) sets `LAYERS_RC` to 1.95 x the LEF capacitance and turns both on;
  CI 44499734618 runs it, and CI 44499740319 runs the calibration alone (branch
  `rom-areg-rc`). **Both passed** (finished 12:30 UTC). With all three settings: setup slack
  slow +3.36 ns, typical +9.69, fast +12.84; hold +0.26 ns at the fast corner; routing DRC,
  antenna and LVS clean; precheck, `gl_test`, `check_signoff.py` and `netlist_equiv.py` pass.
  The calibration alone: slow +1.45 ns, hold +0.25. `rom-areg` (with `clk50`) is merged into
  `v4` and `main`, and `rom-areg-rc` is deleted. **50 MHz is closed.** Magic's DRC reports
  416,536 errors in every run since 18 Nov (188,488 on 7 Nov): a warning in this flow, whose
  DRC gate is the KLayout precheck, which passes.

* **Update 20 Nov: fine mode hardened.** CI 44698976647 (970d4b8, branch `halfcycle`, plan
  5f) signs off at 20 ns in every corner: setup slack slow +2.27 ns, typical +3.89, fast +4.58;
  hold +0.29 ns; routing DRC, antenna and LVS clean; precheck, `gl_test`, `check_signoff.py` and
  `netlist_equiv.py` pass. The typical and fast corners lost 6-8 ns of slack because their worst
  path is now a pad into the falling-edge flop, which the constraints give half a cycle less the
  pad's 4 ns; at the slow corner that path has +2.73 ns. One max-slew warning: `B_ADDR[8]` of one
  SRAM at the slow corner, 0.596 ns against 0.595 ns (the previous run had none; the macro's
  timing is extrapolated 0.1 % beyond its table). The `formal` workflow failed on the branch:
  SymbiYosys's single-clock model refuses a falling-edge flop, so under `ifdef FORMAL` the
  sample is a free input (sound: nothing in a rising-edge model fixes a pin between two rising
  edges). Merged into `v4` and `main`.

* **Update 19 Nov: the MCU build's stream check.** In the 17 Nov MCU console log
  (`v3_board_mcu_console.log.txt`, from line 757; `*.log.txt` is not committed) every USB
  pass reads `stream 3647/3456 cycles -> PASS`: 3,647 cycles measured for 108 words of 32.
  The repository's `gapless()` (`mcu_ext/protocol_tests/v3_test.c` line 238) allows
  3,456 + 1 % + 4 words = 3,618 and would print NOT VALID. So the build that ran had a wider
  `gapless()` than the repository and `origin/v4`. Muath: commit the `gapless()` that ran,
  with the reason for the slack (the extra 191 cycles are about 6 words), or rerun with the
  repository's, so the 304 of 304 in the README is reproducible.

* **Update 18 Nov:** v4 ran on the AUP-ZU3 on 17 Nov at 12 MHz (bitstream built 03:13;
  the HIL digest `boot_parallel_go 6F06A9E1 over 5366 steps` is v4's). The v3 board test
  against the LP-MSPM0G3519 passed 304 of 304. The HIL suite passed 60 of 67; the seven
  failures are the `cap_*` tests reading `CNT` while v4's two-flop reset release still
  holds the core, a harness fault fixed in `8ffa5a2`, waiting for a rebuild of the `hil`
  app. (CI was stopped by an account limit from 17 Nov until the repository went public on
  18 Nov: A2.)

* **Update 15 Nov:** the four-macro floorplan and its power grid have now been hardened
  locally (WSL). The 13-14 Nov run shorted VPWR to VGND. Cells were stranded under the
  SRAMs, and their wires crossed the SRAMs' Metal4 supply columns. The SRAMs now sit on the
  core's bottom edge (y = 3.78), with a Metal3/Metal4 router keep-out over them. See
  [sram-power-integration.md](sram-power-integration.md). Later that day the TT precheck
  passed locally (0 KLayout DRC items), and a CI GDS run gave the same results as the local
  one. The CI job was then marked failed by the artifact quota (A2), so the gate-level tests
  were run locally instead: 5 of 5 pass, `test_gl_spi_flash` included (section 8.3 of that
  document).

* ~~Hardening, timing sign-off, hold margins, the four-macro floorplan and its power grid, the
  precheck: no flow runs here.~~ **Confirmed by runs since:** the four macros at x = 42,
  452.6, 863.2, 1273.8 um (15 Nov, above); the hold margin (+0.31 ns at the fast corner,
  CI 44379389831, from the post-CTS margin of 0.3; the 0.25 after global routing was unused
  until dfa4c86 turned that repair on; +0.26 ns at 50 MHz); the TT precheck (passed locally,
  15 Nov); 50 MHz, with CI's precheck, `gl_test` and `viewer` jobs passing for the first time
  on v4 (19 Nov, above). **Still open:** the false path from `ui_in*` to
  `uo_out*` in `src/pnr.sdc` and `src/signoff.sdc` (the debug-view select, B7) is still
  declared from the RTL, not checked against a timing report.
* Gate-level simulation with delays (SDF): **run 19 Nov** (`test/run_gl_sdf.py`, locally, on CI
  44499734618's netlist and SDF at 20 ns): 5 of 5 at the slow and fast corners and with random
  SRAM contents (`verification.md` §7). Still open: Icarus runs no setup or hold checks, so a
  violation shows only if it changes what the pins do. **20 Nov:** `gl_sdf.yaml` runs the same
  three in CI, on demand, on any gds run's netlist and SDF (the annotation alone takes about 25
  minutes a corner). On the fine-mode layout (CI 44698976647): 5 of 5 at the slow and fast
  corners and with random SRAM contents (CI 44821883241). Its first run stopped on two SDF
  errors, wire delays from `ui_in[5]` to two antenna diodes that Icarus could not place; the
  runner now leaves out wire delays to diodes, which have no logic.
* A board run above 12 MHz (Muath); the repository is public (18 Nov); CI GDS runs need
  Sam's go-ahead (plan section 7.3); the bench session and video (section 11).
* `firmware/spi_flash.s` moved its store to SRAM 1536-2047 (`v4-bugs.md` F1). Two things use
  it. The gate-level `test/test_gl_firmware.py::test_gl_spi_flash` (it now preloads at
  `FLASH` from `test_targets.py`) **passed on 15 Nov** on the 8x4 netlist. Not run here: the FPGA's C test
  (`fpga/ps/hil/msf_tests.c` line 811, which loads the store at SRAM 512). On the v3 FPGA's
  1,024-word memory 1536 aliases 512, so the C test passes there. **On a v4 bitstream it
  will fail:** `fpga/rtl`'s SRAM is now 2,048 words, so the load in `msf_tests.c` (lines
  811-823) must move to 1536 first. **Changed 13 Nov:** `msf_tests.c` loads at 1536, and
  `msf_boot_write` (`fpga/ps/common/msf_link.c`) no longer refuses addresses above 1023 (it
  checked a hard-coded 1024; now `MSF_SRAM_WORDS`, default 2048, in `msf_link.h`, so a v3
  bitstream builds with `-DMSF_SRAM_WORDS=1024U`). Without that second edit the test would
  have failed with `MSF_ERR_ARG` before reaching the chip. Run in simulation on `fpga/rtl`
  (`python fpga/tests/run_ps_hil.py -t spi_flash`, 113 s): PASS, digest 2a95671e. Not run on
  the board. Not touched: `fpga/ps/loader/src/main.c` line 13 still checks 1,024 words (a
  firmware image above that is not loaded by it; no shipped image is). Prerequisites for the
  v4 bitstream otherwise as in the message to Muath (paste v4's boot ROM into
  `fpga/rtl/tt_um_msf_bootrom.v`; keep `ui_in[3]` low at boot).
* The CI workflows first ran on v4 overnight 11-12 Nov; all three failed, for the reasons in
  `v4-bugs.md` V1, I5 and I6. The fixes were pushed on 12 Nov.

## C. Planned and not built (not errors: left out on purpose, in the plan's cut order)

* The fractional divider (plan 4g). ~~Half-cycle sampling (4f)~~: built after its model study
  and hardened, 20 Nov (B above).
* The boot ROM's read-back and memory test (plan step 5; no room in 128 words, the plan's
  fallback). The capability word says so: `0x0BBF`, its memory-test bit is clear.
* The demo-board driver and the bring-up kit (plan section 10), the envelope sweep script (the
  table in `docs/envelope.md` is made by hand from the tests), the second public draw and the
  newcomers' protocols (plan section 9), `msfc` threads and functions, the cold read.
* ~~The deadline checker's cross-check against the RTL for `AT` writes.~~ **Built 12 Nov:**
  `test/tb.v` `at_evt`, `DeadlineWatch._watch_timed` in `test/fw_tb.py` (floor d >= 0),
  `msf_timing.uses_deadlines()` (programs with `AT` are analysed by `--all` and cross-checked
  by `--rtl-log`), and the first shipped program with timed writes, `firmware/ow_presence.s`
  (a 1-Wire presence responder) with `test/test_ow_presence.py`: 4 of 4 deadlines reached on
  the RTL at the checker's exact values. Still not covered: timed writes with several
  threads in a shipped program (`test_timed_pins.py` covers them with inline programs).
* ~~The second reader of `src/tt_um_msf_serdes.v`~~ **Done 13 Nov** by a session that did not
  write it (`v4-bugs.md`, "The second reading of the engines"): no contradiction with
  `isa.md` 6.1, 6.2, 6.9 in anything a shipped program does; four gaps in the text (E1-E4),
  none changed. By reading only; the engines still have no formal properties.
* F16 for v4: the `ni` miter now carries the new per-thread state (the slot, `ESEL`, `TS`)
  and the second engine set's inputs (12 Nov, `v4-bugs.md` V3), and its planted bug is shown
  (13 Nov: the STAT-LATE mutant is caught at step 8, 11 minutes, `--top ni --only-mutant 0`), but F16 is still
  a bounded check, and the unmutated `ni` run to depth 16 has not been done.
