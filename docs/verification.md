# Verification of msfcore

Verification is layered so that no single source of truth checks itself:
the ISA spec (`docs/isa.md`) is implemented twice, independently, as RTL
and as a Python reference model; protocol behaviour is checked by decoders
and slave models written from the protocol specs; and the pipeline invariants
are proved with formal methods for *every* program.

The v1 review bugs that the tests and mutation checks below refer to:

| ID | Bug |
|---|---|
| B1 | the instruction after any `WAIT_*` executed twice |
| B2 | `WAIT_EDGE` and `WAIT_CMP` release conditions were mixed |
| B3 | ROM/SRAM fetch mux used the current address while the data was registered |
| B4 | boot loader corrupted back-to-back UART bytes |
| B5 | boot loader ignored the WAIT timeout (host starting late gave garbage) |
| B6 | no input synchronisers |
| B7 | boot loader terminator `0x0000` was a valid instruction; no CRC or ACK |
| B8 | the old golden model mirrored the RTL, including B1 |
| B9 | the gate-level SRAM model zeroed `DOUT` when idle; the IHP macro holds it |

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
| `test_peripherals` | CRC check values (CRC-8/16/32/5), TX in every mode decoded from the pins, RX with +-2 % baud and +-1.5 % + jitter, RX at every divisor 4-40, TX->RX loopback |
| `test_threads` | isolation, start/stop/restart, zero-cost branches, determinism of T0 vs any T1 load, events in the other thread's slot |
| `test_boot` | framed UART and parallel loading, back-to-back bytes, late host, NAK + retry, data RAM, `G` anywhere |
| `test_firmware` | the `firmware/` demos against independent decoders and slave models (SPI slave all modes, wired-AND I2C slave with stretching, USB NRZI/CRC16/EOP, 10BASE-T Manchester/FCS/TP_IDL/NLP) |
| `test_protocols`, `test_extensions`, `test_reprogrammable`, `test_crv_invariants` | the original v1 bit-bang demos and constrained-random invariants, kept as regression |
| `test_golden_model` | lock-step RTL vs model on a fixed image; protocol/CRC reference self-checks |
| `test_targets` | the chip as an SPI and an I2C device against controller models: data, response latencies at the pins against the values derived from the ISA costs, speed limits (a faster bus must fail) |
| `test_monitors` | UART, I2C and SPI bus monitors: decoding, timing faults reported with their lengths, no event on legal buses (random phases), limits |
| `test_bridge` | UART to SPI bridge across the two threads, all SPI modes, against a shift-register device model |
| `test_faults` | the controllers' timing knobs measured at the pins; a UART bit-time sweep finds a receiver's theoretical limits |

The instruments these test are described, with their figures, in
[`instruments.md`](instruments.md). Firmware tests run against models
stepped one clock at a time beside the RTL (`test/fw_tb.py`), the same
lock-step idea as the FPGA harness below.

## 5. FPGA — `fpga/tests/`

The same `test_firmware.py` checks run on the FPGA build: the Zynq PS steps
the core one clock at a time and C ports of the protocol models act as the
other end of each bus. In simulation the C runs against the RTL through the
same EMIO interface (`python fpga/tests/run_ps_hil.py`, 14/14). Details in
[`docs/fpga.md`](fpga.md) and [`fpga/ps/README.md`](../fpga/ps/README.md).

## 6. Pre-hardening check — `tools/synth_check.py`

Maps the RTL onto the IHP `sg13g2_stdcell` liberty (slow corner) with Yosys +
ABC and reports cell count, area against the 24-tile budget and the longest
register-to-register logic depth. Current result: 6 893 cells (823 flip-flops),
93 336 um² + the 88 482 um² SRAM macro = 24 % of the 24-tile area before
routing; 69 gate levels (+ 6.4 ns SRAM clk->q at the slow corner), hence
`CLOCK_PERIOD` = 40 ns. Sign-off remains the LibreLane GDS action in CI.

## 7. Hardening runs in CI — `tools/gds_metrics.py`

Every push runs the Tiny Tapeout GDS action (LibreLane on IHP SG13G2, then
precheck and a gate-level test). `tools/gds_metrics.py` downloads each
run's metrics with the GitHub CLI and tabulates them. The v2 RTL has
hardened in every run since it landed, with the same result each time (the
later commits changed firmware, tests and docs only):

| Run | Date | Branch | Commit | Std cells | Util. | Setup slack, slow corner | Hold slack, fast corner | Setup / hold violations | Route DRC | Antenna |
|---|---|---|---|---|---|---|---|---|---|---|
| 39164070033 | 18 Oct | FPGA | 0646361 | 8 666 | 23.2 % | 23.09 ns | 0.11 ns | 0 / 0 | 0 | 0 |
| 39163726286 | 18 Oct | FPGA | 71bd90a | 8 666 | 23.2 % | 23.09 ns | 0.11 ns | 0 / 0 | 0 | 0 |
| 39160768857 | 18 Oct | FPGA | 4f14cf5 | 8 666 | 23.2 % | 23.09 ns | 0.11 ns | 0 / 0 | 0 | 0 |
| 39157643790 | 18 Oct | FPGA | 662398d | 8 666 | 23.2 % | 23.09 ns | 0.11 ns | 0 / 0 | 0 | 0 |
| 39148468747 | 18 Oct | FPGA | 492507e | 8 666 | 23.2 % | 23.09 ns | 0.11 ns | 0 / 0 | 0 | 0 |
| 39022166820 | 17 Oct | main | a7a9b86 | 8 666 | 23.2 % | 23.09 ns | 0.11 ns | 0 / 0 | 0 | 0 |
| 39017713663 | 17 Oct | ethernet | a7a9b86 | 8 666 | 23.2 % | 23.09 ns | 0.11 ns | 0 / 0 | 0 | 0 |

The clock is 40 ns (25 MHz), so the slow corner has 23 ns to spare. The
precheck (KLayout SG13G2 DRC, pin, boundary, layer and cell-name checks)
passed in each run. LibreLane also warns of 16 max-slew and 58 max-fanout
nets at the slow corner; they are design-rule warnings, not timing
violations, and do not fail sign-off. v1 never hardened: the runs before
17 Oct failed (`history.md` §5).

## 8. Mutation score — `tools/mutation.py`

The sections above show that the tests pass; this measures whether they
would fail on a broken design. Yosys (`mutate -list`, seed 1) samples 40
single-bit faults spread over the eight logic modules: a bit inverted, tied
to 0 or 1, or inverted only while another bit is set. The SRAM macro is left
out, and so is its wrapper, which in silicon is only port wiring. Each
mutant replaces its module and runs against `test_core`, `test_isa_v2`,
`test_threads`, `test_peripherals`, `test_firmware`, `test_regressions`,
`test_boot` and `test_random_diff` in that order; the first module to fail
kills it. A test that reads an internal signal Yosys renamed in the
mutant's netlist cannot judge it, so only real failures count. A run takes
about 20 minutes.

Result (21 Oct): **38 of 40 killed**. The first module to fail was
`test_core` for 14, `test_peripherals` 14, `test_isa_v2` 4,
`test_random_diff` 4, `test_threads` 1 and `test_firmware` 1. The random
differential test runs last, so each of its four kills got past every
directed module first.

| Survivor | Verdict |
|---|---|
| `rx_cfg[3]` inverted on its hold path (`tt_um_msf_serdes.v:137`) | equivalent: bit 3 of `RX_CFG` is reserved (`isa.md` §6.2) and nothing reads it |
| `rx_tmr[15]` read as `rx_tmr[15] ^ rx_tmr[4]` (`tt_um_msf_serdes.v:137`) | a gap in the tests. The RX bit timer then runs for about 32 000 extra cycles whenever it is loaded with an even value that has bit 4 set; the RX tests' divisors (104, 8, 6) never load one. `test_rx_uart_divisor_sweep` (every divisor from 4 to 40) was added and kills it |

With the sweep, 39 of the 40 are killed, and the one left cannot change
behaviour. Forty mutants are a sample, not every bit of the design.
`--src <location>` re-runs only the sampled mutants at one location, which
is how the sweep was checked against the survivor.

## 9. Claims and evidence

| Claim | Evidence |
|---|---|
| The RTL implements `isa.md` | an independent Python model, RTL and model compared on every issue of 80 random programs per run (§2); every instruction checked by hand-derived tests (§4) |
| No skipped, repeated or spurious instructions, for any program | formal properties F1-F7 with free memory and pins, all `cover()` points reached; the v1 bugs reinjected are caught (§3) |
| Bit timing is exact and drift free | `test_core` cycle costs; `test_firmware` and `test_faults` measure edges at the pins (UART bits exactly `BIT` cycles for a whole message) |
| UART, SPI, I2C, USB LS, 10BASE-T TX run as firmware | `test_firmware` against independent decoders and device models; the same checks in C on the FPGA build (§5) |
| The chip can be an SPI device at up to 750 kHz with no dummy byte, and an I2C device at 100 kHz (400 kHz with stretching) | `test_targets`: data in all modes, latencies 4 / 5 / 8 / 13 cycles as derived, a 16-cycle SCK works and 14 fails, 400 kHz needs `STRETCH` |
| A bus monitor never reports a timing fault on a legal I2C bus, and reports the real faults with their lengths | `test_monitors`: random legal buses raise no event; single faults at every data bit, at 100 kHz and the legal minimum, reported once each within the stated accuracy |
| The I2C monitor keeps decoding a master that is out of spec on every bit (lows ≥ 34 cycles, bits ≥ 72) | `test_mon_i2c_fast_master` at the corners of that envelope |
| The SPI monitor decodes SCK periods of 28 cycles in a byte, 60 between bytes, all modes | `test_mon_spi_limits` at those values |
| The two threads cooperate without locks (UART in, SPI out) | `test_bridge`, all SPI modes |
| Timing faults can be injected by exact amounts | `test_faults`: each knob measured at the pins; a one-cycle step in the UART bit time moves a receiver from pass to fail exactly where its geometry predicts |
| v2 fits the 6x4 tiles and meets timing at 25 MHz in all corners | the GDS runs above: 23 % utilisation, no setup or hold violations, clean DRC, precheck passed (§7) |
| The tests catch a broken design, not only pass a working one | 39 of 40 sampled single-bit mutants killed; the survivor flips a reserved bit nothing reads (§8) |
