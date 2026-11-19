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
| F9 | the capture engine never writes SRAM port B in a cycle the core uses it (also checked in the capture module alone, with the core's use of port B free) |
| F10 | a `WAIT_CMP` whose target had passed at issue (an independent ghost of isa.md §5's test) releases in the thread's first own stall slot with Z=1, and no running thread stays stalled on one for more than 3 cycles |
| F11 | a thread's LATE flag rises only at such a release in that thread, and falls only by `LATE_CLR` naming it or a `TSTART` of it |
| F12 | the rotation skips no running thread: each owns at least one cycle in any four |
| F13 | a thread's timing depends only on which threads run: in a cycle with no `TSTART` or `TSTOP` the running set stays, and in multi-thread mode the next owner follows from this owner and that set alone, with no bubble. It says nothing about shared memory, pins or peripherals |
| F14 | (v3) the decode stage and its forwarding give the issuing instruction its registers as they are now: both operands equal a shadow of the four register files kept from the writes alone |
| F15 | (v3) the late guard: from the cycle after a guarded thread's late `WAIT_CMP` released, the guard's pins are off the bus (their OE forced to 0), and they stay off while that LATE flag is set; with no guarded flag set no pin is held off |
| F16 | (v3, target `ni`, run by `--top ni`) thread non-interference on two copies of the core (`formal/ni_miter.v`): both get the same inputs; T0 and one more thread *k* run the same program in both, the other threads anything that touches no shared state (pins, SR, MEM_W pointer, FIFO, engines, ports, `TSTART`/`TSTOP`), each copy its own. Then the slot owner is the same in both copies every cycle, T0 and *k* issue in the same cycles the same words from the same addresses with the same operands, and the pins are the same. `LATE_RD`, which reads every thread's flag, is left out on purpose |

| F17 | (v4, `ctrl` target) timed pins, for every program (isa.md 6.8): a pin write that the slot accepts as held is applied in the cycle `CNT` reaches its time and in no other, as the operation it was; a refused write (time passed, or the slot occupied) makes no write, sets Z = 1 and the thread's LATE flag, and a held or refused write makes none now. The ghost restates the rule with a down counter and its own copy of the `AT` operand, not with the design's compare |
| F18 | (v4, `top` target) the pad registers (`uio_out`, `uio_oe`, `uo_out`) hold in every cycle what the old combinational expressions give (`(OUT & ~mask) \| (val & mask)`, `OE & ~guard`): registering the pads moved no edge |

F5 was extended (a timed event writes the pins in any cycle: `exec` or a due event), F11 too
(a LATE flag also rises at a refused timed write). **None of F1-F18 has been run on v4**: the
YoWASP Yosys on the development PC aborts inside `sat` (on v3 too) and has no `yosys-abc`, so
`formal/parse_check.py` (new) only shows that all four targets elaborate; the `formal` workflow
runs the proofs. See `v4-open-problems.md`.

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
| a push into a full FIFO moves its write position | F8 (alone, at depth 7) |
| the capture engine writes port B while the core uses it | F9, in the capture module (at the top level a program cannot start a capture within depth 16: the boot ROM runs first) |
| the rotation skips the thread after the owner | F1 first (depth 4); F12 alone catches it too (depth 6) |
| a stalled thread holds on to the next slot | F13 (`run_formal.py`'s mutant list) |
| no forwarding into E (v3) | F14 |
| the guard ignores which threads it names (v3) | F15 |
| a held timed event is applied one cycle late (v4) | F17. **Not shown**: the proof has not run (`v4-open-problems.md` A1); the mutant's text anchor resolves |
| a refused timed write still goes out (v4) | F17. Not shown, as above |
| a held timed write forgets its operation (v4) | F17. Not shown, as above |
| the pad registers load the old values instead of the next ones (v4; `--top top`) | F18. Not shown, as above |
| `STAT` shows any thread's LATE flag, not the reader's (v3; `--top ni`) | F16 is meant to: no single-copy property sees it. **Shown on v4 (13 Nov):** `run_formal.py --engine abc --depth 16 --top ni --only-mutant 0` catches it, F16 at `ni_miter.v:186`, step 8, in 11 minutes (at depth 5 it passes, 9 Nov). The 15 cycles this row used to give as the reach was an overestimate. This shows F16 can fail; the unmutated `ni` run to depth 16 has still not been done |

A mutant stops at its first counterexample, so the cheap targets go first
(controller, capture module, top level).

**For all time** (`--prove`, ABC `pdr`, which proves each assertion
separately): on the controller, 31 of its 35 assertions are proved for
every reachable state, not just to depth 16. 30 of them took under 7
minutes; F8's `fcount <= fsize` took 3.4 hours. Proved: F1, F2, F3 for
event releases, F4, F5, F6, F8, F11, and F10's bounds on when a late
`WAIT_CMP` releases and that no running thread stays stalled on one. Still
open after 5 hours, and only bounded so far: F3's three timeout
assertions (a timeout releases after exactly TMO+1 stalled cycles, or
within 3 of it with several threads) and F10's first (a late release is a
`WAIT_CMP` release, not a timeout or an event). All four relate the 17-bit
ghost stall counter to the 16-bit timeout; PDR would need an invariant
tying the two. Helper invariants for it, per thread (the stall's kind,
lateness and target, the timeout counter plus the ghost count equal to
TMO, the overrun bounded by F12's gap), did not get there: with them PDR
proved 61 of 87 assertions in an hour, neither the four nor most of the
helpers, and k-induction (SMT, depth 4) fails its step even on F3's event
release, which PDR proves alone. They were left out; the four stay bounded
(depth 16 in CI). `--prove-timeout S` stops each target after S seconds and
lists which assertions were proved and which are open. The top level (F7,
F9 and the controller inside the whole design) has not been run for all
time yet: `--prove` reaches it only after the controller, which does not
finish; `--top top` runs it alone.

## 4. Directed and protocol-level tests

| Module | Scope |
|---|---|
| `test_core`, `test_isa_v2` | every instruction, hand-derived expected values, exact cycle costs, input-sync latency; FIFO costs, full / empty / drops / status; all four SRAM banks (v4: data in banks 2 and 3, calls, `JR` and code across 511/512, 1023/1024 and 1535/1536, a thread started above 1024, the MEM_W pointer wrapping at 2047, a FIFO and the capture buffer above 1023) |
| `test_regressions` | one test per review bug B1-B5 (each failed on v1) |
| `test_v4_fixes` (v4) | the RTL review's fixes: the store-ahead rule on the model (`StoreAheadError` at W+1 and W+2) and on the RTL (the detector in `tb.v` sees the old word run at W+2; W+3 and, with two threads, W+2 run the new word, RTL == model), the reset at any phase of its release and asserted with no clock edge, the debug strap loaded by a two-edge reset, the pads equal the old combinational expressions in every cycle through three engine modes, OE writes and the late guard, the assembler's refusal of `TSTART #4`-`#7` |
| `test_timed_pins` (v4) | `AT`/`TS`/`ESEL`: the pad changes in cycle T+1 with one to four threads running (one stalled, one loading, one counting); the reply idiom `WAIT_EDGE; TS; ADDS; AT; write` answers exactly delta cycles after the edge for four edge phases against the thread slots and one to four threads (TS = E+2, reply in E+delta+3); a passed time is refused (Z = 1, LATE, no pin change) and a time that is exactly now is applied at once; four events in one cycle, T3 first and T0 last (RTL == model); a second timed write finds the slot occupied; `TSTOP` cancels a held event and `TSTART` puts a thread back on page 0; a held `PIN_W` lands as a write, not a set; the deadline checker certifies a reply with room and refuses one without |
| `test_engine_sets` (v4) | two CRC units at once (CRC-32 and CRC-16, each against a bit-serial reference, neither disturbed by the other); two UARTs at once from two threads, one TX engine each, decoded from the pins; TX on `uo[3]` received on `ui[5]` by the other set's RX engine; set 1 wins a shared pin, `STAT` and `TX_WAIT` follow the thread's own set |
| `test_engine_options` (v4) | page 2: TX stuffing for HDLC / CAN kinds against a reference, RX de-stuffing and E_STUFF, E_FRAME with the stop-bit check and `ERR_CLR`, E_UNDER with `stream`, STAT bit 15; the external clock: RX in all four SPI modes at 8 and 4 cycles a bit with a partial word dropped when CS rises, TX as an SPI slave (modes 0 and 1, two words, pin released while CS is high), and the measured speed limits (RX 2 cycles a bit, TX 6) |
| `test_peripherals` | CRC check values (CRC-8/16/32/5), TX in every mode decoded from the pins, RX with +-2 % baud and +-1.5 % + jitter, RX at every divisor 4-40, TX->RX loopback; Manchester RX: two frames at +-2 % with +-1 cycle of jitter (re-locking between them) and a TX->RX loopback at 1 Mbit/s; the glitch filter: which pulses pass, its exact delay, and UART bytes with spikes at every bit centre decoded wrongly without it and rightly with it |
| `test_threads` | isolation, start/stop/restart, zero-cost branches, determinism of T0 vs any T1 load, events in the other thread's slot, a TMO per thread, the FIFO between threads, a wait on every pin of both banks in both threads with the other pins high; four threads: one issue in four cycles each, TID and STAT, the cost of changing the running set (derived from the rule), and T0's pin timing unchanged whatever T1-T3 do |
| `test_capture` | the capture engine: every recorded word against a model of the spec's rules (both modes, several sample periods, level and edge triggers, the ring with pre-trigger history, `ui` as source), the core's timing identical with and without recording, the overflow flag, and `logic_analyzer.s`'s UART dump decoded back into the driven samples |
| `test_debug` | the debug view: every view against the program's values (both pages: T2/T3 registers, running and stalled threads, flags, fetch pointers), off without the strap, and single-stepping a hand-driven clock while reading the PC and a register through the pins |
| `test_boot` | framed UART and parallel loading, back-to-back bytes, late host, NAK + retry, data RAM, `G` anywhere |
| `test_firmware` | the `firmware/` demos against independent decoders and slave models (SPI slave all modes, wired-AND I2C slave with stretching, USB NRZI/CRC16/EOP, 10BASE-T Manchester/FCS/TP_IDL/NLP) |
| `test_protocols`, `test_extensions`, `test_reprogrammable`, `test_crv_invariants` | the original v1 bit-bang demos and constrained-random invariants, kept as regression |
| `test_golden_model` | lock-step RTL vs model on a fixed image; protocol/CRC reference self-checks |
| `test_targets` | the chip as an SPI and an I2C device against controller models: data, response latencies at the pins against the values derived from the ISA costs, speed limits (a faster bus must fail); the board's MCU peer with the MSPM0's timing: one whole pass, limits, every I2C hold, floating pins, a MOSI that only follows MISO |
| `test_monitors` | UART, I2C and SPI bus monitors: decoding, timing faults reported with their lengths, no event on legal buses (random phases), limits |
| `test_bridge` | UART to SPI bridge across the two threads, all SPI modes, against a shift-register device model |
| `test_faults` | the controllers' timing knobs measured at the pins; a UART bit-time sweep finds a receiver's theoretical limits |
| `test_usb_fs`, `test_eth10_rx` (v3) | USB full speed both ways at 48 MHz (4 cycles a bit) and 10BASE-T receive at 50 MHz (RX mode 3), against peers written from the specifications: every edge placed in continuous time (random phase, the rate at the spec's limits, edge jitter: 8 ns for USB, 10 ns for 10BASE-T) and seen from the next clock, as the input synchroniser sees it; sigrok's `usb_signalling` reads both USB packets |

The instruments these test are described, with their figures, in
[`instruments.md`](instruments.md). Firmware tests run against models
stepped one clock at a time beside the RTL (`test/fw_tb.py`), the same
lock-step idea as the FPGA harness below.

The random differential tests (v4) also put in "patch" stores into code at exactly the
allowed distance, timed-pin windows (`ESEL #2`, `AT` with times that are ahead, now and behind,
the reply idiom, two windows sharing a slot), CRC bursts on engine set 1 in T1's code, and code
and data in all four banks (threads at 1020 and 1530, across bank boundaries).

`test_guard` (v3) checks the late guard: one thread guarding its own pins, a
guard naming only another thread leaving them alone, and one thread guarding
another's pins; random-diff compares OE at the pads with random guard loads in
its programs.

**`tools/devsim.py`** runs a program on the ISA model against the device a test
models (the test's own class: `python tools/devsim.py sdspi.s test_sdspi Card
600000`), in seconds instead of the simulator's minutes. It found five of batch
5's six failures on 8 Nov (an end marker equal to a pixel's first word, a
preamble refilled from a pin still driven low, a bit-7 test made on bit 15, a
bit-15 flag tested by doubling, a burst longer than its time-out). The model's
pins change a cycle before the RTL's, so it settles logic, and the RTL test
settles timing.

## 5. FPGA — `fpga/tests/`

The same `test_firmware.py` checks run on the FPGA build: the Zynq PS steps
the core one clock at a time and C ports of the protocol models act as the
other end of each bus. In simulation the C runs against the RTL through the
same EMIO interface (`python fpga/tests/run_ps_hil.py`): 67 tests pass. On the
AUP-ZU3 board itself the first 14 passed on 23 Oct 2026, and all 67 in a later
run, after 29 Oct. Each test prints an execution digest, a CRC-32 of every core
cycle's pins as the PS saw them; it does not depend on the PS's speed (the same
at 2 and 7 PL clocks per GPIO access), so a board log can be compared with the
simulation's test by test. That run's full log was not kept, but a screenshot
shows its last 27 digests. The 11 shortest were simulated on 6 Nov from the
current `fpga/rtl`, and all 11 match.
Details in [`docs/fpga.md`](fpga.md) and
[`fpga/ps/README.md`](../fpga/ps/README.md).

One board test works differently: the core runs free at 12 MHz and a real
LP-MSPM0G3519 drives its UART, I2C and SPI. An early run on firmware v2 counted
60 SPI bytes that no pass sent, which led to firmware v3. With v3 (6 Nov), the
on-chip logic analyser showed a correct UART, SPI and I2C pass on the wires, and
the MCU's own I2C test turned out to read each byte a pass late. With that test
fixed, UART, SPI and I2C passed at both ends on all 148 passes, from pass 1, with
no bad bytes at the FPGA
([`external-mcu_testing.md`](../external-mcu_testing.md)).

## 6. Pre-hardening check — `tools/synth_check.py`

Maps the RTL onto the IHP `sg13cmos5l_stdcell` liberty (slow corner;
`sg13g2_stdcell` works too) with Yosys + ABC and reports cell count, area
against the 24-tile budget and the longest register-to-register logic depth,
in a minute and before any layout. On 28 Oct (the RTL of 6ea46bd): 11 534
cells (1 604 flip-flops), 173 670 µm² + the two SRAM macros' 176 963 µm² =
46.6 % of the 24-tile area before routing; 59 gate levels plus the SRAM's
clock-to-output. An earlier RTL on SG13G2 gave 6 893 cells, 823 flip-flops
and 69 levels, which set v2.0's 40 ns clock. It is an estimate only: the
clock is signed off by LibreLane's STA on the routed layout (§7).

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

v4: the checker follows `ESEL` (the port page decides what `PW`/`PR` mean and what they
cost), keeps the `TS` of the last wait as a time relative to now (set the way the waited-on
register of a `WAIT_CMP` is) and the time `AT` armed. A pin write issued with `AT` armed is a
deadline: it needs its time 0 to 65,280 cycles ahead when it issues, or the hardware refuses
it. The report gives the slack (d at issue) and "on its cycle whatever the other threads do";
a time it cannot derive is refused with the reason. For a reply `WAIT_EDGE; TS; LDR; ADDS; AT;
write` with four threads it certifies d = 17..20 at the write for delta 40, which is what the
RTL test measures.

Checks on the checker, in CI (`test` workflow): `--check-model` runs each
program on the ISA model and every `WAIT_CMP` issue observed must lie inside
the range the analysis computed (the programs that use the SERDES engines,
which the model does not simulate, are skipped); and `uart_tx.s` assembled
with 9 cycles a bit must be rejected (its byte-to-byte path is too long).

## 6c. sigrok's decoders — `tools/sigrok_check.py`

The peer models in the tests were written with the same AI as the firmware, so a shared
misreading of a protocol would pass every test. sigrok's decoders were written by other
people, for real captures. The firmware tests save their pins (`save_trace`), and this runs
`sigrok-cli` on them, in CI: UART, SPI in all four modes, I2C, USB low speed with its CRC16,
CAN with arbitration, ACK and data, 1-Wire down to the ROM and scratchpad, PS/2, JTAG's
IDCODE, SWD down to the DPIDR, WS2812 colours, and what §12 of `instruments.md` learned, on
sigrok's `ir_nec`. Each must print exactly what its test sent.

Each also has a tooth: the same waveform with one edge held back must read differently. An
edge in an idle gap changes nothing a decoder cares about, so it tries edges from the middle
of the activity outwards, and fails if none of the first eight bites.

It found no bug in the firmware. It did find one in sigrok: its PS/2 decoder's `handle_bits`
takes a twelfth falling edge to end a frame of eleven, so it eats the next frame's start bit
and reads every later frame one bit off. Our frames are right, as the bits at the falling
edges show; that trace gets one extra CLK pulse in the idle gap after each frame.

## 7. Hardening and sign-off — `tools/check_signoff.py`

Every push runs the Tiny Tapeout GDS action: LibreLane on IHP SG13CMOS5L
(the shuttle's process), then TT's precheck, a gate-level test and our
sign-off check. The action stays green with timing violations, so
`tools/check_signoff.py` reads LibreLane's metrics and fails unless setup
and hold slack are at least 0 in all three corners (slow 1.08 V 125 °C,
typical 1.20 V 25 °C, fast 1.32 V −40 °C), with no setup or hold
violations and no routing DRC, antenna or LVS errors. Max-slew and max-cap
violations are printed as warnings.

**The clock is signed off at 30 ns (33 MHz) in every corner**
(`src/signoff.sdc`), while placement and routing aim at 20 ns
(`src/pnr.sdc`). The worst path is an SRAM read (6.7 ns clock-to-output in
the slow library) through decode and the ALU into the register file.
Aiming at 30 ns, the tools left it at 33.2 ns; aiming at 20 ns, they make
it 27.5–28.4 ns, which meets 30 ns. At the typical corner the same layout
has about 10.7 ns to spare at 30 ns (a path of about 19.3 ns, so 50 MHz on
typical silicon), but only the slow corner is promised.

| Run | Commit | Std cells | Util. | Setup slack at 30 ns, slow / typical | Hold slack, fast | Setup / hold violations | Max-slew / max-cap warnings | Route DRC, antenna, LVS | Signed off |
|---|---|---|---|---|---|---|---|---|---|
| CI 40079395684 | 1cc1b5b | 16 620 | 45.2 % | −3.21 / +9.51 ns | 0.11 ns | 331 / 0 | 131 / 27 | 0, 0, 0 | no: placed and routed at 30 ns |
| CI 40084290148 | 44a7d31 | 16 662 | 45.3 % | +1.61 / +9.37 ns | 0.11 ns | 0 / 0 | 113 / 28 | 0, 0, 0 | yes |
| CI 40085473871 | 6ea46bd | 16 605 | 45.3 % | +2.17 / +10.72 ns | 0.11 ns | 0 / 0 | 114 / 32 | 0, 0, 0 | yes |
| local S | 44a7d31 + design repair | 18 023 | 47.0 % | +2.49 / +10.64 ns | 0.07 ns | 0 / 0 | 6 / 2 | 0, 0, 0 | yes |
| local F | 6ea46bd's RTL + design repair (`src/config.json`) | 18 220 | 47.3 % | +1.24 / +11.02 ns | 0.07 ns | 0 / 0 | 2 / 2 | 0, 0, 0 | yes |
| **CI 42393871451, the shuttle's flow** | df70880: `tt-gds-action@ttihp27a`, LibreLane 3.1.0.dev4, PDK `89d42ee6`, `SYNTH_STRATEGY` pinned | 17 245 | 46.5 % | **+3.62 / +11.93 ns** | 0.08 ns | 0 / 0 | 3 / 1 | 0, 0, 0 | yes |

The table above is v2.2 on 6x4. **v4 on 8x4 is signed off at 20 ns (50 MHz) in every corner**
(CI 44499734618, dfa4c86, 19 Nov; placement and routing aim at 18 ns): setup slack slow
+3.36 ns, typical +9.69 ns, fast +12.84 ns; hold +0.26 ns at the fast corner; 62.7 %
utilisation; routing DRC, antenna and LVS clean; precheck, the gate-level tests and
`netlist_equiv.py` pass, and `check_signoff.py` prints "signed off". Two changes got it there
([`v4-open-problems.md`](v4-open-problems.md) B): the boot ROM registers its address rather than
its word (784a270), which takes the 128-word lookup out of the fetch cycle, and the wire model
before extraction uses 1.95 times the tech LEF's capacitance (`LAYERS_RC` in `src/config.json`),
because extraction gives 0.18 fF/µm against the LEF's 0.092, with timing-driven placement and
repair after global routing turned on. Without the calibration the tools saw no setup problem
at 18 ns and the routed slow corner missed 20 ns by 0.02 ns (CI 44379389831).

**Design repair** (repair again after global routing, wires at most 300 µm,
30 % margin on slew and capacitance) is in `src/config.json` from 28 Oct.
On the same RTL (local run F against CI 40085473871) it adds about 1 600
cells, mostly buffers, and leaves 2 max-slew and 2 max-cap warnings instead
of 114 and 32. Its effect on the slow-corner slack is within run-to-run
noise: −0.93 ns there, +0.88 ns in run S against CI 40084290148. The four
warnings left are all on SRAM pins, where the macro's timing is
extrapolated beyond its tables. The slow input edges are on address pins
`A_ADDR[4]` and `A_ADDR[6]` (worst 0.76 ns against 0.60 ns, slow corner),
which have 20 ns of setup and 2.5 ns of hold slack. The heavy loads are on
data outputs `A_DOUT[11]` (0.23 pF against 0.064 pF, in every corner) and
`B_DOUT[9]` (0.072 pF): the worst path from `A_DOUT[11]` has 3.04 ns of
slack at the slow corner (OpenSTA with the sign-off constraints, which
reproduces the flow's 1.243 ns worst slack), so its extrapolated 7.07 ns
clock-to-output would have to be 3 ns short to matter. Run F's layout
passes the precheck and the five gate-level tests, and its netlist equals
the RTL (`netlist_equiv.py`, 2 min 26 s: the repair added 1 729 buffers
and no logic). The 109 max-fanout warnings are clock-tree leaf buffers driving 17 to 19 flops
(the limit is 8); the timing above includes them.

**Gate-level tests** (`test_gl_smoke`, `test_gl_firmware`; the action's
`gl_test` job runs them on the routed netlist, with zero delays). The
netlist elaborates with no X on its outputs and recovers from reset; it
boots firmware through the parallel loader as the chip will, then sends
UART with every bit exactly `BIT` cycles for a whole message
(`uart_tx.s`), runs two threads at once (`thread_demo.s`), and answers as
a SPI flash (`spi_flash.s`) at a 14-cycle SCK half period. All five pass
on the netlists of run 40085473871 and local run F. The UART test found a real bug: the
boot ROM let `uio[0]` go low for one cycle when it handed over, which a
receiver takes as a start bit (fixed in 6ea46bd).

TT's precheck (KLayout CMOS5L DRC, pins, boundary, layers, cell names)
passed on run 40079395684. From 24 Oct about 11:00, GitHub stopped starting
jobs on the repository (an account limit on Actions), so the later runs built the
layout but never ran their precheck, gate-level test or sign-off job.
`check_signoff.py`, the gate-level tests and the precheck (`precheck.py` from
tt-support-tools, branch `ihp-sg13cmos5l` at d66cf17) were run by hand on
their artifacts; the precheck passed on the layouts of run 40085473871 and local run F. Before the move to CMOS5L, v2.0 hardened on SG13G2 at 40 ns
with 23.09 ns to spare at the slow corner (run 39022166820, 17 Oct) and at
20 ns with 3.09 ns (run 39633377026). v1 never hardened (`history.md` §5).

## 7b. Power per workload — `tools/power.py`

The routed CMOS5L netlist (CI run 40079395684) runs each workload in a
gate-level simulation, booted through the parallel loader as on silicon. A
VCD of 2 080 cycles in steady state gives the activity of every pin (all
54 758 are annotated), and OpenSTA computes the power from it with the
extracted parasitics and the typical-corner libraries (1.20 V, 25 °C). At
12.5 MHz:

| Workload | Total | Flops | Clock tree | SRAM | Logic |
|---|---|---|---|---|---|
| `uart_tx.s` sending | 1.77 mW | 1.07 | 0.42 | 0.24 | 0.03 |
| `thread_demo.s` (two threads) | 2.14 mW | 1.08 | 0.42 | 0.24 | 0.39 |
| a `JMP` to itself | 1.89 mW | 1.07 | 0.42 | 0.24 | 0.16 |

Most of it is the same whatever the program does: the clock pins of the
1 596 flops (there is no clock gating), the clock tree, and the code bank
of the SRAM, read every cycle. Dynamic power scales with the clock, so
about four times this at 50 MHz; leakage is 31 µW. The simulation has no
delays, so glitches are not counted: these are estimates from the layout,
not measurements.

## 7c. The hardened netlist equals the RTL — `formal/netlist_equiv.py`

The formal properties (§3) and the simulations (§2, §4) are about the RTL.
`netlist_equiv.py` proves that the routed netlist (the GDS action's
`tt_submission/tt_um_msf.v`, with hold buffers, resizing and the clock
tree) behaves the same: one sequential miter of the two designs, the
cells' logic read from the CMOS5L liberty, proved with ABC's `dprove`
(latch correspondence, fraiging, then induction). It runs in CI after
`gds` (job `checks / equiv`, from `gds_checks.yaml`); `gh workflow run
gds_checks.yaml -f run_id=<gds run>` runs it and the sign-off check on an
earlier run's layout, at the commit that layout was built from.

* **What is proved:** from reset, on every cycle and for every sequence of
  inputs (including later resets), `uo_out`, `uio_out` and `uio_oe` are
  the same, and so is every pin of both SRAM macros. The macros are cut
  out as black boxes: their inputs are compared, their outputs are free
  inputs shared by both designs, so the result holds whatever the SRAM
  returns.
* **The start state:** Yosys simulates one cycle with `rst_n` low and
  writes the result back as the flops' initial values. Storage with no
  reset (the register file, the input synchronisers) starts at 0 in both.
  Starting both designs at all zeros instead fails: the netlist's only
  flop (`dfrbpq`, 1 604 of them) resets to 0, so the RTL's 93 bits that
  reset to 1 are stored inverted; before a reset the two disagree and
  `dprove` cannot pair those flops.
* **Not covered:** timing (that is §7), and the netlist's behaviour before
  its first reset.

On the netlist of CI run 40085473871 (commit 6ea46bd): **equivalent**, in
8 min 43 s (miter: 83 inputs, 3 176 flops, 75 053 AND gates). Three
deliberate bugs put into that netlist:

| Bug put into the netlist | Result |
|---|---|
| one NAND2 (`_14196_`, in the SERDES receiver) made a NOR2 | undecided after 43 min (exit 2, so CI fails): the difference shows only once a whole byte has been received, thousands of cycles in, beyond `dprove`'s search. A gate-level simulation of a UART boot, which uses the receiver, shows it: no ACK |
| one AND2 (`_13241_`) made an OR2 | not equivalent, 29 min |
| SRAM bank 0's `A_ADDR[0]` and `A_ADDR[1]` swapped at the macro | not equivalent, 1.5 min |

Earlier attempts and why they failed: Yosys `equiv_make/simple/induct`
proved only 331 of 1 660 points in 68 minutes, because it matches flops by
name and 87 of the netlist's have none; EQY stopped at partitioning on a net that
matched twice; `dprove` from an all-zero start did not finish in 78
minutes. A stand-in SRAM model that hashed its inputs also worked, but the
black box is stronger and needs no model.

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
| No skipped, repeated or spurious instructions, for any program | formal properties F1-F7 with free memory and pins, all `cover()` points reached; the v1 bugs reinjected are caught (§3). On the controller F1, F2, F4-F6 hold for all time (PDR) |
| Bit timing is exact and drift free | `test_core` cycle costs; `test_firmware` and `test_faults` measure edges at the pins (UART bits exactly `BIT` cycles for a whole message) |
| No firmware deadline is ever missed, on any path | `tools/msf_timing.py` (§6b): every `WAIT_CMP` in the firmware issues at least 9 cycles before its target; rejects a too-fast UART. Compared with the ISA model for `uart_tx.s` only (1 of 29 deadlines): the model has no SERDES (`plan.md` §7.1) |
| A missed `WAIT_CMP` deadline is never silent | `WAIT_CMP` with a passed target releases at once with Z=1 and sets LATE (isa.md §5): `test_wait_cmp_late_boundaries` (0 and 255 cycles behind are late, 256 behind and 1 ahead are not), `test_late_flags_per_thread`; formal F10, F11 and mutant B4 (§3); random programs with late targets, RTL == model (§2). Not covered: a TX engine that runs out of words keeps its last level with no flag (`test_tx_underrun_is_silent`), so firmware timed by the engine has to keep it fed |
| UART, SPI, I2C, USB LS, 10BASE-T TX run as firmware | `test_firmware` against independent decoders and device models; the same checks in C on the AUP-ZU3 board, among its 67 C tests, all passing (§5) |
| Running free at 12 MHz on the FPGA, the core takes UART, I2C and SPI traffic from a real LP-MSPM0G3519 | On the AUP-ZU3 with firmware v3 (§5, 6 Nov): UART, SPI and I2C pass at both ends on all 148 passes, and the on-chip logic analyser showed a correct pass on the wires ([`external-mcu_testing.md`](../external-mcu_testing.md)). Vivado reports timing met at that clock inside the FPGA: setup +74.354 ns, hold +0.012 ns, pins not timed ([`fpga.md`](fpga.md#vivado-results)) |
| The chip can be an SPI device at up to 750 kHz with no dummy byte, and an I2C device at 100 kHz (400 kHz with stretching) | `test_targets`: data in all modes, latencies 4 / 5 / 8 / 13 cycles as derived, a 16-cycle SCK works and 14 fails, 400 kHz needs `STRETCH` |
| The chip can stand in for a 25xx SPI flash: ID, status, read, page program and erase, with WIP set while an erase runs | `test_targets` flash tests against a controller model and a reference model: all modes, the 512-byte wrap, program with and without WEL, every erase command polled during the erase; a 28-cycle SCK works and 26 fails |
| A bus monitor never reports a timing fault on a legal I2C bus, and reports the real faults with their lengths | `test_monitors`: random legal buses raise no event; single faults at every data bit, at 100 kHz and the legal minimum, reported once each within the stated accuracy |
| The I2C monitor keeps decoding a master that is out of spec on every bit (lows ≥ 16 cycles, bits ≥ 72) | `test_mon_i2c_fast_master` at the corners of that envelope; a sweep of 196 points inside it |
| The SPI monitor decodes SCK periods of 28 cycles in a byte, 36 between bytes, all modes | `test_mon_spi_limits` at those values |
| The two threads cooperate without locks (UART in, SPI out) | `test_bridge`, all SPI modes |
| Up to four threads run in a fixed rotation; a thread's slots depend only on which threads run, and a thread that uses no shared engine runs the same whatever the others do | `test_threads` four-thread tests and `test_t0_timing_independent_of_t1`; `test_random_four_threads` (RTL == model on every issue, the running set changing at random); formal F1-F6, F4 for four threads, and F13 (with its mutant). The limit, found 8 Nov: the TX, RX and CRC engines are shared, so waiting on one depends on the other threads (`test_shared_engine_wait_depends_on_other_thread`: 22 cycles, or 42 while another thread uses the CRC) |
| An edge leaves the chip on its cycle or not at all, whatever the threads do (v4) | `test_timed_pins`: with one to four threads (one stalled, one loading) an `AT` write changes the pad in cycle T+1 exactly; the reply to an input edge is exactly `delta` cycles after it for every edge phase; a time that has passed is refused with Z = 1 and LATE and no pin changes. Formal F17 (not run: `v4-open-problems.md`); RTL == model on random programs with timed windows and on a four-events-in-one-cycle program; the deadline checker certifies a reply with room and refuses one without |
| Two threads can use a TX, RX and CRC engine each, so one's timing does not depend on the other's (v4) | `test_engine_sets`: two UARTs from two threads decoded at the pins, two CRC units independent, `STAT` and `TX_WAIT` per set; `test_random_*` with T1 on CRC set 1, RTL == model. Plan 7.13's caveat on shared engines no longer applies to threads on different sets |
| The chip can be an SPI slave on the master's clock, in every mode, at 6 cycles a bit transmit and 2 receive (v4) | `test_engine_options`: all four modes against a bus-master model, bytes through the RX engine and MISO sampled at the master's edge; limits measured (RX 2, TX 6 cycles a bit in zero-delay simulation, 8 with a cycle of margin) |
| HDLC and CAN framing's bit stuffing, with errors, run on the engines (v4) | `test_engine_options` against a stuffing reference: TX for both kinds and several n, RX round trip, E_STUFF on a violation, E_FRAME on a bad stop bit, E_UNDER for a stream that runs dry |
| The reset releases every flop on one clock edge; the pads never glitch when an engine takes a pin (v4) | `test_v4_fixes` (release at any phase, asserted with no clock edge, pads equal the old combinational expressions every cycle); formal F18 (not run); gate-level simulation with delays: not done (needs the hardened netlist) |
| The hardware FIFO hands words between threads in order, one instruction each way, and never overfills | `test_threads.test_fifo_between_threads`, `test_isa_v2` FIFO tests, formal F8, the random differential test (§2) |
| The RX engine receives Manchester, re-locking after each frame | `test_rx_manchester_frames_jitter` (+-2 % rate, +-1 cycle jitter), `test_rx_manchester_loopback` |
| The glitch filter removes pulses shorter than its length, delays the rest by exactly that, and leaves unfiltered pins alone | `test_filter_removes_short_pulses`, `test_filter_delay`, `test_filter_uart_spikes` |
| The chip records 8 pins as a logic analyser without changing any thread's timing | `test_capture`: every word checked against the spec's rules; the same loop takes the same cycles recording or not; formal F9 |
| A host can halt, single-step and read the core's state through the pins | `test_debug.test_debug_single_step`: hand-driven clock, PC trace and register read between edges |
| Timing faults can be injected by exact amounts | `test_faults`: each knob measured at the pins; a one-cycle step in the UART bit time moves a receiver from pass to fail exactly where its geometry predicts |
| The design fits its 6x4 tiles on CMOS5L, on the shuttle's own flow, and runs at 33 MHz in every corner, 50 MHz at the typical one | §7: CI 42393871451 on `tt-gds-action@ttihp27a`: 46.5 % utilisation; at 30 ns, setup and hold met in all three corners (slow +3.62 ns, typical +11.93 ns, fast +14.00 ns, hold +0.08 ns), no routing DRC, antenna or LVS errors; precheck, gate-level tests and netlist = RTL pass |
| v4 fits 8x4 tiles with four SRAM macros and runs at 50 MHz in every corner | §7: CI 44499734618 (19 Nov): at 20 ns, setup and hold met in all three corners (slow +3.36 ns, typical +9.69 ns, fast +12.84 ns, hold +0.26 ns), 62.7 % utilisation, no routing DRC, antenna or LVS errors; precheck, gate-level tests and netlist = RTL pass |
| The layout is the RTL, so every check above applies to it | `netlist_equiv.py` (§7c): the routed netlist is proved equivalent to the RTL from reset, for all inputs and all time, with the SRAMs as black boxes. Of three bugs put into the netlist, two are found and the third, deep in the receiver, is left undecided: the tool never calls a broken netlist equivalent. The netlist also boots and runs firmware in gate-level simulation (§7) |
| The tests catch a broken design, not only pass a working one | 39 of 40 sampled single-bit mutants killed on v2.0, 38 of 40 on v2.1, 37 of 40 on v2.2; every survivor cannot change behaviour (§8) |

What is **not** validated yet:

* Nothing has run on silicon. In the C board tests the PS steps the core one
  clock at a time, so they check logic and protocols, not timing at speed.
  Only the MCU test (§5) runs it free, at 12 MHz on the FPGA. Vivado reports
  timing met at that clock inside the FPGA but does not time the pins, and FPGA
  timing says nothing about the chip's.
* Apart from that LP-MSPM0G3519, the other end of every bus is a model written
  by us from the protocol specs, not a real device: no USB host, Ethernet link
  partner, EEPROM or logic analyser has been connected. sigrok's decoders are an
  outside check on what the chip sends (§6c), not on how a real device responds.
* Electrical behaviour is not modelled: pad drive, rise times with real
  pull-ups, USB and 10BASE-T signal levels.
* Timing is checked by static analysis only (§7): the gate-level
  simulations have no delays.
* v4's formal properties, F17 and F18 and the edited F5 and F11, have not been run
  by anything: Yosys parses all four targets (`formal/parse_check.py`) but this PC cannot run the
  solvers, and CI has not run them yet. The same goes for the hardening of v4: four macros on the
  8x4 die, hold margins of 0.3/0.25 ns and the debug-select false path are written, not run.
* Some formal properties are bounded (depth 16 from reset): four of the
  controller's (§3) and those of the top level. The properties cover the
  control pipeline; the SERDES and CRC datapaths are checked in simulation
  only.
* The mutation score comes from a sample of 40 single-bit faults.
