# v4: problems skipped, and things that could not be checked here

Written while implementing `docs/plan-v4.md` on branch `v4`. Sam's instruction (11 Nov
2026): when stuck on an error, skip it, keep going, and report it at the end for a larger
model to review. This file is that report, kept current as work goes on. Each entry says
what happened, how to reproduce it, what I tried, and what I think. "Not checked" entries
are not errors: they are claims this machine cannot test.

## A. Errors skipped

### A1. Formal verification cannot run on this machine
* **What:** neither engine of `formal/run_formal.py` runs here.
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
  1,024-word memory 1536 should alias 512, so the C test should still pass; not run.
* The CI workflows (`test.yaml`, `formal.yaml`) have not run on v4 at the time of writing; they
  are the first run of the formal properties, of the Makefile's module list and of
  `msf_timing.py --all --check-model 20000` on a clean machine.

## C. Planned and not built (not errors: left out on purpose, in the plan's cut order)

* Half-cycle sampling (plan 4f; it needs a model study first) and the fractional divider (4g).
* The boot ROM's read-back and memory test (plan step 5; no room in 128 words, the plan's
  fallback). The capability word says so: `0x0BBF`, its memory-test bit is clear.
* The demo-board driver and the bring-up kit (plan section 10), the envelope sweep script (the
  table in `docs/envelope.md` is made by hand from the tests), the second public draw and the
  newcomers' protocols (plan section 9), `msfc` threads and functions, the cold read.
* The deadline checker's cross-check against the RTL for `AT` writes (a `DeadlineWatch` for
  timed writes in `test/fw_tb.py`); only `WAIT_CMP` is cross-checked on the RTL.
* F16 and the `ni` miter for v4's new per-thread state (the slot, `ESEL`, `TS`).
