# msfcore architecture, for someone who knows PIO and PRU

msfcore is a small CPU whose job is pins and time. This page is the whole design on one screen;
[`isa.md`](isa.md) is the contract, [`verification.md`](verification.md) is the evidence.

```
                         ui_in[8]   uio[8] <-> pads   uo_out[8]
                            |           |                 ^
              2-flop        v           v                 |  pad registers: the final value
              synchronisers (a pad change is seen 2 cycles later)  (a pin write is on the pad next cycle)
                            |           |                 |
   +------------------------+-----------+-----------------+---------------------------+
   |  4 threads, one issue slot: T0 T1 T2 T3 T0 ... (a running thread owns every n-th)  |
   |  each: PC, R0-R7, flags, timeout, ESEL byte, ONE TIMED-PIN SLOT (AT, TS)            |
   |  decode -> execute; every instruction's cost is fixed (isa.md section 5)            |
   +--------+-----------------------+-----------------------------+---------------------+
            |                       |                             |
   timed events: held until        engine set 0 + engine set 1     capture engine (logic
   CNT == time, applied on that    each: TX engine, RX engine,     analyser) ->  FIFO between
   cycle whatever threads do       CRC unit; a thread's ESEL bit   threads, glitch filter
                                   picks its set; external clock
            |                       |                             |
   SRAM 2048 x 16: 4 macros (512 each), boot ROM 128 words, loader: UART/parallel, CRC-8, autobaud
```

## Time is part of the instruction set

Every instruction costs one slot of its thread; a taken branch costs two in single-thread mode and
nothing extra with several threads; a blocking op costs what its stall costs. The costs are in the
ISA ([`isa.md`](isa.md) §5, §7), not in a pipeline description, which is why a **static checker**
(`tools/msf_timing.py`) can decide before a program runs whether a deadline is ever missed, and why
the ISA reference model, written from the document and not from the RTL, is compared with the RTL on
every instruction of every random program.

`WAIT_CMP` waits on an absolute value of a free-running 16-bit cycle counter, so a bit schedule is
`target += period; WAIT_CMP target; write`, with no accumulated drift. A deadline that has already
passed releases at once with Z = 1 and sets the thread's LATE flag; the **late guard** takes the
pins that thread names off the bus until firmware clears it.

## Four threads, no scheduling

The rotation is fixed: each cycle belongs to the next running thread. A thread's slots depend only
on which threads run, never on what they do (formal property F13; F14 shows each gets its own
registers). Nothing is dynamic, so nothing can be starved or surprised.

## Timed pins: the edge leaves on its cycle, or not at all

With *n* threads a thread's own instruction can only run every *n*-th cycle, and a reply to an input
is late by up to *n* - 1 cycles plus whatever code lies between the wait and the write. So v4 takes
the edge out of the instruction stream:

```
WAIT_EDGE 0, rise     ; the other side moved
TS    R1              ; the cycle that really happened (CNT of the event, not of the release)
ADDS  R1, R1, R2      ; our answer, R2 cycles after it
AT    R1              ; arm this thread's one slot
PIN_TGL 0x01          ; held; applied on cycle R1, whatever any thread is doing
```

The write waits in the slot and is applied when `CNT` equals its time. If the time has already passed
when the write arrives, or the slot is occupied, the write is **refused**: no pin changes, Z = 1, the
LATE flag is set. The test measures zero jitter with one to four threads running, one of them
stalled and one loading. Cost: about 190 flops and four comparators. The checker certifies a reply
by one condition, "armed before its time", which is easier than an exact path length.

## Engines, and two sets of them

Generic line-code engines, not protocol blocks: a TX engine (NRZ, NRZI, Manchester, bit stuffing of
any length and kind, a differential pair), an RX engine (the same, with resync and a phase-tracking
Manchester mode for 10BASE-T), and a CRC unit for any width up to 32 bits. Framing stays in firmware,
so the same engine serves UART, USB, CAN and Ethernet. **Two sets** (v4) mean two protocols at once
and both directions of a link, with no engine shared between threads on different sets. An **external
clock** mode makes an engine shift on the other side's clock, so an SPI target runs word by word.
Error flags (frame, stuff, underrun) report what the engines see.

## Memory and loading

2,048 words in four IHP two-port macros and a 128-word boot ROM. Programs are loaded after
fabrication over UART (with autobaud, so any clock) or a parallel port, with a CRC-8 and an ACK; the
RP2350 on the demo board is the natural host.

## Against PIO, PRU and XMOS

| | PIO (RP2040) | PRU (TI) | XMOS | msfcore |
|---|---|---|---|---|
| Timing | one cycle an instruction, per state machine | one cycle an instruction | threads on a 100 MHz scheduler, timed ports | one cycle an instruction, four threads in a fixed rotation, costs in the ISA |
| Decisions | none on the block: the ARM cores decide | the core has an ALU | full CPU threads | a full ALU and registers per thread, on the chip |
| Time as an operand | wait for a pin, a delay field on the instruction | cycle counter | `p @ t <: v`, `p :> v @ t` | `WAIT_CMP` on an absolute count; `AT` / `TS` (v4) |
| Missing a deadline | silent | silent | the port waits | refused or released at once, with a flag, and the late guard |
| Proof | none published | none published | timing analysis with the XTA tool | an independent model, formal properties with planted bugs, a static deadline checker, claims attacked |

What we took: PIO's idea that a protocol is a short program on a few exact-timing instructions; PRU's
single-cycle determinism; XMOS's timed ports (`AT` and `TS` are that, for a four-thread core, with
the refusal added). What we did differently: the decisions stay on the chip (the same core makes the
bit-rate choice of an SPI target, the ACK of an I2C device, the timing check of a monitor), the
timing is a property the ISA states and a tool checks, and a missed deadline is never silent.

## Limits

Honestly, in [`envelope.md`](envelope.md) and [`verification.md`](verification.md) ("What is not
validated yet"): nothing has run on silicon; the other end of every bus but one is a model we wrote;
v4's formal properties are written but not yet run by a solver; the layout of v4 on 8x4 is not yet
hardened.
