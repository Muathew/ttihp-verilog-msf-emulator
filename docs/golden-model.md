# Verification of msfcore

Verification is layered so that no single source of truth checks itself:
the ISA spec (`docs/sam/isa.md`) is implemented twice, independently, as RTL
and as a Python reference model; protocol behaviour is checked by decoders
and slave models written from the protocol specs; and the pipeline invariants
are proved with formal methods for *every* program.

## 1. Independent ISA reference model — `test/isa_model.py`

Written from `isa.md`, not from the RTL. It has no fetch pipeline, no flush
flag and no pc-hold trick: only instruction semantics plus the documented
cost rules (1 cycle per instruction, 2 for a taken redirect, slot
alternation in dual-thread mode, blocking ops that release in their own slot,
inputs visible two cycles after the pad). It is stepped one clock at a time,
so per-cycle events (edges, cycle-counter matches, timeouts, CRC busy) are
exact.

*Why it replaced the old `MsfCoreModel`:* the old model was a line-by-line
transliteration of the RTL pipeline. It reproduced bug B1 (the instruction
after a WAIT executing twice) and could therefore never find it.

## 2. Random differential testing — `test/test_random_diff.py`

`rand_prog.py` generates constrained-random programs: bounded loops, forward
skips, `CALL`/`RET` leaf subroutines, `JR`, WAITs with random input activity
and small timeouts, data-RAM traffic, shift-register and pin ops, CRC bursts,
and in dual mode thread start / stop / restart from either thread. Each
program runs on the RTL and on the model with the same per-cycle pad
schedule; `diffcheck.py` compares **every** issue and stall-release event
(cycle, thread, PC, instruction word) and the full architectural state at
each issue (both register files, flags, OUT/OE/UO, SR). 80 programs per run
(~45 000 instructions); a failure prints the seed (`MSF_SEED=<n>`) and a
disassembly window.

Mutation sanity check: removing the taken-branch penalty from the model makes
the first random program fail at its first branch.

## 3. Formal properties — `formal/run_formal.py`

Properties are in the RTL under `ifdef FORMAL` and checked with Yosys' SAT
engine (bounded model checking from reset, depth 16). The instruction memory
read data, data-RAM read data, pins and peripheral status are **free
inputs**, so the proofs hold for every program and environment.

| ID | Property |
|---|---|
| F1 | the issuing word was fetched in the previous cycle from the issuing thread's own address |
| F2 | per thread, no instruction is skipped or repeated (independent ghost "next address") |
| F3 | a WAIT completes only by its own event (edge/level/CNT match, re-derived by ghost logic), or by timeout after exactly TMO+1 stalled cycles (single mode) / within one slot of expiry (dual) |
| F4 | thread T1 never issues in single-thread mode |
| F5 | pin, SRAM-port, register-file and peripheral side effects only come from an issuing or completing instruction |
| F6 | flags change only through the owning thread's flag ops / blocking ops (or T1's TSTART) |
| F7 | the fetched word comes from the bank (ROM/SRAM) of the address it was fetched with |

Every `cover()` point (WAIT edge/CMP/timeout release, LD release in T1, dual
issue, TSTOP) is shown reachable, so the properties are not vacuous.

**Mutation check:** the review bugs are re-introduced into copies of the RTL
and each must make the proof fail:

| Mutant | Caught by |
|---|---|
| B1 pc not advanced on stall release | F1/F2 |
| B2 WAIT_EDGE/WAIT_CMP release conditions mixed | F3 |
| B3 combinational ROM/SRAM bank select | F7 (needs depth >= 14 to reach the boot ROM's `JMP 128`) |

## 4. Directed and protocol-level tests

| Module | Scope |
|---|---|
| `test_core`, `test_isa_v2` | every instruction, hand-derived expected values, exact cycle costs, input-sync latency |
| `test_regressions` | one test per review bug B1-B5 (each failed on v1) |
| `test_peripherals` | CRC check values (CRC-8/16/32/5), TX in every mode decoded from the pins, RX with +-2 % baud and +-1.5 % + jitter, TX->RX loopback |
| `test_threads` | isolation, start/stop/restart, zero-cost branches, determinism of T0 vs any T1 load, events in the other thread's slot |
| `test_boot` | framed UART and parallel loading, back-to-back bytes, late host, NAK + retry, data RAM, `G` anywhere |
| `test_firmware` | the `firmware/` demos against independent decoders and slave models (SPI slave all modes, wired-AND I2C slave with stretching, USB NRZI/CRC16/EOP, 10BASE-T Manchester/FCS/TP_IDL/NLP) |
| `test_protocols`, `test_extensions`, `test_reprogrammable`, `test_crv_invariants` | the original v1 bit-bang demos and constrained-random invariants, kept as regression |
| `test_golden_model` | lock-step RTL vs model on a fixed image; protocol/CRC reference self-checks |

## 5. Pre-hardening check — `tools/synth_check.py`

Maps the RTL onto the IHP `sg13g2_stdcell` liberty (slow corner) with Yosys +
ABC and reports cell count, area against the 24-tile budget and the longest
register-to-register logic depth. Current result: 6 893 cells (823 flip-flops),
93 336 um² + the 88 482 um² SRAM macro = 24 % of the 24-tile area before
routing; 69 gate levels (+ 6.4 ns SRAM clk->q at the slow corner), hence
`CLOCK_PERIOD` = 40 ns. Sign-off remains the LibreLane GDS action in CI.
