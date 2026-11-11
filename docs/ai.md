# How AI was used, and what checks it

The brief lists "AI-assisted verification" among the methods it wants to
see. This page says how we used it on msfcore, which checks stand between
AI-written work and the chip, and where the AI was wrong.

> Written with the AI itself from the repository and its working notes
> (23-28 Oct 2026); §1 checked by Sam on 28 Oct.

## 1. Who did what

* **The original design, v1, was Sam's**, done at home with an AI coding
  agent (24 Sep – 11 Oct, `history.md` §4): the architecture (a CPU whose
  instructions hit exact cycles, rather than a state machine or a RISC-V),
  ISA v1.0, the first RTL, the assembler and the boot ROM.
* **People** (Sam, Muath) since then: the priorities, every decision in
  `docs/plan.md` §6, the review of v1 that found B1–B9 (`history.md` §6),
  the FPGA block design and every run on the board.
* **AI** (Claude, Anthropic's model, used as a coding agent in Claude
  Code): most of the code since v2, under those decisions: RTL changes,
  the assembler, the ISA model, firmware, tests, device models, the
  formal properties, the tools in `tools/`, and the documentation. The
  agent runs the simulators and tools itself, commits, and pushes to the
  feature branches; nothing is merged to `main` without Sam.
* **v4 (11 Nov):** the plan (`plan-v4.md`: an RTL review, a competitor re-read, five moves,
  a ladder down) was written by the AI from Sam's brief; Sam's order was "follow this plan and
  implement it fully", and, when stuck, "skip the error, report it, a larger model will review
  it". Steps 1 to 5 of the plan, `msfc` and the documents were then written by the AI in one
  conversation, with no sub-agents, and every bug it found or caused is listed with file
  references in [`v4-bugs.md`](v4-bugs.md); what it could not check is in
  [`v4-open-problems.md`](v4-open-problems.md).

## 2. The rule: every AI artefact meets a check it did not write itself

An AI can write code and its tests in the same breath, and both can share
the same misunderstanding. So each kind of work is checked against
something independent of it:

| AI writes | Checked against |
|---|---|
| RTL | an ISA model written from `isa.md`, not from the RTL, compared on every issue of random programs (`verification.md` §2); formal properties with free memory and pins (§3) |
| Formal properties | mutants: known bugs put back into the RTL must each give a counterexample (B1–B4), and every `cover()` must be reached, so a property that is vacuous or too weak shows |
| Tests | mutation testing (`tools/mutation.py`): seeded RTL faults the tests must catch, after the tests have passed on the unmutated design (§8) |
| Firmware | device models written from the protocol standards (PS/2, IEEE 1149.1, ADIv5, CAN 2.0, Maxim 1-Wire, 25xx flash, I2C, SPI, USB, Ethernet), not from the firmware; and the static deadline check (§6b), itself cross-checked against the ISA model |
| The deadline checker | the ISA model (every observed `WAIT_CMP` must fall in the computed range) and a program that must be rejected |
| `msfc`, the compiler (v4) | its own reference interpreter, written from the language's semantics and not from the code generator, on 120 random programs (variables and every change of the pins, in order); the deadline checker run on the compiler's own output; programs that must be refused, with their lines (`test/test_msfc.py`) |
| The timed pins and the engine options (v4) | directed tests that measure the pins in cycles, the model against the RTL on random timed windows, and the planted bugs of `formal/run_formal.py`. **The formal properties have not been run**, and the specification, model and RTL were written by one session in a row: see §3's last v4 row |
| Physical design | LibreLane's DRC, LVS and antenna checks, Tiny Tapeout's precheck, the gate-level test, and reading the timing reports rather than the badge |
| Claims in the docs | `verification.md` §9 maps each claim to the test that backs it, and lists what is not validated |

The FPGA board is the one check outside software: the same C protocol
tests passed on the AUP-ZU3 (§5), run by Muath.

## 3. Where the AI was wrong, and what caught it

Taken from the commit history; each is visible in `git log`.

| What went wrong | Caught by |
|---|---|
| The chip had been hardened for SG13G2, the process of the Tiny Tapeout template the project started from, while the shuttle is CMOS5L; neither we nor the AI had checked it since | a human question led the AI to re-read the competition page against the flow's settings (23 Oct) |
| The slow corner was timed with the *typical* SRAM library: overlapping liberty patterns, and LibreLane loads every match. Every slow-corner sign-off before 23 Oct, including "50 MHz with 3.09 ns to spare", was optimistic | comparing the SRAM clock-to-output in the timing report (3.9 ns) with the slow library (6.7 ns), then reading LibreLane's source |
| The four-thread design was pushed as "green" while the slow corner missed timing by 3.35 ns: the GDS badge does not fail on timing, and the step's notes said the result was pending | an audit of the hardening metrics (23 Oct); now a `signoff` job in CI fails on any setup or hold violation (`tools/check_signoff.py`) |
| The formal harness counted a tool error as a caught mutant, so "B1–B3 caught" could have been errors | moving to SymbiYosys: the covers reported 0 of 9 reached, which was impossible; verdicts are now pass, fail or error |
| A new property (F10) was too strong: it failed for a thread stopped while waiting | the BMC counterexample; the property now holds only while the thread runs, and mutant B4 is still caught |
| The deadline checker counted the wait twice after a `WAIT_CMP`, and lost every bound | its own report (−inf..inf where a hand count gave 85..96) and the model cross-check |
| The JTAG probe returned wrong bits for a partial last byte when the data had more bits set; its test passed by luck | writing the SWD probe with the same code against a stricter model; the JTAG test now sends data that exposes it |
| The I2C monitor needed three designs before it caught every fault on a legal bus | an instruction-level tracer against the pad edges, and sweeps of legal and faulty buses (`history.md` §8) |
| Six mutation "kills" were not real: a new 1-Wire test sent its next command while a 64-bit read was still running (the probe's UART buffer holds one byte), so it failed on the good design and every mutant failed it too | all six were credited to `test_buses` and none to the modules that test those circuits directly; run alone on the unmutated design, the test failed. `mutation.py` now runs the tests on the unmutated design first |
| The deadline checker could not certify `can.s` at any bit time, which read as the firmware being too slow: widening rounded the deadline register's lead (about −175 cycles) to the next power of two, −256 | a script that computed the longest path straight from the code disagreed with the checker by about 90 cycles; recording the checker's own path showed the widening step. Its thresholds are now 16 apart near zero |
| The SPI flash's reads came back shifted by one bit: the SCK waits' CS-check timeout (12 cycles) was shorter than half an SCK period, so a check could run just as SCK changed and the firmware missed an edge | the reference model; a per-cycle trace of the program counter showed the timeout handler running at the edge |
| The first netlist-versus-RTL miter could never fail: an option meant to ignore undefined RTL bits became don't-cares, and optimisation folded the whole comparison to a constant | the result was too good: the AIGER file had one output and no gates. Tracing the comparison's input cone through each pass found the step |
| The AI's rewrite of the MCU peer's SPI loop ignored a timeout, so SCK stuck high counted bits and reported bytes nobody sent. It also kept the original test's same bytes both ways, so a MOSI only following MISO read as correct. Its simulation tests passed, as they only ever drove a clean clock | the first free-running board run: its totals held 60 more SPI bytes than passes. The explanation was then reproduced in simulation. On the board, the logic analyser has since shown SCK high and CS low before the MCU's first SPI frame, and firmware v3 counted no false bytes in that run |
| The AI's I2C test on the MCU waited on a status bit (BUSY) that is only set after the repeated START, so it read the FIFO about 190 µs early and every `PASS` was the byte of the pass before. When the log showed the status stuck at `BUS-BUSY`, the AI first called that harmless | the board's own logs: `rx=--` on pass 1 after every reset, while the on-chip logic analyser showed `0xA5` on the wire on that pass. A stand-in written from TI's manual then reproduced the log's lines; with the fix, the board passed all 148 passes from pass 1 |
| The docs and the AI said 52 of the C tests had never run on the board, and the AI put running them on the list of what was left, when Muath had already run all 67. The AI had only checked its own records, and also asked for an extra MCU reset test that no decision depended on | Muath saying the earlier test had covered them, then a screenshot of `67 of 67 tests passed`. Simulating 11 of its tests from the current RTL then gave the same execution digests as the board |
| README, `verification.md` §9 and the plan called the deadline checker "cross-checked against the ISA model"; on 7 Nov it compared 1 of 29 deadlines, because the model has no SERDES and the other firmwares were skipped with a line CI printed and passed | reading the tool's own output while planning (7 Nov). The words were corrected first; now every firmware test checks each deadline on the RTL with its real peer |
| The AI called four of the new instruments "ours", found nowhere else, then found MarcosAsh's `demo/margins.py` doing the margin finder's job with a Pico, a logic analyser and a PC | a second search before writing it up. "Ours" now means "not found in the READMEs and file lists searched", and says so |
| A margin finder test's hand-worked window for an 80-cycle receiver was wrong; the chip reported 76..84 | the firmware's own result, then the receiver's geometry worked through: the test now derives the window (`uart_limits` in `test_margin.py`) |
| The deadline checker certified a program that broke its own `@range` promise: a planted bug cut `margin_i2c.s`'s THIGH to 15 under `@range R7 24 ...`, the checker trusted the promise over the constant it knew, certified a bound of 57, and the RTL showed 12 | the planted-bug run (§6b), the case it was built for. A promise now stands in only for a value the checker cannot derive (8 Nov) |
| CI's `test` job failed on four pushes in a row and nobody looked: a test that is late on purpose leaked its deadlines into the shared log, and the cross-check read them as the checker's failure | reading the CI log on 8 Nov. Runs that are late on purpose keep their own log |
| The planted-bug run reported three PS/2 mutants as certified but late, which would have been checker bugs. The harness certified each mutant with the firmware's own Q = 240 while the tests ran it at Q = 60, kept only the last test's observations, and let mutants overwrite the waveforms sigrok judges | reproducing the three, then running the checker with the test's Q: it predicts exactly what the RTL showed (d -36..46 where it saw -36..46) and refuses them. All three harness faults are fixed and the campaign re-run |
| Two of the drawn protocols' firmware had bugs on their first try: an `AND` before a `JZ` (`AND` sets no flags) zeroed three of EM4100's nibbles; Microwire's deadlines ran through subroutine calls the checker cannot follow | the RTL test against the frame rebuilt from the data sheet; the checker's "MAY BE LATE" |
| README said a thread's timing never depends on what the other threads do; it does whenever two threads use a shared engine (22 cycles for a CRC alone, 42 while another thread uses the CRC unit). It said a missed deadline is never silent; a TX engine that runs dry sets no flag. It said the chip learns a protocol with no other instrument; the learner runs on a PC. And three numbers were stale (191 tests, "every firmware 20 MHz or less", "at least 9 cycles of slack") | the claim attack of 8 Nov (`plan.md` §7.13): each has a reproducer, two as tests (`test_shared_engine_wait_depends_on_other_thread`, `test_tx_underrun_is_silent`), and each claim is reworded |
| **v4 (11 Nov).** The boot ROM's new autobaud branched with `JZ` on a flag that `AND` does not set: every UART boot hung. The same slip as EM4100's, three weeks apart | the UART boot tests (`test_boot`); fixed with an `ADDS`. `v4-bugs.md` R1 asks for a lint over all firmware |
| v4. Growing the memory to 2,048 words broke `spi_flash.s`, which got its 512-byte wrap for free from 10-bit addresses; no test had said so, because no test had grown the memory | the full suite after the change: a read across the wrap returned zeros. The store moved to the top of the memory, with no cycle added (`v4-bugs.md` F1) |
| v4. Putting `TS` in the deadline checker's state for every program made `onewire.s` lose its certificate: one more register that differs between paths splits the states until the checker widens | a pure-Python test that every shipped program is certified, run after the big suite. `TS` is now tracked only where a program reads it (`v4-bugs.md` T1) |
| v4. `mcu_v3_peer.s` misses two deadlines on purpose to show the late guard, so `msf_timing.py --all` has failed since v3 added it, and CI's step with it | the same test, run for the first time on v4; the program now says `; @late` and the checker must find the miss real (`v4-bugs.md` T2). v3's CI run is the thing nobody looked at |
| v4. `info.yaml` said the clock was "signed off at 20 ns" for a layout nobody had hardened: a target written as a result | re-reading the file while writing the README; reworded |
| v4. The spec, the ISA model and the RTL of the timed pins and of the engine options were all written by the same session in a row, so a shared misreading passes every differential test | not caught: it is the largest risk we know of that no check covers, and `v4-bugs.md` ends with it. A second reader of `isa.md` §6.8-6.9, predicting the pin edges of `test_timed_pins`, would |

## 4. What we take from it

* The checks matter more than the code. Most of the errors above were in
  work that looked finished and had passing tests; they were caught by a
  check written from a different source (a standard, the spec, the
  liberty file, the competition page).
* A green badge is not evidence. Every claim names its test, and the
  numbers come from reports, not summaries.
* The AI is fast at the parts that are mechanical once decided (a firmware
  probe, a device model, a proof harness) and at reading tools' source
  when a result looks wrong. It is no substitute for deciding what to
  build, or for the board.
