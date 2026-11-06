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
