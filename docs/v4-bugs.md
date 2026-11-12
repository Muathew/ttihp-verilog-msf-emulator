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

## Found on 12 Nov: CI's first run of v4, and a second reading

CI ran the two pushed commits overnight and all three workflows failed. This section is what
they showed, what a second reading of v4's RTL, checker and formal harness found, and what was
done. From 12 Nov formal runs on the development PC (`v4-open-problems.md` A1), so "verified"
below means a local run unless it says CI.

### V1. F6 did not know that a timed pin write sets Z (the formal failure)
* **Where:** `src/tt_um_msf_ctrl.v`, `wire f_flagev` (F6, near line 1173).
* **Found by:** CI `formal`, run 43099145276: `Assert failed in tt_um_msf_ctrl:
  tt_um_msf_ctrl.v:1163` at step 6, and the same assertion at step 13 on the top level.
  Reproduced locally in 34 s.
* **Cause:** the property, not the RTL. `isa.md` 6.8 gives a timed write Z = 1 (refused) or 0
  (accepted); the flag logic does that (`else if (pwr_do && armed_c) zf[cur] <= t_refuse`), and
  F6 still listed only v3's flag events.
* **Fix:** `|| pwr_do && armed_c` in `f_flagev`.
* **Status:** fixed, verified: the controller passes the BMC to depth 16 (27 min here), the top
  level too.
* **Check:** the three other properties the first session edited for v4 (F5, F11) and F17, F18
  passed the same run; nothing else in the property list predates a v4 change to what it
  describes, as far as I can see.

### V2. CI's mutation result for v4 was empty
* **Where:** the `== mutation check ==` block of the same CI log: 14 of 14 "caught".
* **Cause:** the unmutated design already failed F6 at step 6, so every mutant "failed" there
  too. `run_formal.py` did not say which assertion stopped a mutant.
* **Fix:** `formal/run_formal.py` `caught_by()` prints the property, line and step for each
  mutant, so a mutant caught by the wrong property shows. Also `bmc()` now skips a target that
  does not read the mutated file (a planted bug in `project.v` used to re-run the whole
  controller BMC first, which cannot fail and takes as long as the BMC itself).
* **Status:** fixed; results in "State on 12 Nov" below.

### V3. The two-copy miter (F16, target `ni`) left v4's new controller inputs open
* **Where:** `formal/ni_miter.v` (the two instances of `tt_um_msf_ctrl_ni`); `formal/run_formal.py`
  `NI_THREAD`, `NI_PORTS`, `ni_sh`.
* **Found by:** reading the miter against the controller's v4 port list; confirmed with Yosys
  `check`: `Wire ni_miter.\b.rx_overrun1 is used but has no driver` (174 such wires).
* **Cause:** steps 3 and 4 added inputs (`tx_full1` ... `crc_hi1`, `opt_rd`, `opt_rd1`,
  `err_any`, `err_any1`) and per-thread state (`esel`, the slot, `TS`) and shared state
  (`cnt_hi`). The miter connected none of the inputs, so `setundef -undriven -expose` would
  have given each copy its own free values (a thread on set 1 would have "seen" different
  status in the two copies: a false counterexample), and its inductive state lacked the new
  registers. `parse_check.py` passed it because an open input is legal Verilog.
* **Fix:** the inputs are miter inputs shared by both copies; the per-thread vector is 277
  bits (was 226), the shared one 124 (was 108). `formal/parse_check.py` now runs `flatten;
  check` and fails on any undriven wire outside the SRAM stub and on any port connected at
  the wrong width.
* **Status:** fixed, verified by `parse_check.py` (four targets "ok"); the `ni` BMC result is
  in "State on 12 Nov".
* **Check:** F16 is still bounded and its planted bug still not shown (v3's state).

### T3. A FIFO pop made the checker track TS, and `ps2_device.s` lost three exact releases
* **Where:** `tools/msf_timing.py`, `Analysis.__init__` (`self.uses_ts`), now `reads_ts()`.
* **Found by:** T1's "Check": `msf_timing.py --all` on the step-1 tree (`git archive 0f11429`)
  against CI's output for the head. One difference outside `@late`: `firmware/ps2_device.s`
  0x13e, 0x142, 0x145 went from `208..226`, `472..472`, `234..234`, "release exact" to
  `207..226`, `471..472`, `233..234`, "release may lag up to n-1 cycles". The program had not
  changed.
* **Cause:** T1's fix tracked TS "only if the program contains a `PR`". `FIFO_GET` is
  `PR ra,#0` on page 0, so every program with a FIFO still paid for TS.
* **Fix:** TS is tracked only if the program holds an `ESEL` that selects page 1 and a read of
  port 0. The ranges are the step-1 tree's again.
* **Status:** fixed, verified (`test_site.py::test_ts_is_tracked_only_by_a_program_that_can_read_it`).
  The lost precision was on the safe side (a lower bound one lower), never a wrong certificate.
* **Check:** every other program's ranges are identical between the two trees (229 lines
  compared), so T1's worry about `AT_REG` and `ESEL_REG` splitting states does not show in any
  shipped program; none of them uses `AT` or `ESEL` yet.

### T4. The checker took every read outside port page 1 as 0 (a wrong certificate)
* **Where:** `tools/msf_timing.py`, the `X_PR` case for pages other than 0 (near line 551; was
  `R[ra] = U if page == 1 else ("c", 0)`).
* **Found by:** reading the checker's v4 changes. Reproduced: a program that reads `ERR_STAT`
  (page 2), then takes a 40-cycle path if it is not zero before a deadline 20 cycles away, was
  reported `d at issue 14..14: slack >= 13`, exit 0.
* **Cause:** the line was written in step 3, when page 1 was the only other page. Steps 4 and 5
  added page 2 (option words, error flags) and page 3 (identity, `CNT_H`); their reads became
  the constant 0, so a path taken on an engine error was never analysed.
* **Fix:** the option words, the error flags, `AT_STAT` and `CNT_H` are unknown; `CHIPID`,
  `CHIPVER`, `CHIPCAPS` are their constants; an unused port reads 0. The program above is now
  `-45..14: MAY BE LATE`, exit 1.
* **Status:** fixed, verified (`test_site.py::test_a_path_taken_on_an_engine_error_is_analysed`,
  seven cases). No shipped program reads those pages, so no published certificate changes.
* **Check:** the ISA model has its own table for these reads (`test/isa_model.py` near line
  542); the constants are now in three places (RTL, model, checker).

### T5. The checker certified a timed write that the hardware refuses (the slot was full)
* **Where:** `tools/msf_timing.py`, the `AT rb` case of `X_PW` on page 1 and `timed_write()`.
* **Found by:** reading `isa.md` 6.8 while planning the timed-pin device (12 Nov, evening).
  Reproduced: `WAIT_EDGE; TS R1; AT R1+100; PIN_SET; AT R1+200; PIN_CLR`. The second `AT`
  issues about 8 cycles after the first write, while its event is still held; 6.8 says
  that `AT` sets refuse-next and the write is refused (Z = 1, LATE). The checker reported
  both writes "slack >= 94 / >= 189", exit 0.
* **Cause:** the abstract state tracked the armed time but not the held event: "a slot
  holds one event" was not modelled at all.
* **Fix:** two more abstract registers. `HELD_REG` is the time of the held event (set by an
  accepted write whose time is ahead, shifted with time like `TS`, and emptied by
  `settle_held()` once its time has certainly passed, so loops still join). `RN_REG` is
  refuse-next, set by an `AT` that may find the slot full; the next pin write is then
  reported refused. The program above is now refused (exit 1); with a `WAIT_CMP` on the first
  time before the second `AT` it is certified.
* **Status:** fixed, verified by `test_site.py::test_a_second_timed_write_while_the_slot_is_full_is_refused`
  and the four checker tests near it. No shipped program uses `AT`, so no published
  certificate changes; `msf_timing.py --all` was not re-run here (CI runs it).
* **Check:** (1) the d = 0 edge: the checker treats an event due in the cycle the next `AT`
  issues as still held (refuse-next); confirm against the RTL's refuse-next condition in
  `src/tt_um_msf_ctrl.v` (`at_pend`, near line 836). If the RTL sees the slot as empty in
  that cycle, the checker is one cycle pessimistic, which is safe. (2) `TSTART` of a thread
  by itself clears its slot (6.8); the checker keeps `HELD_REG`, which is safe. (3) A loop
  back to an unbounded `WAIT_EDGE` keeps a held event possibly pending, so the next `AT`
  is flagged unless the program states its input spacing (`@range`): correct, but the
  timed-pin device below must state it.

### I5. CI's deadline cross-check failed on two programs (inherited from v3)
* **Where:** `.github/workflows/test.yaml` step "Deadlines against the RTL"; `test/test_nes_pad.py`;
  `test/test_mcu_v3_peer.py` line 178; `test/fw_tb.py` `DeadlineWatch`; `tools/msf_timing.py`
  `check_rtl_log`.
* **Found by:** CI `test`, run 43099145368: all 319 cocotb tests passed, then `nes_pad.s: 4 of
  5 deadlines reached ... not reached: 0x0aa` and `mcu_v3_peer.s: 0 of 2 ... 0x13b, 0x152`,
  exit 1. v3's CI runs failed the same way (it was never green there).
* **Cause:** (a) the NES test shortened the frame to one piece (`FRAME_P=1`), and the wait
  between frames is then never issued. (b) The peer program's two deadlines are late on
  purpose, its test booted with `late_ok=True`, and such runs are kept out of the log.
* **Fix:** (a) the test runs the program as shipped (a real 16.7 ms frame, 400,000 cycles).
  (b) `DeadlineWatch` knows the `@late` deadlines from the checker: the RTL must miss each of
  them every time (d < 1), and the LATE flag its thread raises just after is expected, once;
  any other LATE rise still fails the test. The peer test boots without `late_ok`.
  `check_rtl_log` fails if an `@late` deadline was met on the RTL.
* **Status:** fixed, verified: both tests pass (3 of 3), and `msf_timing.py nes_pad.s
  mcu_v3_peer.s --rtl-log` gives 5 of 5 and 2 of 2. The other 174 deadlines were reached in
  CI's run.
* **Check:** `late_ok=True` is still used by other tests (search `late_ok`); each is a run that
  breaks its program's stated assumptions on purpose, which is what the flag is for.

### I6. Three CI steps have never run on v4, and the artifact store is full
* **Where:** `.github/workflows/test.yaml`: "Planted timing bugs", "sigrok decoders", "Learning
  a protocol" come after the step that failed (I5). The `docs` workflow and the test job's
  upload failed with `Failed to CreateArtifact: Artifact storage quota has been hit`.
* **What was done:** the third step's tests were run here (`pytest test/test_learn_tool.py
  test/test_site.py test/test_msfc.py`: 201 passed before today's changes). The upload step is
  split: results always (small), the waveform only on failure, both kept 7 days and neither
  able to fail the job. The first two steps are still unrun on v4 (`fw_mutants.py` is slow
  here; sigrok-cli is not installed on this PC).
* **Status:** the quota itself is open, and it is the account owner's to clear: all 324 live
  artifacts total 6.9 GB (measured later on 12 Nov; the first count above saw only the first
  100), about 4.6 GB of it the `GDS_logs` of earlier hardening runs and 1.7 GB `test-results`
  with the suite's waveform. Breakdown and the delete commands in `v4-open-problems.md` A2.
  Nothing was deleted. Until space is freed the `docs` workflow (Tiny Tapeout's own action, which uploads
  the PDF) stays red whatever the repository holds.

### D1. `isa.md` named the page-2 reads wrongly
* **Where:** `docs/isa.md` 6.9, the port table: `TX_OPT ra`, `RX_OPT ra`, `ERR ra`, `TX_EXT ra`,
  `RX_EXT ra`.
* **Cause:** the assembler's names are `TX_OPT_RD`, `RX_OPT_RD`, `ERR_STAT`, `TX_EXT_RD`,
  `RX_EXT_RD` (`test/assembler.py` `PAGE2_OPS`); `ERR R1` does not assemble.
* **Status:** fixed in the table.

### What the second reading checked and found sound
* **R1's check** (a jump on a flag an `AND`/`OR`/`XOR` did not set): a scan of all 80 programs
  and the boot ROM for a conditional jump right after a logic or shift instruction found one
  site in code, `am2302.s` 0x0ae (`ADDS R6,R6,R6` / `SHR R6,R6,#1` / `JNZ`), which is the
  idiom: the flags are the `ADDS`'s. The other three hits are data words.
* **F1's check** (reliance on 10-bit addresses): `la_stream.s` line 54, `logic_analyzer.s` 75,
  `pinfind.s` 800 and `flight.inc` 89 mask `CAP_STAT` / `CAP_TPOS` with `0x3FF`. Their buffers
  are below 1,024, where bit 10 is 0, so they are right as shipped; a copy with its buffer
  above 1,023 would need `0x7FF`. Left as it is, noted here.
* **Timed pins against `isa.md` 6.8** (`src/tt_um_msf_ctrl.v`, "Pin writes" and "the slot"):
  arrival (now / refused / held), refuse-next and its use, application order, `TSTOP` and
  `TSTART`, Z and LATE: the RTL does what the text says, case by case. `TS` had no property at
  all; it has one now (F20).
* **The engines against 6.9** (`src/tt_um_msf_serdes.v`): both stuffing kinds on both sides, the
  three error flags and what clears them, the external clock's gate, edge, `pre` and release:
  read against the text, no difference found. Not a proof: the engines have no formal
  properties, only the directed tests.

### State on 12 Nov (what has run on the tree that was pushed)
* **New properties** (`src/tt_um_msf_ctrl.v`, near lines 1267, 1301, 1315): F19 (engine
  binding: a thread's engine writes, pops and option writes go to the set its `ESEL` words
  chose, a blocking engine instruction completes on that set's status, and what it reads
  back is that set's; two asserts), F20 (a running thread's `TS` is the time of the event
  that ended its last `WAIT_EDGE` / `WAIT_CMP`, or its release cycle if none, whatever the
  other threads did), F21 (the cost table of `isa.md` section 5: how many cycles after an
  instruction its thread issues the next one; the deadline checker's certificates add these
  numbers up). With the F6 fix the controller has 77 assertion cells; `top` 67, `ni` 86, `cap` 0
  (`formal/parse_check.py`, native Yosys, all four "ok", no undriven wires).
* **Mutants** (`formal/run_formal.py`, run one at a time, each caught by its own property):
  F19 x2 (`ctrl.v` 1287, 1291, step 3), F20 (1308, step 6), F21 (1343, step 3), and the two
  F17 mutants (1243 step 6, 1255 step 4). The whole mutant list has not been run on this tree.
* **BMC, controller, depth 16:** passed with the F6 fix before F19-F21 were added (27 min).
  With F19-F21 it reached step 11 of 16 with no failure when the session ended
  (`%TEMP%\msf_formal_wrjkj90w`). Top level and `ni`: not run to the end on this tree.
* **CI formal, e92524b (run 43275921661): green.** BMC depth 16 PASS on `tt_um_msf_ctrl`,
  `tt_um_msf_cap`, `tt_um_msf`; 18 of 18 cover points reached; 18 of 18 mutants caught, 16
  by their own property, the F12 and F13 mutants by F1 (`ctrl.v:1068`, steps 4 and 6),
  which a reviewer may want to look at: F12/F13 have no assertion that catches them first.
  The `ni` target (F16) is not in CI's default run (`--top ni` only) and has not run on v4.
* **Simulation:** CI ran 319 of 319 cocotb tests at 46ae2d6. After the 12 Nov edits, run here:
  `test_nes_pad` and `test_mcu_v3_peer` (3 of 3), the four new `test_site.py` tests, and
  `msf_timing.py nes_pad.s mcu_v3_peer.s --rtl-log` (5 of 5, 2 of 2). The full suite, the
  pure-Python set and the full formal run were left to CI's run on the 12 Nov push
  (`v4-open-problems.md` A3).
* **Timed writes cross-checked on the RTL (12 Nov, evening):** `test/tb.v` `at_evt` and
  `DeadlineWatch._watch_timed` (`test/fw_tb.py`) check every timed pin write a firmware test
  issues against the checker's range (floor d >= 0). First program: `firmware/ow_presence.s`,
  a 1-Wire presence responder; `test_ow_presence` passes (1 of 1, 23 s), and
  `msf_timing.py firmware/ow_presence.s --rtl-log` gives 4 of 4 (d = 349 and 1435 for the two
  timed writes, 348 and 1434 for the `WAIT_CMP`s, each the checker's single value).
  **Check:** `DeadlineWatch` reads `at_time_c` and `cnt` at the negedge after the write
  issues, as it reads `qa` for `WAIT_CMP`. A reviewer should confirm that `at_time_c` is
  still the armed time in that half cycle (the slot register changes at the posedge).

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
