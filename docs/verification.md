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
each issue (all four register files, flags, OUT/OE/UO, SR, each thread's TMO,
the running threads,
the FIFO's count, drops and configuration). The programs push to, pop from
and reconfigure the FIFO from both threads, often while it is full or
empty, either thread may set its own TMO, and code and data sit in both
SRAM banks: T1 runs across the 511/512 boundary, calls and returns cross
banks, data goes to 0-127 and 960-1023. A third set of programs runs four
threads: T0 starts T1-T3 and any thread stops or restarts itself or the
others, so the running set and the rotation change all the time. 120
programs per run
(~45 000 instructions); a failure prints the seed (`MSF_SEED=<n>`) and a
disassembly window.

Mutation sanity check: removing the taken-branch penalty from the model makes
the first random program fail at its first branch.

## 3. Formal properties — `formal/run_formal.py`

Properties are in the RTL under `ifdef FORMAL` and checked by bounded model
checking from reset, depth 16, with SymbiYosys and ABC's `bmc3` (native
Yosys from OSS CAD Suite in CI; about 10 minutes for both targets). Until
the four threads, Yosys' own `sat` engine under YoWASP did this; the
four-thread design needs more memory than 32-bit WASM has, and `--engine
sat` remains for smaller runs. A run only counts as passed, failed or caught
on SymbiYosys's own verdict: a tool error is reported as an error. The instruction memory
read data, data-RAM read data, pins and peripheral status are **free
inputs**, so the proofs hold for every program and environment.

| ID | Property |
|---|---|
| F1 | the issuing word was fetched in the previous cycle from the issuing thread's own address |
| F2 | per thread, no instruction is skipped or repeated (independent ghost "next address") |
| F3 | a WAIT completes only by its own event (edge/level/CNT match, re-derived by ghost logic), or by timeout after exactly TMO+1 stalled cycles (single mode) / within one slot of expiry (dual) |
| F4 | only T0 issues in single-thread mode, and only running threads ever own a cycle |
| F5 | pin, SRAM-port, register-file and peripheral side effects only come from an issuing or completing instruction |
| F6 | flags change only through the owning thread's flag ops / blocking ops (or T1's TSTART) |
| F7 | the fetched word comes from the bank (ROM/SRAM) of the address it was fetched with |
| F8 | the FIFO never holds more words than its size; its positions move only by a push that fits, a pop that finds a word, or `FIFO_CFG` |
| F9 | the capture engine never writes SRAM port B in a cycle the core uses it |
| F10 | a `WAIT_CMP` whose target had passed at issue (an independent ghost of isa.md §5's test) releases in the thread's first own stall slot with Z=1, and no running thread stays stalled on one for more than 3 cycles |
| F11 | a thread's LATE flag rises only at such a release in that thread, and falls only by `LATE_CLR` naming it or a `TSTART` of it |

Every `cover()` point (WAIT edge/CMP/timeout release, LD release in T1, dual
issue, TSTOP, all four threads running and issuing, a push to a full FIFO,
a pop of an empty one) is shown
reachable, so the properties are not vacuous.

**Mutation check:** the review bugs are re-introduced into copies of the RTL
and each must make the proof fail:

| Mutant | Caught by |
|---|---|
| B1 pc not advanced on stall release | F1/F2 |
| B2 WAIT_EDGE/WAIT_CMP release conditions mixed | F3 |
| B3 combinational ROM/SRAM bank select | F7 (needs depth >= 14 to reach the boot ROM's `JMP 128`) |
| B4 a late `WAIT_CMP` waits for `CNT` to wrap (the v2.1 behaviour) | F10 |

## 4. Directed and protocol-level tests

| Module | Scope |
|---|---|
| `test_core`, `test_isa_v2` | every instruction, hand-derived expected values, exact cycle costs, input-sync latency; FIFO costs, full / empty / drops / status; both SRAM banks (data, calls, code and branches across 511/512, the MEM_W pointer wrapping at 1023) |
| `test_regressions` | one test per review bug B1-B5 (each failed on v1) |
| `test_peripherals` | CRC check values (CRC-8/16/32/5), TX in every mode decoded from the pins, RX with +-2 % baud and +-1.5 % + jitter, RX at every divisor 4-40, TX->RX loopback; Manchester RX: two frames at +-2 % with +-1 cycle of jitter (re-locking between them) and a TX->RX loopback at 1 Mbit/s; the glitch filter: which pulses pass, its exact delay, and UART bytes with spikes at every bit centre decoded wrongly without it and rightly with it |
| `test_threads` | isolation, start/stop/restart, zero-cost branches, determinism of T0 vs any T1 load, events in the other thread's slot, a TMO per thread, the FIFO between threads, a wait on every pin of both banks in both threads with the other pins high; four threads: one issue in four cycles each, TID and STAT, the cost of changing the running set (derived from the rule), and T0's pin timing unchanged whatever T1-T3 do |
| `test_capture` | the capture engine: every recorded word against a model of the spec's rules (both modes, several sample periods, level and edge triggers, the ring with pre-trigger history, `ui` as source), the core's timing identical with and without recording, the overflow flag, and `logic_analyzer.s`'s UART dump decoded back into the driven samples |
| `test_debug` | the debug view: every view against the program's values (both pages: T2/T3 registers, running and stalled threads, flags, fetch pointers), off without the strap, and single-stepping a hand-driven clock while reading the PC and a register through the pins |
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
same EMIO interface (`python fpga/tests/run_ps_hil.py`, 14/14), and on the
AUP-ZU3 board itself the same 14 tests passed (23 Oct 2026). Details in
[`docs/fpga.md`](fpga.md) and [`fpga/ps/README.md`](../fpga/ps/README.md).

## 6. Pre-hardening check — `tools/synth_check.py`

Maps the RTL onto the IHP `sg13g2_stdcell` liberty (slow corner) with Yosys +
ABC and reports cell count, area against the 24-tile budget and the longest
register-to-register logic depth. Current result: 6 893 cells (823 flip-flops),
93 336 um² + the 88 482 um² SRAM macro = 24 % of the 24-tile area before
routing; 69 gate levels (+ 6.4 ns SRAM clk->q at the slow corner), hence
`CLOCK_PERIOD` = 40 ns. Sign-off remains the LibreLane GDS action in CI.

## 6b. Firmware deadlines — `tools/msf_timing.py`

A pin edge scheduled with `WAIT_CMP` is exact only if the program reaches
the `WAIT_CMP` before its target. Every instruction's cost is fixed by
`isa.md` §5 and §7, so this is decided before the program runs: the tool
assembles a program and explores its control flow with abstract registers
(constants; "now + d" with d an interval, for values read from `CNT`;
unknown). Counted loops unroll exactly because their counters stay
constant; registers that are dead at a program point are dropped, and a
point that collects too many states joins and widens them, so the search
ends. Widening moves a bound that keeps growing to the next of a fixed set
of thresholds (16 apart between -2048 and 2048, powers of two beyond), not
to infinity. With coarser thresholds `can.s` could not be certified at any
bit time: its deadline register is up to ~175 cycles behind "now" just
before the period is added, and rounding that to -256 made the next
deadline look late. At each `WAIT_CMP` it reports the range of d (target minus `CNT`) at
issue over every path; d >= 1 everywhere means the deadline is never missed.
With *n* threads every instruction costs *n* cycles (conservative for code
that runs alone) and the tool also says whether each release is jitter-free.

| Firmware | Threads | d at issue | Worst slack |
|---|---|---|---|
| `uart_tx.s` (115200 at 12 MHz) | 1 | 85..96 | 84 cycles |
| `usb_ls_tx.s` | 1 | 10 | 9 cycles |
| `ws2812.s` | 1 | 636 | 635 cycles |
| `thread_demo.s` | 2 | 492..496, release exact | 491 cycles |
| `ps2_device.s` (10 deadlines) | 2 | 208..472 | 207 cycles |
| `onewire.s` (8 deadlines) | 1 | 64..5756 | 63 cycles |
| `can.s` (6 deadlines, BT = 480) | 2 | 28..444 | 27 cycles |
| `eth_10baset_tx.s` (link-pulse gap) | 1 | 63988..63995 | 63987 cycles |

Checks on the checker, in CI (`test` workflow): `--check-model` runs each
program on the ISA model and every `WAIT_CMP` issue observed must lie inside
the range the analysis computed (the programs that use the SERDES engines,
which the model does not simulate, are skipped); and `uart_tx.s` assembled
with 9 cycles a bit must be rejected (its byte-to-byte path is too long).

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

The clock is 40 ns (25 MHz), so the slow corner has 23 ns to spare. On the
`rtl-changes` branch the same RTL was hardened at 20 ns (50 MHz, run
39633377026): the same 8 666 cells, 3.09 ns of setup slack at the slow
corner, 0.11 ns of hold slack at the fast corner, no violations, no
routing DRC or antenna errors. The constraint stays at 20 ns there. The
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
out, and so is its wrapper, which in silicon is only port wiring. The
tests are first run on the unmutated design, and the run stops if any of
them fails. Each mutant replaces its module and runs against `test_core`,
`test_isa_v2`, `test_threads`, `test_peripherals`, `test_firmware`,
`test_regressions`, `test_boot` and `test_random_diff`, then every other
module; the first module to fail kills it. A test that reads an internal
signal Yosys renamed in the mutant's netlist cannot judge it, so only real
failures count. (Until 24 Oct only the eight modules listed ran, and there
was no baseline run; the v2.0 and v2.1 results below are from that setup.)

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

On the `rtl-changes` branch the run was repeated on the v2.1 RTL (commit
`65da42d`: timeout per thread, FIFO, debug view; a fresh sample of 40):
**37 of 40 killed**. Two survivors cannot change behaviour (a bit of the
constant 1 in the timeout decrement forced to the 0 it already is, and a
data bit on the pin-write path when nothing is written). The third was a
gap: in one thread's wait logic, `ui` pin 7 read inverted whenever `ui`
pin 3 was high, and no test waited on one pin while the others of its
bank were high. `test_wait_edge_every_pin_both_threads` (each thread waits
on all 16 pins in turn with the other seven high) was added and kills it:
38 of 40, the other two equivalent.

On `fixes` (v2.2 RTL with the LATE flag; a fresh sample of 40, seed 1),
the eight modules killed 30 of 40. The ten survivors were re-run against
every module. `test_capture` killed four, all in the capture engine, which
has its own test module that the old list left out. The first re-run also
reported six kills by `test_buses` that were not real: a 1-Wire test was
failing on the unmutated design (it sent the next command while a 64-bit
read was still running), so every mutant failed it too. That is why the
tool now has the baseline run and the full module list. Of those six:

| Mutant | Verdict |
|---|---|
| bit 6 of `a + k` forced to 0 (`tt_um_msf_ctrl.v:122`) | equivalent: the sum is cut to 2 bits |
| bit 31 of the constant 16 in `16*cur` forced to 0 (`tt_um_msf_ctrl.v:154`) | equivalent: it is already 0 |
| a bit of `-(10*tt)` inverted when bit 24 is 0 (`tt_um_msf_ctrl.v:452`) | equivalent: `tt` is 1-3, so the value is negative and bit 24 is 1 |
| `~rx_pop` read as 1 (`tt_um_msf_serdes.v:229`) | a gap: an `RX_POP` on the very cycle the next word lands was flagged as an overrun. `test_rx_pop_as_a_word_lands` moves the pop a cycle at a time across that moment and kills it |
| the filter's bypass for uio pin 1 always on (`tt_um_msf_filt.v:56`) | a gap: only uio2 was ever filtered. `test_filter_every_pin` kills it |
| a bit of the capture tick counter inverted (`tt_um_msf_cap.v:116`) | a gap: the capture tests used `DIV` 1-3, where the counter never has bit 3 set. `test_cap_divisor_sizes` (9 to 4099) kills it |

With the three tests, **37 of 40 are killed**, and the other three cannot
change behaviour.

## 9. Claims and evidence

| Claim | Evidence |
|---|---|
| The RTL implements `isa.md` | an independent Python model, RTL and model compared on every issue of 80 random programs per run (§2); every instruction checked by hand-derived tests (§4) |
| No skipped, repeated or spurious instructions, for any program | formal properties F1-F7 with free memory and pins, all `cover()` points reached; the v1 bugs reinjected are caught (§3) |
| Bit timing is exact and drift free | `test_core` cycle costs; `test_firmware` and `test_faults` measure edges at the pins (UART bits exactly `BIT` cycles for a whole message) |
| No firmware deadline is ever missed, on any path | `tools/msf_timing.py` (§6b): every `WAIT_CMP` in the firmware issues at least 9 cycles before its target; cross-checked against the ISA model; rejects a too-fast UART |
| A missed deadline is never silent | `WAIT_CMP` with a passed target releases at once with Z=1 and sets LATE (isa.md §5): `test_wait_cmp_late_boundaries` (0 and 255 cycles behind are late, 256 behind and 1 ahead are not), `test_late_flags_per_thread`; formal F10, F11 and mutant B4 (§3); random programs with late targets, RTL == model (§2) |
| UART, SPI, I2C, USB LS, 10BASE-T TX run as firmware | `test_firmware` against independent decoders and device models; the same checks in C on the AUP-ZU3 board, 14/14 (§5) |
| The chip can be an SPI device at up to 750 kHz with no dummy byte, and an I2C device at 100 kHz (400 kHz with stretching) | `test_targets`: data in all modes, latencies 4 / 5 / 8 / 13 cycles as derived, a 16-cycle SCK works and 14 fails, 400 kHz needs `STRETCH` |
| A bus monitor never reports a timing fault on a legal I2C bus, and reports the real faults with their lengths | `test_monitors`: random legal buses raise no event; single faults at every data bit, at 100 kHz and the legal minimum, reported once each within the stated accuracy |
| The I2C monitor keeps decoding a master that is out of spec on every bit (lows ≥ 16 cycles, bits ≥ 72) | `test_mon_i2c_fast_master` at the corners of that envelope; a sweep of 196 points inside it |
| The SPI monitor decodes SCK periods of 28 cycles in a byte, 36 between bytes, all modes | `test_mon_spi_limits` at those values |
| The two threads cooperate without locks (UART in, SPI out) | `test_bridge`, all SPI modes |
| Up to four threads run in a fixed rotation; a thread's timing depends only on which threads run | `test_threads` four-thread tests; `test_random_four_threads` (RTL == model on every issue, the running set changing at random); formal F1-F6 and F4 for four threads |
| The hardware FIFO hands words between threads in order, one instruction each way, and never overfills | `test_threads.test_fifo_between_threads`, `test_isa_v2` FIFO tests, formal F8, the random differential test (§2) |
| The RX engine receives Manchester, re-locking after each frame | `test_rx_manchester_frames_jitter` (+-2 % rate, +-1 cycle jitter), `test_rx_manchester_loopback` |
| The glitch filter removes pulses shorter than its length, delays the rest by exactly that, and leaves unfiltered pins alone | `test_filter_removes_short_pulses`, `test_filter_delay`, `test_filter_uart_spikes` |
| The chip records 8 pins as a logic analyser without changing any thread's timing | `test_capture`: every word checked against the spec's rules; the same loop takes the same cycles recording or not; formal F9 |
| A host can halt, single-step and read the core's state through the pins | `test_debug.test_debug_single_step`: hand-driven clock, PC trace and register read between edges |
| Timing faults can be injected by exact amounts | `test_faults`: each knob measured at the pins; a one-cycle step in the UART bit time moves a receiver from pass to fail exactly where its geometry predicts |
| v2.0 fits the 6x4 tiles and meets timing at 25 MHz in all corners, and at 50 MHz, on SG13G2 | the GDS runs above: 23 % utilisation, no setup or hold violations, clean DRC, precheck passed; the 20 ns run on `rtl-changes`: 3.09 ns to spare (§7). The shuttle is CMOS5L, and the four-thread v2.1 build missed 50 MHz at the slow corner: both are being fixed (`docs/plan.md` §1.1-§1.2) |
| The tests catch a broken design, not only pass a working one | 39 of 40 sampled single-bit mutants killed on v2.0, 38 of 40 on v2.1, 37 of 40 on v2.2; every survivor cannot change behaviour (§8) |

What is **not** validated yet:

* Nothing has run on silicon. On the FPGA board the PS steps the core one
  clock at a time, so the board run checks logic and protocols, not timing
  at speed.
* The other end of every bus is a model written by us from the protocol
  specs, not a real device: no USB host, Ethernet link partner, EEPROM or
  logic analyser has been connected.
* Electrical behaviour is not modelled: pad drive, rise times with real
  pull-ups, USB and 10BASE-T signal levels.
* The hardened netlist is only smoke-tested (`test_gl_smoke`: it
  elaborates, its outputs carry no X, reset recovers); its function is
  checked on the RTL.
* The formal proofs are bounded (depth 16 from reset) and cover the
  control pipeline; the SERDES and CRC datapaths are checked in simulation
  only.
* The mutation score comes from a sample of 40 single-bit faults.
