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

### A2. The account's artifact store was full (6.9 GB; cleaned to 713 MB on 12 Nov)
* **What:** CI's `docs` workflow (Tiny Tapeout's `tt-gds-action/docs`, which uploads the
  PDF) fails with `Failed to CreateArtifact: Artifact storage quota has been hit` whatever
  the repository holds. The `test` workflow's uploads now cannot fail the job
  (`v4-bugs.md` I6), but the `docs` action's upload is not ours to change.
* **Measured 12 Nov** (`gh api repos/Muathew/ttihp-verilog-msf-emulator/actions/artifacts`):
  324 live artifacts, 6,922 MB. By kind: `GDS_logs` about 4,650 MB (rtl-changes 1,497,
  FPGA 750, fixes 702, main 455, optional 426, steal 375, ethernet 196, plan7 179,
  sram-diversion 70); `test-results` about 1,730 MB (plan7 508, v3 427, fixes 278, mcu 225,
  main 161, rtl-changes 90, ...); `tt_submission` and `gds_render` the rest. v4 has none: its
  uploads failed.
* **Cleaned 12 Nov, on Sam's choice:** 308 artifacts (6,209 MB) deleted, none failed. Kept:
  the newest artifact of each kind on `main`, `plan7` and `v3`, 16 items, 713 MB: the
  newest hardening run of `main` (3 Nov) and of `plan7` (7 Nov) with its `GDS_logs`,
  `tt_submission`, `gds_render`, `precheck_reports`, `gatelevel_test_results`; the newest
  `test-results` of `main`, `plan7`, `v3`; the three PDFs. Deleted: every older run on those
  branches and every artifact of rtl-changes (63), FPGA (42), optional (40), fixes (38),
  steal (21), mcu (14), ethernet (14), sram-diversion (7). Code and CI text logs are not
  artifacts and are untouched.
* **Still open:** whether uploads work again. GitHub says it recalculates usage every 6 to
  12 hours; if the quota is counted over the billing month (GB-hours), uploads may stay
  blocked until Muath's month resets. 713 MB is under a Pro or Education plan's 2 GB but
  over GitHub Free's 500 MB. Muath can see which on his billing page; the `docs` workflow
  of the next push says whether it worked. Each hardening run adds about 230 MB, so the
  repository's artifact retention (Settings, Actions, General) is worth lowering to a week
  or two.

### A3. Long runs left for later (12 Nov: the session had an hour, Sam asked for no long runs)
* **Formal to depth 16:** done by CI instead, green at e92524b (`v4-bugs.md`, "State on
  12 Nov"), so a local rerun is not needed. Still not run anywhere on v4: the `ni` target
  (F16, `formal/run_formal.py --engine abc --depth 16 --top ni`, hours) and the unbounded
  proof of the controller (`--prove --prove-timeout 3600 --top ctrl --no-mutants`).
* **The full cocotb suite (319) and the pure-Python tests** after the 12 Nov edits: not run
  locally. What ran: the modules each edit touches (`test_nes_pad`, `test_mcu_v3_peer`, the
  new `test_site.py` tests). CI's `test` workflow on the push runs all of it, plus the
  deadline cross-check.

(more entries are added below as they come up)

## B. Not checked here (needs the other machine, CI, a board or a person)

* Hardening, timing sign-off, hold margins, the four-macro floorplan and its power grid, the
  precheck: no flow runs here. `src/config.json` has hold margins 0.3 / 0.25 ns (B9) and four
  macros at x = 42, 452.6, 863.2, 1273.8 um (8 um apart; plan section 7.2) that no run has
  confirmed. `src/pnr.sdc` and `src/signoff.sdc` have a false path from `ui_in*` to `uo_out*`
  (the debug-view select, B7) declared from the RTL, not from a timing report.
* Gate-level simulation with delays (SDF): needs a hardened netlist.
* Vivado build and board run of v4 (Muath); the repository is private (Muath makes it public);
  CI GDS runs need Sam's go-ahead (plan section 7.3); the bench session and video (section 11).
* `firmware/spi_flash.s` moved its store to SRAM 1536-2047 (`v4-bugs.md` F1). Two things that
  use it could not be run here: the gate-level `test/test_gl_firmware.py::test_gl_spi_flash`
  (needs CI's netlist; it now preloads at `FLASH` from `test_targets.py`), and the FPGA's C test
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

* Half-cycle sampling (plan 4f; it needs a model study first) and the fractional divider (4g).
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
