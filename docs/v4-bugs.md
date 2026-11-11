# v4: bugs found while building it, with references

Written for a larger model to analyse (Sam, 11 Nov 2026: "remember any bugs reference in a
file and a larger model will analyse"). Every bug I found or caused while implementing
`docs/plan-v4.md`: where it is, how it was found, what I believe the cause is, what I did,
and what I would want checked. Fixed bugs are here too, because a fix can be wrong and a bug
usually has siblings. Things I could not check at all are in `docs/v4-open-problems.md`.

Status words: **fixed, verified** = a test that failed now passes (named); **fixed, not
verified** = changed, but nothing here can run the check; **open**. Line numbers are of the
working tree on 11 Nov 2026 and drift as files change: search for the quoted text.

The step-1 findings (B1-B11 of the RTL review, among them the v3 store-ahead bug confirmed
in simulation) are described in `docs/plan-v4.md` section 5 and are not repeated.

Reproduce the suite: `cd test && ../.venv/Scripts/python.exe run_tests.py -m <module>`
(`-t <exact test name>` for one test; `MSF_BUILD=<name>` for a second build directory);
pure-Python tests: `python -m pytest test/test_learn_tool.py test/test_site.py test/test_msfc.py`.

## RTL and boot ROM

### R1. The ROM's autobaud hung every UART boot
* **Where:** `test/bootloader.s` lines 60-62 (`AND R2, R1, R2` / `ADDS R2, R2, R0` / `JZ noab`);
  regenerated into `src/tt_um_msf_bootrom_data.v` and `fpga/rtl/tt_um_msf_bootrom.v`.
* **Found by:** the UART boot tests (`test/test_boot.py`) after step 5 added autobaud.
* **Cause:** `AND` does not set Z (`docs/isa.md`), so `JZ noab` branched on a stale flag.
* **Fix:** `ADDS R2, R2, R0` (R0 = 0) between them; the loader is 115 words.
* **Status:** fixed, verified (`test_boot` passes in the final-tree run).
* **Check:** the same slip anywhere else: a conditional jump whose last flag-setting
  instruction was `AND`, `OR` or `XOR`, in `test/bootloader.s`, `firmware/*.s`,
  `tools/msfc.py`'s output. A lint over the assembler's output would find them all.

### R2. TS was captured for a thread that is stopped
* **Where:** `src/tt_um_msf_ctrl.v` line 823 (`if (run[ak] && st[ak] && evt[ak] && !whit[ak]) ts[...] <= cnt`).
* **Found by:** the RTL-vs-model state comparison (`test/diffcheck.py`, random programs),
  as far as my notes say.
* **Cause:** the RTL followed the waits of threads that were not running; the spec says TSTART
  clears `TS`, so the value could only show in the debug view and the comparison probe.
* **Fix:** gate the capture on `run[ak]`.
* **Status:** fixed, verified (`test_random_diff`, `test_timed_pins`).
* **Check:** whether the debug view can still show a stale `ts` of a stopped thread, and
  whether F17 (not run anywhere) allows what this does.

### R3. The "refuse next" flag stayed set after the write it refused
* **Where:** `src/tt_um_msf_ctrl.v` lines 830-838 (`at_bad`; "refuse-next is used up"), and
  the same rule in `test/isa_model.py` (`p_bad` handling in the pin-write path).
* **Found by:** the directed tests in `test/test_timed_pins.py` and the random timed windows.
* **Cause:** the flag was cleared only on TSTART/TSTOP, so one early `AT` refused every
  later write of that thread.
* **Fix:** cleared on consumption in the RTL and in the model.
* **Status:** fixed, verified (`test_timed_pins`, `test_random_diff`).
* **Check:** F17's ghost (`// F17 (v4)` in `src/tt_um_msf_ctrl.v`) was written before this
  fix: it may need the same rule.

## The ISA model

### M1. The model applied TS and due-event clearing at the start of a step
* **Where:** `test/isa_model.py` lines 225-285 (`_ts_new`, `_writes`, applied at the end of
  the step) and line 621 (a TS capture of the same cycle loses to TSTART).
* **Found by:** `test/diffcheck.py`: the snapshot after an issue differed by one cycle.
* **Cause:** the RTL registers these at the end of the cycle; the model changed them first.
* **Fix:** everything lands at the end of the step, in the RTL's order (T3 first, T0 last,
  then the instruction's own write).
* **Status:** fixed, verified (`test_random_diff`).
* **Check:** whether any other model field is updated early in the same way and is simply
  not yet compared.

## Tools

### T1. `msf_timing.py`: one more register in the state cost `onewire.s` its certificate
* **Where:** `tools/msf_timing.py`: `Analysis.ts_fixed` / `uses_ts` (new), `State.key()`,
  `Analysis.run` (`MAX_STATES`, `WIDEN_AFTER`, `_join`).
* **Found by:** `test/test_site.py::test_every_shipped_program_gets_a_row` in the pytest run
  after the final-tree suite. `python tools/msf_timing.py firmware/onewire.s` printed
  `d at issue 63..712 ... also reached with a target not from CNT` at 0x13b where the
  step-1 tree prints `64..711` and certifies.
* **Cause (my analysis):** step 3 put `TS` into the abstract state for every program. The
  state key holds every register, so a register that differs between paths multiplies the
  states at each pc; at `MAX_STATES` the join/widen path sets R5 to unknown.
* **Fix:** `TS` is tracked only if the program contains a `PR` (the `TS` read).
* **Status:** fixed for `onewire.s`: its report is identical to the step-1 tree's, and
  `test_site.py` passes. The CI step `msf_timing.py --all --check-model 20000` was rerun on
  the final tree (result in "State at hand-over" below).
* **Check:** `AT_REG` and `ESEL_REG` are still in the state of every program. Programs that
  use `AT` or `ESEL` (`firmware/msfc/*.msf` outputs, the `test_timed_pins` programs) could
  split states the same way. Compare every program's ranges with the step-1 tree's
  (`git archive 0f11429 tools test firmware`), not only its verdict.

### T2. A late-on-purpose WAIT_CMP made `msf_timing.py --all` fail (already so at step 1)
* **Where:** `firmware/mcu_v3_peer.s` lines 237 and 259 (`WAIT_CMP R3 ; late on purpose`);
  the checker prints `d at issue -1..-1: MAY BE LATE` for both and exits 1;
  `.github/workflows/test.yaml` lines 21 and 51 run `--all`;
  `test/test_site.py::test_every_shipped_program_gets_a_row` fails on it.
* **Found by:** the pytest run after the final-tree suite. Reproduced at commit 0f11429
  (step 1), so it is not caused by v4's changes: v3 added the program, plan7's site test
  and CI predate it.
* **Cause:** the checker is right (the program misses those deadlines to show the late
  guard); nothing let a program say so.
* **Fix:** an annotation, `; @late` on the `WAIT_CMP` line (`tools/msf_timing.py`: `RE_LATE`,
  `source_map`, `Analysis.on_purpose`, and the check at the end of `analyse`). The checker
  takes it only if the target has passed on every path (largest d < 1); an `@late` on a
  deadline that may be met, or where the analysis reaches no `WAIT_CMP`, is an error. Such a
  deadline reports `LATE ON PURPOSE`. The two lines of `firmware/mcu_v3_peer.s` are marked.
  `tools/site_build.py` leaves them out of the slack column; `site/playground.py` rows carry
  `on_purpose`.
* **Status:** fixed, verified by `test/test_site.py` (`test_a_deadline_marked_late_on_purpose_
  is_certified_only_if_it_is_always_late`, `test_the_peer_firmware_that_misses_deadlines_on_
  purpose_says_so`).
* **Check:** that the annotation cannot hide a real miss: the three failure cases are in the
  first test; a larger model should look for a fourth (a deadline that is late on some paths
  and on time on others while the checker's hi is wrongly below 1).

## Firmware

### F1. `spi_flash.s` depended on the 1,024-word address space
* **Where:** `firmware/spi_flash.s` line 47 (`.equ FLASH`), line 168 (`OR R5, R5, R0` after
  `INCR R5`), line 289 (the erase's end test); `test/test_targets.py` line 251;
  `test/test_gl_firmware.py` line 115; `docs/instruments.md` line 62.
* **Found by:** the final-tree suite: `test_targets.test_flash_id_status_read` (after a READ
  crossed the 512-byte wrap, bytes 8-10 of a transfer read 0 instead of the store's bytes 0-2)
  and `test_flash_speed_limit` (SCK 28: `works=False`).
* **Cause:** the store was at 0x200-0x3FF; `INCR` from 0x3FF gave 0x400, which a 10-bit
  address folded to 0x000, and the `OR` put the base back. With 11-bit addresses 0x400 is a
  real address.
* **Fix:** the store moved to 0x600-0x7FF, the top of the 2,048 words, so running off its end
  runs off the address space, with no extra cycle on the read path (the speed limit is
  untouched). The erase's end test `AND`/`ADDS`/`JZ` needed `FLASH` to be a single bit; it is
  now `SUBS R1, R2, R0` / `JN er_done`. On a 1,024-word chip the same words appear at
  512-1023, so the program should work unchanged there.
* **Status:** fixed, verified (`test_targets`: 22 of 22). Not verified: the gate-level
  `test_gl_firmware.test_gl_spi_flash` (needs CI's netlist), and the FPGA's C test
  (`fpga/ps/hil/msf_tests.c` line 811 loads the store at SRAM 512, which should alias on the
  v3 FPGA's 10-bit memory; not run).
* **Check:** other programs that rely on address arithmetic folding at 1,024: search the
  firmware for `INCR`/`ADDS` on a pointer followed by `OR` with a base, for `0x3FF`, and for
  the word "wrap". The suite passed everywhere else, but it covers only the paths it tests.

## Site

### S1. The playground still believed in 1,024 words
* **Where:** `site/playground.py` line 24 (`FIRMWARE_WORDS = 896`); `test/test_site.py`
  `test_too_long_does_not_assemble` (897 NOPs).
* **Found by:** the pytest run: 897 NOPs now assemble, so the check said "not followed".
* **Cause:** the capacity was v3's 1,024 less the 128 below the program.
* **Fix:** 1,920; the test uses 1,921 and 1,919.
* **Status:** fixed, verified (`test_site.py::test_too_long_does_not_assemble`).

## msfc

### C1. The first edge of a timed write was one cycle late
* **Where:** `tools/msfc.py`, the `at t <pin op>;` statement.
* **Found by:** `test/test_msfc.py::test_uart_edges_are_104_cycles_apart_and_decode`.
* **Cause:** it compiled to `WAIT_CMP` then the write, so the write was issued after its time;
  the form has to be `AT` + write + `WAIT_CMP` so the hardware makes the edge on the cycle.
* **Status:** fixed, verified.

### C2. Compiled code disagreed with the interpreter on nested expressions
* **Where:** `tools/msfc.py` `expr` (about lines 336-390) and the condition code (about 412).
* **Found by:** `test_compiled_code_equals_the_interpreter_on_random_programs` (120 seeds).
* **Cause:** a right operand that is itself an operation, and compound assignments whose right
  side reads the target, overwrote an operand.
* **Fix:** the right side goes first into R7; `x op= e` evaluates `e` into a temporary; a left
  operand that needs both temporaries is refused with "expression too complex".
* **Status:** fixed, verified (120 seeds).
* **Check:** that the refusals are not too eager: programs that need no refusal should compile.

## Tests and infrastructure

* **I1.** `test/test_debug.py` `test_debug_single_step` (line 102) hung the whole run: my
  scripted `ClockCycles` waits for edges that never come when the clock is driven by hand.
  Replaced by two explicit edges. Verified.
* **I2.** `test/Makefile` line 49 (`COCOTB_TEST_MODULES`) lacked the four new modules, so
  CI would not have run them. Added; `test/run_tests.py` has them too.
* **I3.** `formal/run_formal.py` (mutation anchors, `assert old in s` near line 201): the
  anchors of the B1 pc width and the F16 stat mutants no longer matched after step 2 and 3.
  Fixed; all 15 anchors resolve (not run against a solver).
* **I4.** `test/test_engine_sets.py` `test_tx_on_uo_received_on_ui_by_the_other_set` read data
  RAM an earlier test had left (the loader does not clear it), and waited for
  `uo_out == DONE` while `uo[3]` carries the TX line. Fixed by preloading zeros (line 184)
  and masking `0xF7` (line 198). Verified (4 of 4). **Check:** other tests that read RAM they
  did not preload.

## Where correlated mistakes are most likely

The plan's method is a model written from the specification and compared with the RTL. In
steps 3 and 4 the specification (`docs/isa.md` 6.8, 6.9), the model and the RTL were all
written by the same session in a row, so a shared misreading would pass every differential
test. The pieces with no second reader: the timed-pin slot rules (section 6.8: behind = 0,
1-255, else held; "refuse next"; T3 first, T0 last), the external-clock layout (6.9), and
the stuffing kinds. A reader of `docs/isa.md` alone, asked to predict the pin edges of
`test/test_timed_pins.py`'s programs, would find any such misreading.

## State at hand-over (11 Nov 2026, when Sam said to finish)

Order of events, so a reviewer knows what each result applies to:

1. Steps 1-5 and `msfc` written in one working tree (step 1 committed earlier as 0f11429).
2. Final-tree cocotb suite, four shards: 319 tests in 77 modules, 316 passed, 3 failed
   (F1 twice, I4). Fixed; `test_targets` (22 of 22) and `test_engine_sets` (4 of 4) re-run
   and pass. **The whole suite was not re-run after those fixes.**
3. Pure-Python tests then found T1, T2, S1 (fixed; `test_site.py`'s new tests pass).
   `python tools/msf_timing.py --all --check-model 20000` (CI's "Firmware deadlines" step)
   on the final tree: exit 0, 73 of 181 deadlines compared with the model, the two
   `mcu_v3_peer.s` ones reported `LATE ON PURPOSE`. The full pytest set was still running
   when this was written; the modules that use the checker (`test_timed_pins`,
   `test_mcu_v3_peer`, `test_learn`, `test_engine_sets`) were being re-run too.
4. Committed as c68fb13 (code and tests) and 670bec8 (documents), pushed as branch `v4`;
   CI (`formal`, `test`, `docs`) queued at 20:58 UTC. The formal run is the first for F17/F18.
   Read it with `gh run list --branch v4`, and log whatever it shows here.

What a larger model should start with: the CI `formal` result (F17, F18, the edited F5 and
F11); T1's "Check" (state splitting for `AT`/`ESEL` programs); F1's "Check" (other firmware
relying on 10-bit folding); the last section above (a second reader of `isa.md` 6.8-6.9).
Not done at all: see `docs/v4-open-problems.md` section C.
