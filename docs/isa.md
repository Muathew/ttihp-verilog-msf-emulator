# ISA Spec — `msfcore` (Protocol Emulator Core) v4

> v4 (branch `v4`, from 2026-11-11; `docs/plan-v4.md`), built in steps, each of which
> is listed here when it lands. **Step 1** (the review's fixes): a store into code
> must be **three** instructions ahead (§1: v3 fetches a cycle earlier than v2.2, and
> v3's text still said two; the ISA model now raises an error on a violation); the
> reset is released through a two-flop synchroniser, so every flop leaves reset on
> one clock edge and the core's cycle 0 is two cycles later than in v3 (§1); the
> pads are driven straight from flops, with no change of timing; the limit of RX mode
> 3 is stated (§6.2). Every v3 program that stores into code at least three
> instructions ahead, and every program that does not store into code, runs unchanged
> and takes the same cycles from the core's cycle 0.
> **Step 2** (2,048 words): four 512-word SRAM macros instead of two, so the PC and
> every address are 11 bits (§1); `JMP`, `CALL`, `RET`, `JR`, `LD`, `ST`, `MEM_A`,
> `MEM_W` and `TSTART` use all of them; `FIFO_CFG` and `CAP_BUF` take the extra base
> bit in a bit that was free, so the v3 encodings mean what they meant (§6.5, §6.6);
> the debug view shows PC bit 10 (§8). A v3 program runs unchanged unless it relied
> on the address space wrapping at 1,024 words.
> **Step 5** (in progress): port page 3 (§6.10) with the chip ID, version, capabilities and the high
> half of a 32-bit cycle counter; the boot ROM's autobaud and memory test (`bootloader.s`).
> **Step 4** (engine sets and options, §3.2, §6.1, §6.9): a second TX / RX / CRC set chosen by
> `ESEL` bit 0, the engines on all 24 pins (`RX_CFG` bit 3, `TX_CFG` bit 15), error flags
> (`STAT` bit 15), general stuffing (HDLC, CAN) and an external clock, on port page 2.
> **Step 3** (timed pins): `ESEL` (`SETM` selector 6, §3.2) gives each thread a port
> page, and page 1 has `AT`/`TS` (§6.8): a pin write that the hardware applies on a
> given `CNT`, or refuses (Z = 1, LATE) if the time has passed. Nothing a v3 program
> does changes (every thread starts on page 0).
>
> v3 (2026-11-08, branch `v3`): a decode stage, for a 20 ns clock, with every cost
> in §5 and §7 unchanged; the **late guard** (`LATE_CLR` with bit 15, §5 and §6.5):
> a thread that misses a deadline lets go of the pins it names; and the RX
> engine's **phase-tracking Manchester** mode (mode 3, §6.2) for bit periods that
> are not a whole number of cycles (10BASE-T at 50 MHz). v2.2 programs run
> unchanged and take the same cycles.
> v2.1 (2026-10-21, branch `rtl-changes`) adds a timeout register per thread,
> the peripheral ports `PW`/`PR` with a FIFO between threads (§6.5), the
> debug view (§8), a second 512-word SRAM bank (10-bit PC and addresses) and
> a capture engine that records the pins into SRAM (§6.6), Manchester
> receive (§6.2), an input glitch filter (§6.7) and four hardware threads
> (§7: `TSTART` / `TSTOP` name the thread; with no name, T1 as before).
> v2.0 programs run unchanged unless one thread relies on a `TMO` the other
> thread set after `TSTART`, or on an address wrapping at 512.
> v2.2 (2026-10-23, branch `fixes`): a `WAIT_CMP` whose target has already
> passed releases at once with `Z=1` and sets the thread's LATE flag (§5,
> `STAT` bit 14, port 7) instead of waiting 65,536 cycles for `CNT` to wrap.
> Programs that wait more than 65,280 cycles ahead must split the wait.
> v2.0 (2026-10-16) supersedes v1.0. All v1 encodings keep their meaning **except
> `CALL`** (v1: `R7 ← PC+2`, no jump; v2: `CALL imm11` jumps). v1 history is in git.
> This document is the contract for the RTL (`src/`), the assembler
> (`test/assembler.py`) and the reference model (`test/isa_model.py`).

## 1. Machine model

* 16-bit instructions, word addressed, 11-bit PC (0–2047, wraps).
* **Four hardware threads** T0–T3 (§7). Each thread has its own PC, R0–R7
  (16-bit), flags `Z`/`N`, blocking-op state and `TMO`. After reset only T0 runs.
* **Flags:** `ADDS`, `SUBS`, `INCR`, `DECR` set `Z` (result==0) and `N` (bit 15).
  Every **blocking op** (§5) sets `Z` = 1 if it ended by timeout, else 0, and
  leaves `N` alone. `FIFO_PUT` and `FIFO_GET` set `Z` = 1 when the FIFO was
  full / empty, else 0 (§6.5). Nothing else touches flags.
* **Cycle counter `CNT`:** 16-bit, free running, reset to 0.
* **Memory:** one 2048×16 address space, held in four 512-word SRAM banks
  (bank = address bits 10:9; crossing between them costs nothing).
  * Fetch: words 0–127 come from the **boot ROM**, 128–2047 from the SRAM.
  * Data (`LD`/`ST`/`MEM_W`/FIFO): always the SRAM, all 2048 words. SRAM 0–127 is
    therefore 128 words of data RAM that never collide with code; 128–2047 is
    code or data (the capture engine and a frame buffer can sit in 1024–2047, clear
    of a program that fits below). **Stores into code** (a store, `MEM_W` or `FIFO_PUT` into a word that is
    later issued): the word that issues in cycle *c* was read from the SRAM at the
    clock edge that ends cycle *c*−2, and a store issued in cycle *w* lands at the
    edge that ends cycle *w*, so a word is safe to issue from cycle *w*+3. Issued in
    *w*+1 or *w*+2 it runs its **old** content, and a fetch in the very cycle of the
    write reads and writes one address on the macro's two ports at one edge, which
    silicon does not define. In single-thread mode that is a target three or more
    instructions after the store (`W+3`); with *n* threads a thread owns every *n*-th
    cycle, so a thread can change its own instruction *k* ahead only if *k*·*n* ≥ 3
    (two threads: `W+2`; three or four: `W+1`), and another thread's instruction
    only if it does not issue within two cycles. There is no bypass in the hardware (it
    would sit on the fetch address, the latest signal in the core), and the ISA model
    raises an error on a violation. v2.2 fetched a cycle later and allowed one
    instruction less; v3's text kept v2.2's rule by mistake (plan-v4 B1).
* **Shift register `SR`** (16-bit) with configurable output/input pins (§4).
* **Pins:** `uio` (8 bidirectional: `OUT`, `OE`, input), `uo_out` (8 outputs:
  `UO`), `ui_in` (8 inputs).
* **Inputs are synchronised** (2 flip-flops): a pad change at cycle *t* is first
  visible to instructions executing at cycle *t+2* (*t+2+len* on a `uio` pin
  the glitch filter is turned on for, §6.7).
* **Outputs are registered:** a pin write executed in cycle *t* appears on the
  pad from cycle *t+1*. Since v4 `uio_out` and `uio_oe` come straight from flops
  that hold the final value (the register, the TX engine's override and the late
  guard already applied), so a pad never glitches when an engine takes or releases a
  pin; the timing is unchanged. (`uo_out` is the `UO` register, or the debug view while
  it is selected, §8.)
* **Reset:** PC(T0)=0 (boot ROM), T1–T3 stopped, all registers 0, flags 0, `OUT`=
  `OE`=`UO`=0, `SR`=0, `SER_CFG`=out 7/in 7/MSB-first, `TMO`=0xFFFF (every
  thread), `MEM_W` pointer = 128, FIFO empty at SRAM 64–127, peripherals
  disabled. **`rst_n` is asserted on every flop at once and released through a
  two-flop synchroniser (v4, plan-v4 B2): every flop leaves reset on the same clock
  edge, whatever the phase of the release.** The core's cycle 0 (the cycle the boot
  ROM's word 0 issues in) is the third clock edge after `rst_n` rises, two cycles later
  than in v3; every cycle count in this document is from cycle 0. Hold `rst_n` low for
  at least three clocks: the debug-view select `ui_in[2]` (§8) is sampled on the
  clock edges while the synchronised reset is low.

## 2. Encoding

`b15..b11` = opcode. Formats:

| Fmt | b10..b8 | b7..b5 | b4..b2 | b1..b0 | Notes |
|---|---|---|---|---|---|
| R | rd | rs | rt | 00 | |
| I | rd | imm8 (b7..b0) | | | zero-extended |
| B | rel9 (b10..b2), signed | | | 00 | target = addr+1+rel9 |
| A | imm11 (b10..b0) | | | | target = imm11 |
| X | ra | rb | sub (b4..b0) | | EXT group, opcode `01111` |

Reserved bits must be 0 (the assembler enforces this).

## 3. Opcode table

| op | Mnemonic | Fmt | Semantics |
|---|---|---|---|
| 00000 | `LDR rd,#imm8` | I | rd ← imm8 |
| 00001 | `ADDS rd,rs,rt` | R | rd ← rs+rt; Z,N |
| 00010 | `SUBS rd,rs,rt` | R | rd ← rs−rt; Z,N |
| 00011 | `AND rd,rs,rt` | R | |
| 00100 | `OR rd,rs,rt` | R | |
| 00101 | `XOR rd,rs,rt` | R | |
| 00110 | `SHL rd,rs,#imm3` | R | rd ← rs << b4..b2 |
| 00111 | `SHR rd,rs,#imm3` | R | logical |
| 01000 | `MOV rd,rs` | R | |
| 01001 | `JMP imm11` | A | |
| 01010 | `JZ rel9` | B | if Z |
| 01011 | `JNZ rel9` | B | if !Z |
| 01100 | `JP rel9` | B | if !N |
| 01101 | `JN rel9` | B | if N |
| 01110 | `NOP` | — | |
| 01111 | **EXT** | X | see §3.1 |
| 10000 | `PIN_W rs` | I | OUT ← rs[7:0] (rs in b10..b8) |
| 10001 | `PIN_R rd` | I | rd ← {0, uio_in (synced)} |
| 10010 | **SETM** `sel,#m` | I | b10..b8 = sel: 0 `OE_SET` OE\|=m · 1 `PIN_SET` OUT\|=m · 2 `PIN_TGL` OUT^=m · 3 `UO_SET` UO\|=m · 4 `UO_TGL` UO^=m · 5 `OE_TGL` OE^=m · 6 **`ESEL #m`** (§3.2) · 7 NOP |
| 10011 | **CLRM** `sel,#m` | I | 0 `OE_CLR` OE&=~m · 1 `PIN_CLR` OUT&=~m · 2 `PIN_WI` OUT←m · 3 `UO_CLR` UO&=~m · 4 `UO_WI` UO←m · 5 `OE_WI` OE←m · 6,7 NOP |
| 10100 | `CNT_R rd` | I | rd ← CNT |
| 10101 | `INCR rd` | I | rd ← rd+1; Z,N |
| 10110 | `DECR rd` | I | rd ← rd−1; Z,N |
| 10111 | `IN_RD rd` | I | rd ← {0, ui_in (synced)} |
| 11000 | `CALL imm11` | A | R7 ← addr+1; PC ← imm11 |
| 11001 | `RET` | — | PC ← R7[10:0] |
| 11010 | `WAIT_EDGE #c` | I | blocking, §5. c[2:0]=pin, c[3]=bank (0 uio, 1 ui), c[6:4]=cond: 0 any edge, 1 rising, 2 falling, 4 level 0, 5 level 1 (3,6,7 = any edge) |
| 11011 | `WAIT_CMP rs` | I | blocking until CNT == rs (rs in b10..b8) |
| 11100 | `SHIFT_OUT rs` | I | SR shifts out one bit to the SR output pin, shifts in rs[0] (§4) |
| 11101 | `SHIFT_IN` | — | SR shifts in the SR input pin (§4) |
| 11110 | `MEM_W rs` | I | SRAM[ptr] ← rs; ptr ← ptr+1 (11-bit wrap) |
| 11111 | `LDH rd,#imm8` | I | rd[15:8] ← imm8 (rd[7:0] kept) |

### 3.1 EXT group (opcode 01111, X format: `ra`=b10..8, `rb`=b7..5, `sub`=b4..0)

| sub | Mnemonic | Semantics |
|---|---|---|
| 0 | `SR_LD rb` | SR ← rb |
| 1 | `SR_RD ra` | ra ← SR |
| 2 | `XFER` | full-duplex SR shift: output bit out, input pin in (§4) |
| 3 | `SER_CFG rb` | rb[2:0] SR out pin, rb[5:3] SR in pin, rb[6] LSB-first |
| 4 | `OUT_W rb` | UO ← rb[7:0] |
| 5 | `MEM_A rb` | MEM_W pointer ← rb[10:0] |
| 6 | `LD ra,rb` | ra ← SRAM[rb[10:0]] — blocking, always 1 stall cycle/slot |
| 7 | `ST ra,rb` | SRAM[ra[10:0]] ← rb |
| 8 | `SWAP ra,rb` | ra ← {rb[7:0], rb[15:8]} |
| 9 | `JR rb` | PC ← rb[10:0] |
| 10 | `TMO rb` | the executing thread's blocking-op timeout ← rb (0 = never time out). Reset 0xFFFF |
| 11 | `STAT ra` | ra ← status (§6.4) |
| 12 | `TX_CFG rb` | §6.1 |
| 13 | `TX_DIV rb` | TX symbol period in cycles (0 treated as 1) |
| 14 | `TX_PUSH rb` | blocking until TX buffer free, then buffer ← rb |
| 15 | `TX_WAIT` | blocking until TX idle |
| 16 | `RX_CFG rb` | §6.2 |
| 17 | `RX_DIV rb` | RX bit period in cycles (0 treated as 1) |
| 18 | `RX_POP ra` | blocking until RX word available, then ra ← word |
| 19 | `CRC_CFG rb` | rb[3:0] bits per CRC_W − 1, rb[4] reflected |
| 20 | `CRC_W rb` | blocking until CRC idle, then start feeding rb (§6.3) |
| 21 | `CRC_RDL ra` | blocking until CRC idle, then ra ← CRC[15:0] |
| 22 | `CRC_RDH ra` | ra ← CRC[31:16] (not blocking) |
| 23 | `CRC_SETL rb` | CRC[15:0] ← rb |
| 24 | `CRC_SETH rb` | CRC[31:16] ← rb |
| 25 | `CRC_POLL rb` | POLY[15:0] ← rb |
| 26 | `CRC_POLH rb` | POLY[31:16] ← rb |
| 27 | `TSTART #t,rb` | (re)start thread *t* at rb[10:0] with cleared state (§7); *t* in the ra field, 1–3, 0 = T1 (`TSTART rb`). The hardware reads only ra[1:0], so ra = 4–7 would alias 0–3: the assembler refuses `#4`–`#7` |
| 28 | `TSTOP #t` | stop thread *t* (§7); *t* as for `TSTART` (`TSTOP` = T1), the same two bits |
| 29 | `TID ra` | ra ← executing thread id (0–3) |
| 30 | `PW #p,rb` | peripheral port *p* (0–7, in the ra field) ← rb (§6.5) |
| 31 | `PR ra,#p` | ra ← peripheral port *p* (0–7, in the rb field) (§6.5) |

### 3.2 `ESEL`: the thread's port page (v4)

`ESEL #m` (`SETM` selector 6) sets the executing thread's **selector byte**, one per
thread, 0 at reset and at `TSTART`, so every v3 program sees what it saw before:

| Bits | Field |
|---|---|
| 0 | engine set (0 or 1): the TX, RX and CRC instructions and `STAT` bits 0–4 of this thread address that set. **Reserved, 0, until the second engine set exists (plan-v4 step 4)** |
| 3:1 | **port page** *g* (0–7): `PW #p` / `PR #p` address port 8*g* + *p* (§6.5). Page 0 is the ports of v3; page 1 is the timed pins (§6.8) |
| 7:4 | reserved, 0 (the assembler refuses a value above 15) |

`ESEL` costs one slot like any other instruction. The selector is the thread's own: a
change by one thread cannot change what another thread's instructions mean, and the
deadline checker follows it like any constant. A thread that uses the page-0 ports
(FIFO, capture, filter, late guard) and the timed pins switches with one `ESEL` each
way; the instructions that are not `PW`/`PR` do not depend on the page.

## 4. Shift register

`SER_CFG` selects the output pin `po`, input pin `pi` and order. The input is the
synced `uio_in[pi]`. The output bit is written into `OUT[po]` (the same output
register as `PIN_W`, so it appears on the pad next cycle).

| Op | MSB-first (default) | LSB-first |
|---|---|---|
| `SHIFT_OUT rs` | OUT[po]←SR[15]; SR←{SR[14:0],rs[0]} | OUT[po]←SR[0]; SR←{rs[0],SR[15:1]} |
| `SHIFT_IN` | SR←{SR[14:0],in} | SR←{in,SR[15:1]} |
| `XFER` | OUT[po]←SR[15]; SR←{SR[14:0],in} | OUT[po]←SR[0]; SR←{in,SR[15:1]} |

## 5. Blocking ops and timing

Blocking ops: `WAIT_EDGE`, `WAIT_CMP`, `LD`, `FIFO_GET`, `TX_PUSH`, `TX_WAIT`,
`RX_POP`, `CRC_W`, `CRC_RDL`.

* `WAIT_*`, `LD` and `FIFO_GET` (a load, §6.5) always stall. The others complete in their issue cycle if
  their condition already holds; otherwise they stall.
* While stalled, the condition is checked every cycle. The op **completes in
  its release cycle** (the stall cycle in which the condition holds, or the
  timeout expires); the next instruction executes in the thread's next slot.
* Timeout: loaded from the thread's `TMO` at issue, decremented every stalled cycle, release
  with `Z=1` when it reaches 0: a full timeout is exactly `TMO`+1 stalled
  cycles for a stall issued in single-thread mode; in multi-thread mode
  (also if other threads start or stop meanwhile) the release is in the
  thread's first own slot at or after expiry (`TMO`+1 to `TMO`+4 cycles).
  `TMO`=0 disables the timeout. On a non-timeout completion `Z=0`.
* `WAIT_EDGE` edge conditions compare the pin with its value in the previous
  cycle (the first comparison is against the value at issue). Level conditions
  release in the first stall cycle where the level holds.
* `WAIT_CMP` releases in the stall cycle where `CNT == rs` (rs latched at issue).
  **Late targets:** if at issue the target is 0–255 cycles at or before
  `CNT` (`(CNT - rs) mod 2^16 < 256`), the deadline has already passed:
  instead of waiting for `CNT` to wrap (65,536 cycles), the op releases in
  the thread's first stall slot with `Z=1`, like a timeout, and sets the
  thread's sticky **LATE** flag (`STAT` bit 14, port 7, §6.5). So a target
  can be at most 65,280 cycles ahead of the issue cycle.
  **Late guard (v3):** while the LATE flag of a thread the guard names (port 7,
  §6.5) is set, the guard's `uio` pins are off the bus: their output enables
  read 0 at the pads, whatever `OE` holds and whatever any engine drives, from
  the cycle after the late `WAIT_CMP` released until the flag is cleared or the
  guard changed. A program that misses a deadline stops driving the lines it
  was timing instead of driving them late (formal property F15). `OE` itself is
  not changed, so clearing the flag puts the pins back as the program left them.
* `LD` releases in the cycle after issue; the destination is written then.
* A completed `TX_PUSH`/`RX_POP`/`CRC_W`/`CRC_RDL` performs its transfer in its
  completion cycle.

**Cycle costs (single-thread mode):** every instruction 1 cycle; taken branch,
`JMP`, `CALL`, `RET`, `JR` 2 cycles; `LD` and `FIFO_GET` 2 cycles; `WAIT_CMP` with target
`T` ≥ issue-CNT+2: next instruction executes when `CNT == T+1`; a late `WAIT_CMP`
2 cycles; blocking op that stalls *k* cycles costs 1+*k*.

**Multi-thread mode** (§7): the running threads take cycles in turn. Every
instruction costs one slot of its thread (branches have **no** penalty). A
blocking op that stalls completes in a slot of its own thread (it cannot
complete in another thread's slot), so it costs ≥ 2 slots. `LD` and
`FIFO_GET` cost exactly 2 slots. Conditions are still sampled every cycle
(an edge or `CNT` match in another thread's slot is not lost).

## 6. Peripherals

### 6.1 TX engine (`TX_CFG`)
| Bits | Field |
|---|---|
| 2:0 | pin (`uio` index; with bit 15, `uo` index) |
| 3 | diff: also drive pin+1 (mod 8) with the complement |
| 5:4 | mode: 0 NRZ, 1 NRZI (0 = toggle), 2 Manchester (IEEE 802.3: 1 = low→high), 3 NRZ |
| 6 | bit-stuff: after six consecutive 1 data bits insert a 0 |
| 7 | LSB-first |
| 11:8 | word width − 1 (1–16 bits per push) |
| 12 | idle level (line level after enable; NRZI reference level) |
| 13 | enable (engine drives the pin(s), overriding `OUT`) |
| 14 | release: the engine drives its pins only while a symbol is on the line; before the first and after the last symbol (on the exact boundary) the pins show `OUT` |
| 15 | (v4) drive the `uo` pins instead of `uio` (no `OE`: `uo` is always an output; the late guard does not reach it) |

Writing `TX_CFG` resets the engine (buffer and shifter empty, line ← idle). In v4 there are
**two** TX engines, two RX engines and two CRC units, the engine sets 0 and 1; a thread's
`ESEL` bit 0 (§3.2) names the set its `TX_*`, `RX_*` and `CRC_*` instructions and `STAT` bits 0–4
address, and the blocking ops wait on that set. The sets are independent, so two threads that
use different sets (a monitor on TX and RX of a link, a USB to UART bridge) share no engine and
neither's timing depends on the other. Where both sets drive one pin, set 1 wins.
A symbol lasts `TX_DIV` cycles (Manchester: one half-bit). The engine is
double-buffered: one word shifting, one in the buffer; back-to-back pushes give a
gapless stream. When it runs out of data the line keeps its last level.
`busy` = symbol in progress or data pending.

### 6.2 RX engine (`RX_CFG`)
| Bits | Field |
|---|---|
| 2:0 | pin (index, synced) |
| 3 | (v4) bank: 1 = the `ui` pin instead of the `uio` pin (the glitch filter is for `uio` only) |
| 5:4 | mode: 0 NRZ, 1 NRZI (no transition = 1), 2 Manchester (IEEE 802.3: low→high = 1; `RX_DIV` = half-bit), 3 Manchester, phase-tracking (v3; `RX_DIV` = 65536 × bit rate / clock) |
| 6 | de-stuff: drop the bit after six consecutive 1s |
| 7 | LSB-first |
| 11:8 | word width − 1 |
| 12 | start mode: 0 continuous (starts at the first transition), 1 async character (falling edge = start bit, sample `width` bits, re-arm) |
| 13 | enable |
| 14 | resync: every input transition re-centres the sampling phase |

Samples are taken at bit centres (period `RX_DIV`). One received word is buffered
(`rx_avail`); a new word arriving while full sets `rx_overrun`. NRZI reference
level starts at the line level seen when enabled.

**Manchester** (mode 2) is self-clocking: a transition taken as a bit centre
gives the bit (the level after it), then the line is ignored for 1.5
half-bits (hiding the transition at the boundary between equal bits) and
the next transition within the following half-bit is the next centre.
Every centre may be up to half a half-bit early or late. With no
transition in that window the frame has ended: a partial word is dropped
and the next transition starts a frame, taken as a centre, so a frame must
start with a bit whose first half is the idle level (e.g. a preamble of
alternating bits, as Ethernet's). Start mode, de-stuffing and resync do
not apply.

**Phase-tracking Manchester** (mode 3, v3) is for bit periods that are not a
whole number of cycles, such as 10BASE-T at 50 MHz (5 cycles a bit, a half-bit
2.5: `RX_DIV` = 13107). The engine keeps the phase within the bit in 1/65536ths
and adds `RX_DIV` every cycle. The first transition of a frame is a bit centre
(phase 0.5), and so are the next six whatever their phase, each moving the phase
half way to 0.5: the lock, which needs a preamble of alternating bits (it has
no other transitions). After that a transition is a centre only in the middle
half of the bit (phase 0.25–0.75), and moves the phase a sixteenth of the way to
0.5, so the phase follows the average centre; a transition nearer the bit's
boundary (between equal bits) is ignored. A centre gives the bit (the level after
it); a bit that ends with no centre ends the frame, dropping a partial word, and
the next transition starts a frame. Start mode, de-stuffing and resync do not
apply. At 50 MHz it takes 10BASE-T with up to 10 ns of edge jitter at random and
±100 ppm (`test_eth10_rx`); 802.3 allows 13.5 ns, but sampling at 20 ns adds 20 ns
of spread, which leaves the centres and the boundaries 1.5 ns apart even with a
perfect clock: more needs sampling on both clock edges or a faster clock.
**Limit (plan-v4 B10):** the phase is a 16-bit number and a centre moves it to within
a sixteenth of the bit's middle (the first centre of a frame to 0.5, the lock's to
between 0.25 and 0.75) before `RX_DIV` is added. The sum must not wrap, or a bit
boundary is missed, so `RX_DIV` must stay below about 0.25 of a bit a cycle (16,384):
a bit period of **four cycles is the least, five (13,107) is what the tests cover**.
Below four cycles a bit the mode is not defined.

### 6.3 CRC unit
32-bit `CRC` and `POLY`. `CRC_W` processes `len` (=`CRC_CFG[3:0]`+1) bits of rb,
one per cycle (busy for `len` cycles):
* normal (MSB-first): bits rb[len−1]..rb[0]; `fb = CRC[31]^bit;
  CRC = (CRC<<1) ^ (fb ? POLY : 0)` — polynomials/CRCs are **left-aligned**.
* reflected (LSB-first): bits rb[0]..rb[len−1]; `fb = CRC[0]^bit;
  CRC = (CRC>>1) ^ (fb ? POLY : 0)` — **right-aligned**, reflected polynomial.

Examples: CRC-8/0x07 → POLY=0x07000000, result CRC[31:24]. CRC-32 (Ethernet) →
reflected, POLY=0xEDB88320, init 0xFFFFFFFF, FCS=~CRC. USB CRC16 → reflected,
POLY=0xA001, init 0xFFFF, send ~CRC. USB CRC5 → reflected, POLY=0x14, init 0x1F.

### 6.4 STAT
bit0 tx_full · bit1 tx_busy · bit2 rx_avail · bit3 rx_overrun · bit4 crc_busy ·
bit5 T1 running · bit6 executing thread id, bit 0 · bit7 FIFO not empty ·
bit8 FIFO full · bit9 capture recording · bit10 capture done · bit11 T2
running · bit12 T3 running · bit13 executing thread id, bit 1 · bit14 LATE
of the executing thread (§5: one of its `WAIT_CMP`s found its target already
passed) · bit15 (v4) the executing thread's engine set has an error (§6.9).

### 6.5 Peripheral ports and the FIFO
`PW #p,rb` writes rb to port *p*, `PR ra,#p` reads port *p* into ra, in the executing
thread's port page (`ESEL`, §3.2): port 8*g* + *p* on page *g*. The table is **page 0**,
the only page every thread starts on and the one v3 programs use; page 1 is §6.8. Unused
ports: a write does nothing, a read gives 0.

| Port | `PW` (write) | `PR` (read) |
|---|---|---|
| 0 | `FIFO_PUT rb`: push rb | `FIFO_GET ra`: pop into ra |
| 1 | `FIFO_CFG rb`: base = {rb[13], rb[9:0]} (11 bits), rb[12:10] *n*: size 2^(*n*+1) words (2–256); empties the FIFO, clears drops | `FIFO_STAT ra`: ra ← drops[6:0] << 9 \| count[8:0] (no side effect) |
| 2 | `CAP_BUF rb` (§6.6) | `CAP_STAT ra` (§6.6) |
| 3 | `CAP_DIV rb` | `CAP_TPOS ra` |
| 4 | `CAP_TRIG rb` | 0 |
| 5 | `CAP_CTL rb` | 0 |
| 6 | `FILTER rb` (§6.7) | 0 |
| 7 | `LATE_CLR rb`: rb[15] = 0: clear the LATE flag of each thread *t* with rb[*t*] = 1; rb[15] = 1: load the **late guard**, rb[11:8] the guarded threads, rb[7:0] the pins (v3) | `LATE_RD ra`: ra ← {guard pins, guarded threads, LATE flags of T3..T0} in bits 15:8, 7:4, 3:0 |

The FIFO's words live in the SRAM, in the size-aligned block that contains
`base` (base's low bits are ignored); the read and write positions are in
hardware, so either thread can push and pop with one instruction and nothing
to lock. Reset: base 64, 64 words (SRAM 64–127).

* `FIFO_PUT rb`: 1 cycle (a store). If the FIFO is full the word is dropped,
  drops counts it (modulo 128: a reader subtracts its last reading), and
  `Z` = 1; otherwise `Z` = 0.
* `FIFO_GET ra`: a load from the oldest word, so it costs what `LD` costs
  (2 cycles, or 2 slots in multi-thread mode). If the FIFO is empty, ra is unchanged
  and `Z` = 1; otherwise `Z` = 0.
* Each thread touches the SRAM only in its own cycles, so the FIFO never
  makes one thread's timing depend on the other's.

### 6.6 Capture engine
Records 8 pins (the synchronised `uio`, or `ui`) into an SRAM ring buffer:
a logic analyser on the chip. Configured through ports 2–5:

| Port | Write | Bits |
|---|---|---|
| 2 | `CAP_BUF` | base = {rb[14], rb[9:0]} (11 bits), rb[13:10] *n*: buffer of 2^(*n*+1) words (*n* ≥ 10: all 2,048), the size-aligned block containing base. Reset: 512, 256 words |
| 3 | `CAP_DIV` | cycles per sample (0 = 1). Reset 1 |
| 4 | `CAP_TRIG` | rb[7:0] mask, rb[15:8] value: the condition is (pins & mask) == value |
| 5 | `CAP_CTL` | rb[9:0] *post*, bit12 edge trigger, bit13 record `ui` (else `uio`), bit14 change mode, bit15 arm (0 stops) |

* Writing `CAP_CTL` with bit 15 set arms it: from the next cycle it takes a
  sample every *div* cycles (pins as seen two cycles earlier, like any
  input) and records continuously, wrapping round the buffer, so the words
  before the trigger are kept.
* **Sample mode:** two samples a word, the earlier in the low byte.
  **Change mode:** a word {pins, ticks since the last word} at the first
  sample, at every sample that differs from the last word's, and after
  255 samples without a change.
* **Trigger:** level (the condition holds) or edge (it holds, and did not
  at the previous sample; after arming it must be seen false first). The
  word holding the first such sample is the trigger word; *post* more
  words are recorded after it, then the engine stops (done). Mask 0
  triggers on the first sample.
* It writes SRAM port B only in cycles the core does not use it, through
  a two-word queue, so **recording never changes any thread's timing**. A
  word that finds the queue full is lost (its slot keeps the old content)
  and sets overflow: two samples a word every cycle leave the core every
  other cycle for data, so only bursts of `LD`/`ST`/`MEM_W`/FIFO longer
  than about 4 cycles cost samples.
* `CAP_STAT`: bit15 recording, bit14 triggered, bit13 done, bit12 overflow,
  bit 11 0, bits 10:0 the address the next word goes to (after done: the oldest
  word, once the buffer has wrapped). `CAP_TPOS`: the trigger word's address.
  (v4: both 11 bits; bit 10 of CAP_STAT was always 0 before.)

### 6.7 Input glitch filter
`FILTER rb` (port 6): rb[7:0] the `uio` pins to filter, rb[11:8] *len*
(0 = 1). A filtered pin's level changes only once its synchronised input
has held the new level for *len* cycles in a row: shorter pulses are
removed, and every change it passes arrives *len* cycles late. Everything
that reads `uio` (`PIN_R`, `WAIT_EDGE`, the SR input, the RX engine) sees
the filtered pins; the capture engine records the pins as they are, so a
logic analyser still shows the glitches. Reset: no pin filtered (the
timing of §1 and §5 holds). Writing `FILTER` restarts the filters at the
pins' present levels.

### 6.9 Engine options (v4, port page 2)

Page 2 (`ESEL` bits 3:1 = 2: `ESEL #4` for set 0, `#5` for set 1) holds the options of the TX
and RX engines of the thread's set. Everything is 0 at reset and after a `TX_CFG`/`RX_CFG`
write leaves it alone (an option outlives a reconfiguration); with every option 0 the engines
behave as in v3.

| Port | `PW` | `PR` |
|---|---|---|
| 0 | `TX_OPT rb` | `TX_OPT_RD ra` |
| 1 | `RX_OPT rb` | `RX_OPT_RD ra` |
| 2 | `ERR_CLR rb`: clear the error flags that are 1 in rb[2:0] | `ERR_STAT ra`: ra ← {E_UNDER, E_STUFF, E_FRAME} in bits 2:0 |
| 3 | `TX_EXT rb` | `TX_EXT_RD ra` |
| 4 | `RX_EXT rb` | `RX_EXT_RD ra` |

**General stuffing** (`TX_OPT` / `RX_OPT` bits 4:0, for NRZ and NRZI): *n* = bits 3:0 (1–15; 0
means the classic rule if `TX_CFG` / `RX_CFG` bit 6 is set, else no stuffing), *kind* = bit 4.
Kind 0: after *n* consecutive 1 data bits a 0 is inserted (HDLC *n* = 5, USB *n* = 6, the v3
rule is *n* = 6). Kind 1: after *n* equal bits the opposite bit is inserted, and the inserted bit
counts as the first of the next run (CAN *n* = 5). Runs continue across words. The receiver drops
the stuff bit; if it is not what the kind requires (kind 0: not 0; kind 1: equal to the bit before)
it sets **E_STUFF**, and drops it anyway (an HDLC flag, which has six 1s, shows as a stuff error
and tells firmware where frames start).

**Errors** are sticky per engine set until `ERR_CLR` or a `TX_CFG` / `RX_CFG` write of the engine
that set them (TX: E_UNDER, RX: E_FRAME, E_STUFF); `STAT` bit 15 is their OR for the thread's
set (`RX_OVERRUN`, `STAT` bit 3, stays separate).
* **E_FRAME**: `RX_OPT` bit 5 (character mode only): the word completes at the centre of the stop
  bit, one bit later than without, and the stop bit must be 1; if it is 0 the word is delivered
  all the same and E_FRAME is set. Without the bit the receiver never looks at the stop bit (v3).
* **E_UNDER**: `TX_OPT` bit 5 (*stream*): the engine finishes a word with no next word waiting
  and sets E_UNDER. A program that streams sets the bit with its first push and clears it when
  it pushes the last word, so the end of a message is not an error.

**External clock** (`TX_EXT` / `RX_EXT`, same layout): the engine shifts on edges of a pin
instead of its divider, so a protocol whose clock comes from the other side runs word by word.

| Bits | Field |
|---|---|
| 2:0, 3 | clock pin, bank (1 = `ui`) |
| 4 | active edge: 0 rising, 1 falling |
| 5 | enable (0: the engine uses its divider, as in v3) |
| 6 | TX only: **pre**: present the first bit as soon as the gate is active, not at the first edge |
| 10:8, 11 | gate pin, bank |
| 12 | gate polarity: 1 = active while the pin is high |
| 13 | gate enable (0: always active) |

* The pins are the synchronised ones, so an edge on the pad in cycle *t* is seen in *t* + 2.
* **RX:** on every active edge while the gate is active, the engine takes the data bit (the
  `RX_CFG` pin, NRZ, the `RX_CFG` width and order) and the word completes after `width` edges
  (`rx_avail`; overrun as usual). When the gate goes inactive a partial word is dropped. No
  divider, no start bit, no resync.
* **TX:** on every active edge while the gate is active, the engine presents the next bit of the
  word on its pin (`TX_CFG` NRZ, width, order, idle level, release): the line level of cycle *t* + 2
  is on the pad from *t* + 3. With **pre** the first bit of the first word goes out as soon as the
  gate is active (SPI mode 0 and 3, CPHA = 0: shift on the opposite edge to the one the master
  samples with), otherwise on the first edge (CPHA = 1). With no word to present the line keeps its
  level (E_UNDER with *stream*). When the gate goes inactive the shifter is cleared; the word in the
  buffer stays for the next time. With `TX_CFG` release the engine owns its pin only while the gate
  is active (a tri-stated MISO). `tx_busy` is the gate being active, or data pending.
* Limits, measured in simulation (`test_external_clock_speed_limits`; a real pad needs margin on
  top): the receiver takes a master clock of 2 cycles a bit (the pins and the clock go through the
  same synchroniser, so a bit and its edge arrive together). The transmitter's bit is on the pad 3
  cycles after the edge, so a master that samples half a period after its shifting edge is
  answered correctly from 6 cycles a bit (half = 3, no margin) and not at 4; the tests use 8
  (half = 4, one cycle of margin: 6 MHz at 50 MHz).

### 6.10 Identity and the 32-bit counter (v4, port page 3)

Page 3 (`ESEL #6`) is read only (a `PW` does nothing):

| Port | `PR ra,#p` |
|---|---|
| 0 | `CHIPID`: 0x4D53 (`MS`) |
| 1 | `CHIPVER`: 0x0400, the ISA version (major in bits 15:12, minor in 11:8) |
| 2 | `CHIPCAPS`: capabilities, bit 0 timed pins, 1 second engine set, 2 external clock, 3 general stuffing and error flags, 4 engine options, 5 the 32-bit counter, 6 ROM memory test (not built: the ROM has no room, so 0), 7 ROM autobaud; bits 15:8 = log2 of the SRAM words (11); the value is 0x0BBF |
| 3 | `CNT_H`: the high 16 bits of the 32-bit cycle counter (the low half is `CNT`) |

On v2.2 and v3 `ESEL` is a no-op and port 0 of page 0 is `FIFO_GET`, so a host that clears
a register, selects page 3 and reads port 0 gets 0 there (an empty FIFO leaves it unchanged)
and 0x4D53 here: that tells the generations apart. The counter's high half moves when the low half
wraps, so a reader that wants a coherent 32-bit time reads high, low, high and repeats if the
two highs differ. `WAIT_CMP` still works on the low half; the high half is for long timeouts and
timestamps made in firmware (a time 2^32 cycles is 86 s at 50 MHz).

### 6.8 Timed pins (v4, port page 1)

A thread's edges are exact only if the instruction that makes one runs on the right
cycle, and with *n* threads a thread owns every *n*-th cycle. Timed pins make an edge
independent of that: the thread says when, and the hardware makes the edge on that
cycle or not at all. (Prior art: XMOS's timed ports; the refusal is ours.)

Page 1 (`ESEL #2`, §3.2):

| Port | `PW` | `PR` |
|---|---|---|
| 0 | `AT rb`: arm this thread's event slot with the time rb | `TS ra`: ra ← the time of the event that released this thread's last `WAIT_EDGE` / `WAIT_CMP` |
| 1 | 0 | `AT_STAT ra`: ra ← {bit 0 armed, bit 1 held, bit 2 refuse-next} (no side effect) |

* **`TS`.** The value of `CNT` in the cycle the condition became true: for an edge, the
  first cycle in which the synchronised pin differs; for a level, the first stall cycle in
  which it holds; for `WAIT_CMP`, the target. It is *not* the cycle the thread was
  released in, which may be later (other threads' slots, the thread's own). A wait that
  ends by timeout or by a late target has no event: `TS` is then `CNT` in its release
  cycle, and `Z` is 1 as always. Reset 0.
* **`AT`.** Each thread has one event slot. `AT rb` arms it: the thread's **next pin
  write** is not applied in its own cycle but at the cycle in which `CNT` equals the
  time rb, and is on the pad from the next cycle, exactly as if an instruction had made
  it in that cycle. The thread does not wait. Pin writes are `PIN_W`, `SETM`/`CLRM`
  selectors 0–5, `OUT_W`, `SHIFT_OUT` and `XFER` (the shift register moves at once; only
  the pin write, `OUT[po]` ← the bit, is timed). `AT` is consumed by that one write.
* **On arrival** of the pin write (cycle *c*), with *behind* = (`CNT` − time) mod 2^16:
  * *behind* = 0: the time is now. The write is applied now, as an untimed write.
  * 1 ≤ *behind* ≤ 255: the time has passed (the test of a late `WAIT_CMP`). **Refused:**
    the write is not made at all, `Z` = 1, the thread's LATE flag is set (so the late
    guard, §5, acts on a program that cannot keep its time), and the slot is empty.
  * otherwise (the time is 1 to 65,280 cycles ahead): the write is held; `Z` = 0.
  * The slot already holds an event (an earlier timed write whose time has not come):
    the `AT` that armed this write set *refuse-next*, so the write is **refused** as
    above. A slot holds one event; a program that wants two keeps them apart.
  `Z` is 1 after a refusal and 0 after an acceptance, so a program can test it; a pin
  write with no `AT` armed leaves `Z` alone.
* **Application.** In the cycle where `CNT` equals a held event's time, the write is
  applied, whatever its thread is doing (running, stalled on a wait, in a bubble, or
  waiting for a slot that comes later). Events of several threads in one cycle are all
  applied, T3's first and T0's last, so on one bit the lower thread's write wins and
  toggles stack; then the pin write of the instruction issuing in that cycle, if any.
  An event is applied to the registers `OUT`/`OE`/`UO` (so the late guard and the
  TX engine's override act on it as on any write) and held in the slot as the operation
  it was (set, clear, toggle or write, with its mask), not as a value, so writes made in
  between by other threads are not undone.
* `TSTOP` of a thread cancels its held event (one that is due in the same cycle is still
  applied); `TSTART` of a thread (also a thread restarting itself) clears its slot. A
  thread's `AT` leaves the other threads' slots alone.
* **Why it is a property of the ISA:** the slots are four small state machines that
  depend on `CNT` and the thread's own instructions, never on the other threads, so the
  edge lands on its cycle with four threads running, one stalled, or one stopped
  (formal F17).

## 7. Threads

Four threads, T0–T3. T0 always runs; T1–T3 are started and stopped by any
running thread. `TSTART` and `TSTOP` name the thread in the ra field (1–3;
0 means T1, so the v2.0 encodings `TSTART rb` / `TSTOP` keep their meaning).

* `TSTART #t,rb`: thread *t* gets PC=rb, R0–R7=0, flags 0, LATE 0, no
  pending op, the starting thread's `TMO`, and runs. If it was running it is
  restarted (a thread may restart itself).
* `TSTOP #t`: thread *t* stops at the end of the cycle (a thread may stop
  itself; stopping a thread that is not running does nothing).
* **Single mode** (only T0 runs) has exactly the v1 timing (§5).
* **Multi-thread mode** (T0 and at least one of T1–T3 running): each cycle
  belongs to one running thread, the next running one after the previous
  cycle's, in the order T0, T1, T2, T3, T0, …. With *n* threads running,
  each owns every *n*-th cycle. A thread's slots therefore depend only
  on which threads are running, never on what they do, and so does the
  timing of everything it does on its own. The TX, RX and CRC engines are
  shared (below): a blocking op on an engine another thread is using waits
  for that thread (`test_shared_engine_wait_depends_on_other_thread`).
* **When `TSTART` / `TSTOP` changes the running set** in cycle *c*, cycle
  *c*+1 belongs to the thread that would have owned it before the change,
  if it still runs and was not just restarted; otherwise to the next
  running thread after *c*'s owner, and *c*+1 is a bubble for it (it
  issues nothing, though a stalled op may still complete). For two
  threads this gives:
  * T0 starts T1 in single mode (cycle *c*): T0 issues again at *c*+1, T1's
    first instruction at *c*+2 (no bubble).
  * T0 restarts a running T1 (*c*): T1's slot *c*+1 is a bubble, T1
    restarts at *c*+3. T1 restarting itself costs nothing (it issues at
    *c*+2).
  * T1 stops itself (*c*): single mode from *c*+1, T0 issues at *c*+1.
  * T0 stops T1 (*c*): single mode from *c*+1, one bubble, T0 issues at
    *c*+2.
  and, for instance, with T0 and T1 running, T0 starting T2 at *c* costs
  nothing: T1 issues at *c*+1, T2 at *c*+2, T0 at *c*+3.
* Shared: memory, pins, SR, MEM_W pointer, FIFO, peripherals, CNT. A thread's
  peripheral transfers happen only in its own slots, so they never collide.
  Each thread has its own `TMO`.

## 8. Debug view

Holding `ui_in[2]` high while `rst_n` is low puts the chip in debug mode
until the next reset. `uo_out` then shows one byte of internal state chosen
by `ui_in[7:2]` on the page chosen by `ui_in[1]`, instead of `UO`; the
firmware runs as usual (it still sees
`ui_in` as inputs). The select acts without a clock edge, so a host that
clocks the chip one cycle at a time (the demo board's RP2040 can) reads any
view between steps: halt, single-step, breakpoints on a PC and register
inspection need nothing more on the chip.

Page 0 (`ui_in[1]` = 0):

| `ui_in[7:2]` | `uo_out` |
|---|---|
| 0x00–0x0F | T0 register R(sel[3:1]), low byte if sel[0]=0, high if 1 |
| 0x10–0x1F | T1 register, same layout |
| 0x20 | PC of the last instruction issued, bits 7:0 |
| 0x21 | bits 1:0 that PC's bits 9:8 · bit2 its thread, bit 0 · bit3 multi-thread mode · bit4 T0 stalled · bit5 T1 stalled · bit6 its thread, bit 1 · bit7 that PC's bit 10 |
| 0x22, 0x23 | that instruction's word, low / high byte |
| 0x24 | bit0 Z(T0) · bit1 N(T0) · bit2 Z(T1) · bit3 N(T1) |
| 0x25, 0x26 | `CNT` low / high byte |
| 0x27, 0x28 | `SR` low / high byte |
| 0x29, 0x2A, 0x2B | `OUT`, `OE`, `UO` |
| 0x2C, 0x2D | FIFO count[7:0]; {drops[6:0], count[8]} |
| others | 0 |

Page 1 (`ui_in[1]` = 1):

| `ui_in[7:2]` | `uo_out` |
|---|---|
| 0x00–0x0F | T2 register R(sel[3:1]), low byte if sel[0]=0, high if 1 |
| 0x10–0x1F | T3 register, same layout |
| 0x20 | bits 3:0 stalled threads (bit *t* = T*t*) · bits 7:4 running threads |
| 0x21 | {N, Z} of T3, T2, T1, T0 (bits 7:6 … 1:0) |
| 0x22–0x25 | T0–T3 fetch pointer, bits 7:0 (a running thread's: its next instruction; T0 alone: one ahead) |
| 0x26 | the four pointers' bits 9:8 (T3 in bits 7:6 … T0 in 1:0) |
| 0x27 | the four pointers' bit 10 (T3 in bit 3 … T0 in bit 0) |
| others | 0 |
