# Plan v3: get ahead of the field

Branch `v3`, started 8 Nov 2026 from `plan7` (06da447). Sam: "figure out how they were
able to do this [48-50 MHz] ... make sure we have a higher breadth, and hardware
guarantees, anything for us to get ahead. You can change the RTL. Make a plan first to
work off, keeping it up to date when you complete a task."

`main` stays v2.2, signed off and run on the board, until this branch has its own
sign-off and board run. The status table is kept current as items land.

## Status

| # | Item | Status |
|---|---|---|
| 1 | Pipeline: a decode stage, ISA v2.2 timing unchanged | built (8 Nov): D registers the word and its operands, E forwards the last write, the SRAM fetches with the pointers' next values. Full suite 236/237: the failure was a reset released on a clock edge (the debug single-step test) fetching word 0 twice, because the fetch address depended on rst_n; fixed: the boot ROM's read register resets to word 0 and the address comes from registers only. RTL deadline cross-check: every deadline of the existing programs reached and in its range (86). Formal F1-F14 and mutants: rerunning on the fix |
| 2 | Timing closure at 20 ns, local hardening | first local harden of v3 (sign-off 20 ns, pnr 16 ns): mid-PnR worst slack +4.6 ns at the typical corner (about 11.4 ns of path); global routing congested (overflow 2,358, 47.7 % of the resource; v2.2's local runs at pnr 20 ns: 1-86, 39 %; 25 % more wire), like the October attempt at pnr 16 ns on v2.2 (run B), whose detailed routing did not converge; detailed routing running. Next if it fails: pnr 20 ns with sign-off at 20 ns |
| 3 | Line unit: 10BASE-T receive, USB full-speed | **USB full speed done without RTL** (8 Nov): at 48 MHz a bit is exactly 4 cycles, and the RX engine's NRZI, de-stuffing and edge resync (there since v1, used for low speed) receive it: `usb_fs_rx.s` takes 32-byte packets at +-0.25 % with random phase and 8 ns of edge jitter, sampled as the synchroniser does, and checks the CRC16; `usb_fs_tx.s` sends with the EOP to the cycle; sigrok's `usb_signalling` at full speed reads both. Needs the chip at 48 MHz (20.8 ns), so item 2's sign-off. **10BASE-T receive: built** (8 Nov), the RX engine's mode 3, phase-tracking Manchester: the phase in 1/65536ths of a bit advances `RX_DIV` a cycle (13107 at 50 MHz), seven centres lock it, then only the bit's middle half counts and each centre pulls the phase a sixteenth of the way, so it follows the average. No new flops (it reuses the timer, the window flag and de-stuffing's counter). `eth10_rx.s` keeps the frame and checks its FCS as the bytes come in; `test_eth10_rx`: minimum and maximum frames at +-100 ppm with 10 ns of edge jitter at random, a corrupted one refused. Not 802.3's 13.5 ns: sampling at 20 ns adds 20 ns of spread, which leaves centres and boundaries 1.5 ns apart even with a perfect clock; mode 3 in Python, 150 frames a point: none wrong at 10 ns, 1 at 11, 11 at 12. More would need sampling on both clock edges |
| 4 | Hardware guarantees: late kill, timed pin events, thread non-interference | **late guard built** (8 Nov): `LATE_CLR` with bit 15 names threads and pins; while a named thread's LATE flag is set its pins' output enables read 0 at the pads, from the cycle after the late `WAIT_CMP` released until the flag is cleared; `LATE_RD` reads the guard back. Formal F15 (BMC depth 16 passes on all three targets) and its planted bug (the guard ignoring which threads it names), `test_guard` (one thread, another thread), the ISA model, random-diff comparing OE at the pads with guard loads in the random programs, `isa.md` 5 and 6.5. **Timed pin events: held**: no encoding is free (32 of 32 EXT sub-ops, 8 of 8 ports) and the die is congested at 50 MHz; decided after item 2. **Non-interference**: F13 (a thread's slots depend only on the running set) and F14 (each instruction gets its own thread's registers, through the new forwarding) prove it within one copy. **F16, two copies** (`formal/ni_miter.v`, `--top ni`): written, with its planted bug (`STAT` showing any thread's LATE flag, which no single-copy property sees); BMC on two copies is slow (depth 5 in 2 minutes, depth 6 not in 20), so PDR is being tried |
| 5 | Breadth: 75 programs or more | 75 programs written (42 at the start), every one with deadlines certified; 25 committed with passing tests and sigrok where a decoder exists; 20 more in test (batch 5); the checker found real bugs in five of them on the way (MIDI, quadrature, stepper pause, I2S frame boundary, DShot300 refused) and one test-tightening found RC-5's 18.5 us drift. Batch 5's first run (20 programs, 23 tests): six failures, five of them real firmware bugs: WS2801's end marker equal to yellow's first word; MDIO's first preamble refilled from a pin still driven low; the SD card's response wait testing bit 15 for bit 7; nRF24's WAIT flag (bit 15) tested by doubling, which wraps to 0; NEC's repeat burst longer than the 0.5 ms step time-out; and one test bug (ADF4351's idle LE counted as a latch). Found by running each program against its test's own device model on the ISA model, which takes seconds; all six pass on the RTL |
| 6 | Tools, model, docs for the new ISA parts | not started |
| 7 | Sign-off and evidence again | not started |

## 0. What the leaders did, and where our time goes (facts of 8 Nov)

**Clock.** All three leaders close a 20 ns clock (`CLOCK_PERIOD` 20) at the slow corner
(1.08 V, 125 °C), the one we sign off at:
* Escapement went from 33 MHz (v1.5) to 50.8 MHz at the slow corner (v1.6) by decoding
  the instruction a stage earlier and reading its operands a stage earlier, for 354 more
  flip-flops; its register file is in latches. Same 6x4, same four-thread barrel as ours.
* MarcosAsh: 48 MHz with +4.03 ns at the slow corner, 8x4, two cores, each with its own
  program SRAM and a shared data SRAM (three 512x16 macros), repair after global routing.
* Wirelingo: 50 MHz with +0.01 ns, 6x4, two cores with a 256x32 SRAM each, placement and
  routing repaired at all three corners.

Ours (CI 42393871451, shuttle flow, 30 ns): the worst path is the SRAM macro's
clock-to-output, 6.78 ns, then about 19 ns of decode, register read, ALU and write-back,
much of it wire buffers across the die: 26.9 ns, +3.62 ns at 30 ns. The decode works on the
macro's output in the same cycle as the ALU (`tt_um_msf_ctrl.v`: "IMEM has a registered
read ... decoded in cycle N+1"). The macro is not the lever: at the slow corner a 512x16
gives its data after 6.3-6.7 ns whether it has one port or two, a 256x32 after 5.1-5.6 ns
(the PDK's liberty files), so changing macros saves about 1 ns. Splitting the path is
what Escapement did, and what this plan does.

**USB full-speed and 10BASE-T receive** both need clock recovery in hardware, which the
leaders call a line unit: Wirelingo receives USB at 48 MHz, 4 clocks a bit, and 10BASE-T
within 802.3's ±13.5 ns of edge jitter at 50 MHz; MarcosAsh has both too. Our RX engine
samples at fixed bit centres, which needs about 4 samples a half-bit for Manchester: ~80 MHz.

**Hardware guarantees.** MarcosAsh: the chip checks each program against a certificate
and refuses to start one it cannot check; a missed deadline takes the engine off its
pins (proved). Escapement: "never late" proved for every program, for all time. Loom:
each thread's isolation proved. Ours: a static checker (outside the chip) and a LATE flag.

**Breadth.** MarcosAsh 68 firmwares, Wirelingo 68, Escapement 49, Loom adding about forty
overnight on 8 Nov. Ours: 39.

## Goals

* **G1** 50 MHz at the slow corner, signed off at 20 ns in every corner, with ISA v2.2's
  timing unchanged: every instruction costs what `isa.md` §5 and §7 say, so every firmware,
  the checker and the ISA model stay right, cycle for cycle.
* **G2** USB full-speed (12 Mbit/s, 4 clocks a bit at 48 MHz) and 10BASE-T receive.
* **G3** Hardware guarantees, each with a formal proof: a late deadline can take the
  thread's pins off the bus; timed pin events that happen on their cycle or not at all,
  for every program; a two-copy proof that a thread's slots and state cannot be changed by
  another thread that uses no shared engine.
* **G4** 75 programs or more, each with a test whose peer is written from the standard,
  and sigrok's decoder where one exists.
* **G5** All of it signed off again: GDS, netlist = RTL, gate-level tests, formal, a larger
  mutation run, planted timing bugs, and a board run by Muath.

## 1. Pipeline: a decode stage, with ISA v2.2 timing unchanged

Three stages: F (the address goes to the SRAM), D (decode the SRAM's word, read the
registers, compute branch and call targets, into pipeline registers), E (ALU, write-back,
flags, branch decision, blocking ops, pins, peripherals).

* **Single thread.** Fetch two ahead. A taken branch is decided in E from the flag
  registers (the instruction before it set them in its own E, a cycle earlier) and the
  target computed in D, and the target goes to the SRAM in the same cycle: the word after
  the branch, in D, is dropped, and the target reaches E two cycles after the branch. That
  is today's 2-cycle taken branch. `JR`, `RET`: the target comes from the forwarded register.
* **Forwarding.** The instruction in D read its registers before the one in E wrote: E's
  result is forwarded at E's input when the destination matches (single thread only; with
  two or more threads the next instruction of a thread is at least two cycles behind).
* **Blocking ops.** The stalled instruction holds E, its successor waits in D, the fetch
  holds; the release cycle and the transfers stay where `isa.md` §5 puts them. `LD` uses
  port B as now, 2 cycles.
* **Threads.** The word for the thread that owns cycle c+2 is fetched in cycle c. The rule
  for a change of the running set (`isa.md` §7) names the owner of c+1, whose word is
  already in D, and a bubble where it stopped or restarted, as now; c+2 follows the new set.
* **Safety net.** The ISA model is not touched: the random differential tests compare the
  pipelined RTL with it on every issue, and all 237 tests must pass unchanged. Formal: the
  ghost signals that follow a word through the pipeline get a stage more; F1-F13 must hold.
* **Done when** the tests and formal pass and a quick hardening (item 2) shows the long
  path split.

## 2. Timing closure at 20 ns

* A fast loop first: synthesis and STA at the slow corner on the local LibreLane image,
  calibrated against CI's 26.9 ns on the v2.2 RTL.
* Then local hardening at 20 ns (pnr) with sign-off at 20 ns in every corner, following
  what the leaders needed: repair after global routing, maximum wire length, all corners.
* Fallbacks, in order: move the next-longest paths into the new stage; a 256x32 macro
  (about 1 ns); 8x4 tiles if Tiny Tapeout confirms them.
* **Done when** a local run signs off at 20 ns in every corner, then one CI GDS run (asked first).

## 3. Line unit: 10BASE-T receive and USB full-speed

* **10BASE-T:** a Manchester receiver that times the intervals between edges (half-bit
  2.5 clocks at 50 MHz) and recovers the clock from the mid-bit transitions, with a peer
  that adds 802.3's ±13.5 ns of edge jitter and ±100 ppm. Firmware `eth10_rx.s`: preamble,
  SFD, bytes into the FIFO, FCS checked with the CRC unit.
* **USB full-speed:** at 48 MHz, 4 clocks a bit: check how far the RX engine's NRZI,
  de-stuffing and resync go, and add what clock recovery needs. Firmware `usb_fs_*.s`,
  a host model from the USB 2.0 specification, sigrok's `usb_signalling` at full speed.
* **Done when** both pass against their peers and sigrok at their rates, certified.

## 4. Hardware guarantees

* **Late kill:** a per-thread setting: when that thread's `WAIT_CMP` finds its target
  passed, the pins it has set as outputs are released (OE cleared) until firmware clears
  the flag. Formal: from the cycle after a late `WAIT_CMP`, those pins stay released.
* **Timed pin events:** a small queue: firmware posts (time, mask, value); the hardware
  applies it on exactly that `CNT`, or refuses it if the time has passed, and counts the
  refusal. Formal, for every program: an event is applied on its cycle or not at all.
  Firmware can then make edges that cannot be late, whatever the code around them does.
* **Thread non-interference:** F14 on two copies of the core: same running set, one thread
  given different programs in the other threads that touch no shared engine: its slots,
  registers and the pins it alone drives are the same.
* **Done when** each has its proof, its mutant and a directed test, and `isa.md` says it.

## 5. Breadth: 75 programs or more

Each program has a test whose peer is written from the standard, and sigrok's decoder
where one exists. In batches:
1. The draw pool's 24 protocols with decoders not yet done: AM230x, AVR PDI, DALI, DCF77,
   DMX512, DSI, EM4305, I2S, NEC IR, RC-5, RC-6, Maple bus, MDIO, Miller, Morse, Qi, SD card
   (SD mode), ST7735, SWIM, T55xx, TDM audio, TLC5620, USB Power Delivery, Wiegand.
2. Common ones the field has: LIN, MIDI, SENT, DShot, SBUS, servo PWM, quadrature encoder,
   HD44780, MAX7219, SPI SD-card start-up, WS2812 receive, I3C.
3. After item 3: USB full-speed device, 10BASE-T receive.

## 6. Tools, model and documents for the new parts

The assembler, the ISA model, `msf_timing.py` (late kill, timed events), the
playground, `isa.md` v3, `verification.md`, `instruments.md`, README.

## 7. Sign-off and evidence again

Formal CI (BMC, mutants, PDR), netlist = RTL, gate-level tests on the new netlist, a
mutation run of several hundred faults, the planted-timing-bug campaign, the deadline
cross-check, sigrok, power. `fpga/rtl` in step with `src`, and a board run of the 67 C
tests and the MCU test by Muath: until then, the board evidence is v2.2's, and the
write-up says so.

## Risks and decisions

* The pipeline touches the heart of a design that is signed off and has run on the board.
  If it does not close or does not pass, `v3` stays a branch and `main` keeps v2.2.
* Board evidence: v3 needs Muath's board run before it can be submitted honestly.
* CI GDS runs are on demand: local runs first, CI asked for.
* Merging `v3` into `main` is Sam's decision, after item 7.
