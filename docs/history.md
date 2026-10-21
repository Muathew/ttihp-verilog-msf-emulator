# msfcore project history

One log of how the design got here: the early decisions, research, plans
and problems, oldest first. It replaces 17 separate planning files (listed
at the end). The current design is described in [isa.md](isa.md),
[info.md](info.md), [verification.md](verification.md) and [fpga.md](fpga.md).
Where an early decision was later changed, the entry says so.

## Timeline

| Date | Who | What |
|---|---|---|
| 2026-09-10 | Jane Street | Protocol-emulator ASIC competition announced |
| 2026-09-19 | Muath | Repository created from the Tiny Tapeout IHP template |
| 2026-09-23 | Sam | Toolchain working (Icarus, cocotb 2.0.1); 12 MHz clock target chosen |
| 2026-09-24 | Sam | Architecture decided (hybrid core), ISA v1.0 locked, competitor research, first RTL, assembler and boot ROM |
| 2026-09-25 | Sam, Muath | First green suite (9/9): WAIT stalls hold the PC so the next instruction re-executes |
| 2026-09-28 | Muath | First hardening attempt fails DRC: ~31K cells against a ~24K budget |
| 2026-09-30 | Muath | IHP SRAM macro integrated in place of the flip-flop SRAM; GDS rendered in GDS3D |
| 2026-10-11 | Sam | Over-the-wire UART bootloader, echo loopback, extension protocols, constrained-random suite |
| 2026-10-12 | Muath, Sam | cocotb reference model; gate-level simulation fixed with a behavioural SRAM macro model |
| 2026-10-14 | Sam | 10BASE-T transmit at 20 MHz as a real test (v1: waveform playback) |
| 2026-10-16 | Sam | Full review of v1: 9 bugs and the gaps below; v2 plan written |
| 2026-10-17 | Sam | v2 done: bug fixes, ISA v2, SERDES, CRC, two threads, boot ROM v2, formal, 103 tests |
| 2026-10-18 | Muath, Sam | AUP-ZU3 FPGA port; PS-stepped core and C protocol tests (14/14); 1 Hz blink; competitor survey updated (research.md) |
| 2026-10-19..21 | Sam | Instruments from the survey: SPI and I2C targets, UART / I2C / SPI bus monitors, UART-to-SPI bridge, timing-fault knobs, CI hardening table, mutation score (39/40), WS2812 and a PIO comparison; 130 tests |
| 2026-10-21 | Sam | Branch `rtl-changes`: 50 MHz sign-off, a timeout per thread, a hardware FIFO between the threads, the debug view; 137 tests |

## 1. Architecture decision (24 Sep)

Three models were compared:

| Model | For | Against |
|---|---|---|
| A. PRU-style RISC | ordinary assembly firmware, easy to verify, ~3–6K cells | timing is loop delays unless counters are cycle-exact |
| B. PIO-style state machines | tiny, inherently cycle-accurate, RP2040-proven | awkward for general logic, less "CPU-like" than the brief asks for |
| C. Hybrid RISC + shift engines | most capable | most area, two verification stories |

The first recommendation was A with the smallest footprint (8 registers,
16-bit instructions, 256-word SRAM, ~3–4K cells). The architecture research
the same day changed it to a hybrid built around a RISC brain, with:

* a 16-bit hardware shift register (`SHIFT_IN`/`SHIFT_OUT`) so UART, SPI and
  JTAG are one shift per bit and the ALU stays free for CRC;
* event-driven stalls, `WAIT_EDGE` and `WAIT_CMP`, with a hardware timeout
  ("a hang is a dead chip"); the research ranked these the most valuable
  difference from PIO and PRU;
* open-drain I/O through `OE_SET`/`OE_CLR` (needed for I2C, USB, SWD);
* a 512-word instruction memory and a ROM UART bootloader, the two things
  that make the chip reprogrammable after fabrication;
* a single 16-bit free-running cycle counter and no fractional divider
  (UART at 104 cycles/bit has 0.17 % error).

The research estimated ~8K cells (≤10K worst case) against an 18K usable
ceiling. Stretch blocks (bit stuffing, CRC assist, a bit-pattern
micro-sequencer) were to be optional.

Later changes: the flip-flop memory became the IHP SRAM macro (30 Sep). v2
added a SERDES and a CRC unit, which cover bit stuffing and CRC in
hardware. 10BASE-T, first dropped as infeasible at 12 MHz, was built in v2
at 20 MHz.

## 2. Design questions (24 Sep)

77 questions were answered between Sam, Muath and the research. Only
decisions that still explain the design are kept here.

| Topic | Decision | Later |
|---|---|---|
| Core | hybrid, cycle-accurate; study RP2040 PIO first | kept |
| Registers, width | 8 x 16-bit, 16-bit instructions, flags Z and N only, no multiply | kept; v2 added a second register file for thread 1 |
| Immediates | imm8 everywhere; `WAIT_CMP rs` compares the counter with a register, so no wide immediate is needed | kept |
| Calls | `CALL`/`RET` with R7 as the link register | v2 made `CALL imm11` jump directly |
| Memory | 512 words, one image, loaded during reset; run forever, no halt | kept; 0–127 became data RAM in v2 |
| Pins | whole-byte `PIN_W` plus OE set/clear masks; 1-cycle output latency; firmware owns all 8 `uio` pins; `ui_in` read by `IN_RD` | kept; v2 added per-bit set/clear/toggle and 2-flop input synchronisers (+2 cycles) |
| `uo_out` | done/status flag | v2: fully firmware-writable |
| Clock | 12 MHz, 8 MHz fallback if timing fails | constrained at 40 ns (25 MHz) in v2; firmware assumes 12 MHz (20 MHz for 10BASE-T) |
| Protocols | UART 8N1, 9600–115200; SPI 1 Mbit/s, all four modes in firmware; I2C 100 kHz; master and slave planned for SPI and I2C; USB low speed as stretch; JTAG as the "firmware only" demo | masters implemented; slaves not yet |
| Verification | per-block tests, bit-accurate protocol checkers, 1000-seed constrained random, golden model, gate-level rerun, 3 negative tests, waveforms kept as evidence | golden model became the independent ISA model plus formal (v2) |
| FPGA | pre-check on Muath's AUP-ZU3 | done 18 Oct |
| Area and flow | 18K-cell usable ceiling; TT SRAM macro (registered read latency budgeted); 5 % slow-corner margin; RTL freeze after all protocol tests pass | v2: 6,893 cells, 24 % of the area before routing |
| Team | Sam and Muath, pair programming; `main` stays green, feature branches | kept |
| Boot | 128-word ROM UART bootloader | v2: framed, CRC-8, ACK/NAK, plus a parallel loader |

Open at the time: the scoring rubric, and who breaks ties on architecture.

## 3. Competitor research

Moved to [research.md](research.md): the 24 Sep survey (5 entries, IHP
prior art, ideas taken) and the 18 Oct update (about 55 entries, led by
PRISM, Abagel-coder and TeslaCoilerOW, with updated recommendations).

## 4. v1 plan and progress (24 Sep – 11 Oct)

The plan ran in milestones, each step closed by a measurable gate:

| Milestone | Scope | Outcome |
|---|---|---|
| M0 | toolchain | done 23 Sep |
| M1 | ISA spec, block diagram, area budget, firmware plan | done 24 Sep (`isa.md` v1.0) |
| M2 | RTL blocks, integration, blink smoke test, assembler | gate met 25 Sep after fixing the WAIT stall (hold the PC) and generating the boot ROM data |
| M3 | UART TX/RX, SPI, I2C firmware with checkers, constrained random, stretch USB/Ethernet | v1 bit-bang demos by 14 Oct; replaced by the v2 firmware library |
| M4 | synthesis, P&R at 12 MHz, gate-level sim, submission | open; the GDS action in CI is the gate |

A known gap from 25 Sep: every test preloaded SRAM directly, so nothing
exercised the bootloader. It became M3.0 and was closed by the over-the-wire
bootloader tests (11 Oct).

The v1 verification plan added: dynamic reprogramming (UART, SPI, I2C, JTAG
loaded one after another without reset); UART RX and echo; SPI modes 0 and 3;
I2C with stretching; extension protocols in firmware (USB LS, JTAG TAP with
IDCODE, SWD, PS/2, CAN 2.0A); ±1–3 % baud drift; and invariant checks. Those
are now `test_reprogrammable`, `test_protocols`, `test_extensions` and
`test_crv_invariants`, kept as regressions. The invariant checks were
directed simulations; real formal proofs came with v2.

## 5. v1 hardening failure (28 – 30 Sep)

The first GDS run failed with unresolved DRC (shorts, spacing). The design
mapped to ~31K cells against ~24K: the flip-flop SRAM alone was ~6,000
flip-flops, and P&R spread cells thin and left routes unfinished (the
"wires to nothing" seen in GDS3D). Fix: the TT-style IHP
`RM_IHPSG13_2P_512x16_c2_bm_bist` macro, integrated 30 Sep following
[urish/ttihp-sram-test](https://github.com/urish/ttihp-sram-test).

Corrected figures (17 Oct): 6x4 tiles is ~1.21 x 0.62 mm ≈ 0.75 mm², the
macro is ~0.088 mm², and v2 maps to 6,893 cells, 24 % of the area before
routing. The GDS3D process file for viewing the GDS is
[`gds3d/tt_um_msf.txt`](gds3d/tt_um_msf.txt) (20 PDK layers; enable the
TopMetal layers to see the macro's power connections).

## 6. v2 rework (16 – 17 Oct)

A full review of v1 found:

| ID | Finding | Kind |
|---|---|---|
| B1 | instruction after any `WAIT_*` executed twice | RTL bug |
| B2 | `WAIT_EDGE` and `WAIT_CMP` release conditions mixed | RTL bug |
| B3 | ROM/SRAM fetch mux used the current address with registered data | RTL bug |
| B4 | bootloader corrupted back-to-back UART bytes | firmware bug |
| B5 | bootloader ignored the WAIT timeout | firmware bug |
| B6 | no input synchronisers | silicon risk |
| B7 | bootloader terminator `0x0000` = `LDR R0,#0`; no CRC, no ACK | protocol |
| B8 | golden model mirrored the RTL, B1 included | verification |
| B9 | gate-level SRAM model zeroed `DOUT` when idle | model bug |
| G1–G6 | shift register not loadable/readable; `uo_out` unused; no per-bit pin ops, no indirect jump; SRAM 0–127 unused; USB/Ethernet were waveform playback; ~85 % of the area unused | ISA and feature gaps |
| V1–V2 | "formal" tests were directed sims; I2C test checked only START, SPI never checked MISO | verification gaps |
| H1 | stale docs and numbers, dead files, 70 MB of generated GDS in git, runner gaps | housekeeping |

Everything was fixed in 11 phases, each closed by a gate:

| Phase | Result |
|---|---|
| 0 Infrastructure | `run_tests.py` reads sources from `info.yaml`, `--all` runs every module, conda venv DLL fix, review probes as regression tests |
| 1 Bugs B1–B5 | fixed, each with a regression test that failed on v1 |
| 2 Synchronisers | 2-flop on `ui_in`/`uio_in`, +2 cycles, documented |
| 3 ISA v2 core | EXT group, `LDH`, pin set/clear/toggle, `uo_out` writes, shift register ops, `LD`/`ST` data RAM, `SWAP`, `JR`, `CALL imm11`, WAIT conditions, `TMO`, `STAT`; assembler v2; independent ISA model; random differential test |
| 4 Peripherals | SERDES TX/RX, CRC unit |
| 5 Threads | two barrel-interleaved hardware threads |
| 6 Boot ROM v2 | framed protocol, CRC-8, ACK/NAK, UART and parallel loaders, `tools/msf_boot.py` (103 of 128 ROM words) |
| 7 Firmware | UART, SPI modes 0–3, I2C, USB LS TX and RX, 10BASE-T TX, two-thread demo |
| 8 Formal | properties F1–F7, covers reachable, B1–B3 mutants caught; CI workflow |
| 9 Area and timing | 6,893 cells, 24 % of the area; 69 gate levels + 6.4 ns SRAM clk→q, so `CLOCK_PERIOD` relaxed from 20 to 40 ns |
| 10 Docs | ISA v2, datasheet, verification doc; dead files and stale GDS removed |

Deviations found while doing it: TX release mode became "drive the pins only
while a symbol is on the line" (the 10BASE-T test showed the idle pair
still driving); the parallel loader uses a two-phase level handshake (an
edge arriving before the loader re-entered its wait was lost); formal uses
Yosys' SAT engine (SymbiYosys solvers have no Windows wheels).

Out of scope: 10BASE-T receive (needs ≥4x oversampling, 80 MHz), a full USB
device stack, and local P&R (no PDK install; the GDS action is the gate).

Still open then: push so the GDS action hardens v2. It did, first time,
on 17 Oct: 8 666 cells, 23 % utilisation, no timing violations in any
corner, clean DRC and precheck, gate-level test passing (every run since
in `verification.md` §7). Bring-up on silicon remains.

## 7. FPGA (18 Oct)

Muath ported the core to the AUP-ZU3. The PS loader first targeted AXI GPIO,
which the block design didn't have; the same day it moved to PS EMIO GPIO,
the wrapper gained a step mode, and the cocotb firmware tests were ported to
C for the PS (14/14 against the RTL). Muath's 1 Hz blink became the first
board check; a full RTL run measured its period at exactly 6,000,000 cycles.
On 19 Oct the block design validated in Vivado for the first time, but
synthesis crashed inside Vivado itself (a tool fault, not a design error).
Current state: [fpga.md](fpga.md).

## 8. Instruments (19 – 21 Oct)

The survey (`research.md`) ranked what to take from other entries; the
firmware-only items were done without touching the RTL, so the green GDS
stands. They are described in [instruments.md](instruments.md).

* **Targets.** The SPI target answers in the byte right after the command
  although the address's last bit arrives on its last clock: it loads both
  candidate registers early and sends the even one's first bit, swapping in
  the odd one within the turnaround. Every latency was derived from the ISA
  costs first and then asserted at the pins, and each speed limit is tested
  from both sides (16-cycle SCK works, 14 fails).
* **I2C monitor.** The hardest piece, redesigned three times. A first
  version polled the lines after a timed wait and was too slow to return
  for the next rise on a legal 100 kHz bus, so it missed every short low.
  Its replacement queued events at SCL falls and could lose a bit right
  after a reported fault. The rules that worked: catch every edge with a
  level wait; measure from the earliest an edge can have been when it may
  precede its wait (so a legal bus can never be flagged); queue nothing at
  a fall, only after the next rise; and decode the rest of a byte untimed
  after its first fault. A tracer that logs thread 0 instruction by
  instruction against the pad edges found each problem; sweeps of legal,
  single-fault and every-bit-faulty buses set the figures in its header.
* **Fault injection.** Knobs on the existing controllers, kept as
  assembler defines whose defaults leave the tested waveforms unchanged.
  The UART sweep is the check that they are exact: a mid-bit receiver
  fails at precisely the bit times its geometry predicts.
* **Evidence.** `tools/gds_metrics.py` tabulates every CI hardening run;
  `tools/mutation.py` measures how many seeded RTL faults the tests catch.
  Its first run killed 38 of 40. One survivor was a reserved bit nothing
  reads; the other showed the RX tests never loaded the bit timer with
  certain values, and a divisor sweep added for it brought the score to
  39 of 40. `verification.md` §9 maps each claim to its test.

## 9. RTL changes (21 Oct –, branch `rtl-changes`)

A separate line of RTL work, never merged, so the green design stays the
reference. The ideas and why each was worth it are in `research.md`; the
order was: clock, three small changes, a second SRAM bank, a capture
engine, Manchester receive and a glitch filter, more threads.

* **Clock.** The 40 ns runs had 23 ns of setup slack, so the hardening
  constraint went to 20 ns (50 MHz); the demo board can clock a design up
  to 66.5 MHz. It closed with the same 8 666 cells and 3.09 ns to spare
  at the slow corner, no setup or hold violations: the worst path is about
  17 ns, so the v2 logic would run to roughly 59 MHz. Firmware timing is in
  cycles, so every instrument gets faster in proportion.
* **Timeout per thread.** `TMO` was shared, so a timeout thread 0 set also
  cut thread 1's waits short (the reporter retried its `TX_PUSH` for that
  reason). Each thread now has its own, and `TSTART` copies the starter's,
  which keeps every existing program working.
* **FIFO.** `PW` / `PR` take the last two free EXT codes and reach eight
  ports each; port 0 is a FIFO whose words stay in data RAM while its
  positions are in hardware, so a push is a 1-cycle store and a pop a
  2-cycle load (a flip-flop FIFO deep enough for the monitors, 64 words,
  would have cost about 2 000 cells). A first version cleared the drop
  count when read; the SPI monitor's producer and the reporter would both
  read it, so reads have no side effect and drops count modulo 128. The
  monitors and the bridge moved onto it from `ring.inc`:

  | | Before (software queue) | After (FIFO) |
  |---|---|---|
  | `mon_i2c.s` / `mon_spi.s` / `mon_uart.s` / `bridge.s` | 380 / 176 / 184 / 120 words | 319 / 104 / 145 / 81 |
  | SPI monitor, gap between bytes | 60 cycles | 36 |
  | I2C monitor, shortest SCL low it decodes (72-cycle bits) | 34 cycles | 16 |
  | I2C monitor, high a byte's first bit needs for the low after it to be timed | about 56 cycles | 36 (every legal high) |
  | I2C monitor, faults it reports | from a byte's third bit | from its second |

* **Debug view.** `ui_in[2]` high at reset makes `uo_out` show the PC and
  word of the last instruction, the registers, flags and the rest
  (`isa.md` §8). The select comes straight from the pins, not through the
  synchroniser: changing the view between single steps must not need a
  clock edge, and nothing but the output multiplexer sees it.

## Working conventions

* Top module `tt_um_msf`; standard TT pins, every output assigned; `ena`
  ignored; `tiles: "6x4"` in `info.yaml`.
* `info.yaml` `source_files` and `test/Makefile` `PROJECT_SOURCES` list the
  same files (`run_tests.py` reads `info.yaml`).
* RTL: Verilog-2005, one module per file named after it, no latches, async
  active-low reset, explicit widths, unused inputs in an `_unused` reduction.
* Run the full suite after any RTL change; synthesise early and check area.

## Files this log replaces

`docs/sam/`: `arch-compare.md`, `architecture-research.md`,
`competitor-research.md`, `design-questions.md`, `PLAN.md`, `PROGRESS.md`,
`IMPLEMENTATION_PLAN.md`, `project-guide.md`, `ethernet-impl.md`,
`.instructions.md`; `docs/TEST_PLAN_AND_EXTENSIONS.md`;
`docs/shared/drc-analysis.md`; `docs/muath-friend/review.md` and
`updates.md`; `docs/muath/` (three FPGA docs, merged into `fpga.md`). Their
full text is in git at commit `4f14cf5`, e.g.
`git show 4f14cf5:docs/sam/competitor-research.md`.
