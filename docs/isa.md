# ISA Spec — `msfcore` (Protocol Emulator Core) v2.2

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

* 16-bit instructions, word addressed, 10-bit PC (0–1023, wraps).
* **Four hardware threads** T0–T3 (§7). Each thread has its own PC, R0–R7
  (16-bit), flags `Z`/`N`, blocking-op state and `TMO`. After reset only T0 runs.
* **Flags:** `ADDS`, `SUBS`, `INCR`, `DECR` set `Z` (result==0) and `N` (bit 15).
  Every **blocking op** (§5) sets `Z` = 1 if it ended by timeout, else 0, and
  leaves `N` alone. `FIFO_PUT` and `FIFO_GET` set `Z` = 1 when the FIFO was
  full / empty, else 0 (§6.5). Nothing else touches flags.
* **Cycle counter `CNT`:** 16-bit, free running, reset to 0.
* **Memory:** one 1024×16 address space, held in two 512-word SRAM banks
  (bank = address bit 9; crossing between them costs nothing).
  * Fetch: words 0–127 come from the **boot ROM**, 128–1023 from the SRAM.
  * Data (`LD`/`ST`/`MEM_W`/FIFO): always the SRAM, all 1024 words. SRAM 0–127 is
    therefore 128 words of data RAM that never collide with code; 128–1023 is
    code (self-modifying writes are allowed but must be ≥ 2 instructions ahead
    of the write in single-thread mode, ≥ 1 slot in multi-thread mode).
* **Shift register `SR`** (16-bit) with configurable output/input pins (§4).
* **Pins:** `uio` (8 bidirectional: `OUT`, `OE`, input), `uo_out` (8 outputs:
  `UO`), `ui_in` (8 inputs).
* **Inputs are synchronised** (2 flip-flops): a pad change at cycle *t* is first
  visible to instructions executing at cycle *t+2* (*t+2+len* on a `uio` pin
  the glitch filter is turned on for, §6.7).
* **Outputs are registered:** a pin write executed in cycle *t* appears on the
  pad from cycle *t+1*.
* **Reset:** PC(T0)=0 (boot ROM), T1 stopped, all registers 0, flags 0, `OUT`=
  `OE`=`UO`=0, `SR`=0, `SER_CFG`=out 7/in 7/MSB-first, `TMO`=0xFFFF (both
  threads), `MEM_W` pointer = 128, FIFO empty at SRAM 64–127, peripherals
  disabled.

## 2. Encoding

`b15..b11` = opcode. Formats:

| Fmt | b10..b8 | b7..b5 | b4..b2 | b1..b0 | Notes |
|---|---|---|---|---|---|
| R | rd | rs | rt | 00 | |
| I | rd | imm8 (b7..b0) | | | zero-extended |
| B | rel9 (b10..b2), signed | | | 00 | target = addr+1+rel9 |
| A | imm11 (b10..b0) | | | | target = imm11[9:0] |
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
| 10010 | **SETM** `sel,#m` | I | b10..b8 = sel: 0 `OE_SET` OE\|=m · 1 `PIN_SET` OUT\|=m · 2 `PIN_TGL` OUT^=m · 3 `UO_SET` UO\|=m · 4 `UO_TGL` UO^=m · 5 `OE_TGL` OE^=m · 6,7 NOP |
| 10011 | **CLRM** `sel,#m` | I | 0 `OE_CLR` OE&=~m · 1 `PIN_CLR` OUT&=~m · 2 `PIN_WI` OUT←m · 3 `UO_CLR` UO&=~m · 4 `UO_WI` UO←m · 5 `OE_WI` OE←m · 6,7 NOP |
| 10100 | `CNT_R rd` | I | rd ← CNT |
| 10101 | `INCR rd` | I | rd ← rd+1; Z,N |
| 10110 | `DECR rd` | I | rd ← rd−1; Z,N |
| 10111 | `IN_RD rd` | I | rd ← {0, ui_in (synced)} |
| 11000 | `CALL imm11` | A | R7 ← addr+1; PC ← imm11[9:0] |
| 11001 | `RET` | — | PC ← R7[9:0] |
| 11010 | `WAIT_EDGE #c` | I | blocking, §5. c[2:0]=pin, c[3]=bank (0 uio, 1 ui), c[6:4]=cond: 0 any edge, 1 rising, 2 falling, 4 level 0, 5 level 1 (3,6,7 = any edge) |
| 11011 | `WAIT_CMP rs` | I | blocking until CNT == rs (rs in b10..b8) |
| 11100 | `SHIFT_OUT rs` | I | SR shifts out one bit to the SR output pin, shifts in rs[0] (§4) |
| 11101 | `SHIFT_IN` | — | SR shifts in the SR input pin (§4) |
| 11110 | `MEM_W rs` | I | SRAM[ptr] ← rs; ptr ← ptr+1 (10-bit wrap) |
| 11111 | `LDH rd,#imm8` | I | rd[15:8] ← imm8 (rd[7:0] kept) |

### 3.1 EXT group (opcode 01111, X format: `ra`=b10..8, `rb`=b7..5, `sub`=b4..0)

| sub | Mnemonic | Semantics |
|---|---|---|
| 0 | `SR_LD rb` | SR ← rb |
| 1 | `SR_RD ra` | ra ← SR |
| 2 | `XFER` | full-duplex SR shift: output bit out, input pin in (§4) |
| 3 | `SER_CFG rb` | rb[2:0] SR out pin, rb[5:3] SR in pin, rb[6] LSB-first |
| 4 | `OUT_W rb` | UO ← rb[7:0] |
| 5 | `MEM_A rb` | MEM_W pointer ← rb[9:0] |
| 6 | `LD ra,rb` | ra ← SRAM[rb[9:0]] — blocking, always 1 stall cycle/slot |
| 7 | `ST ra,rb` | SRAM[ra[9:0]] ← rb |
| 8 | `SWAP ra,rb` | ra ← {rb[7:0], rb[15:8]} |
| 9 | `JR rb` | PC ← rb[9:0] |
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
| 27 | `TSTART #t,rb` | (re)start thread *t* at rb[9:0] with cleared state (§7); *t* in the ra field, 1–3, 0 = T1 (`TSTART rb`) |
| 28 | `TSTOP #t` | stop thread *t* (§7); *t* as for `TSTART` (`TSTOP` = T1) |
| 29 | `TID ra` | ra ← executing thread id (0–3) |
| 30 | `PW #p,rb` | peripheral port *p* (0–7, in the ra field) ← rb (§6.5) |
| 31 | `PR ra,#p` | ra ← peripheral port *p* (0–7, in the rb field) (§6.5) |

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
| 2:0 | pin (uio index) |
| 3 | diff: also drive pin+1 (mod 8) with the complement |
| 5:4 | mode: 0 NRZ, 1 NRZI (0 = toggle), 2 Manchester (IEEE 802.3: 1 = low→high), 3 NRZ |
| 6 | bit-stuff: after six consecutive 1 data bits insert a 0 |
| 7 | LSB-first |
| 11:8 | word width − 1 (1–16 bits per push) |
| 12 | idle level (line level after enable; NRZI reference level) |
| 13 | enable (engine drives the pin(s), overriding `OUT`) |
| 14 | release: the engine drives its pins only while a symbol is on the line; before the first and after the last symbol (on the exact boundary) the pins show `OUT` |

Writing `TX_CFG` resets the engine (buffer and shifter empty, line ← idle).
A symbol lasts `TX_DIV` cycles (Manchester: one half-bit). The engine is
double-buffered: one word shifting, one in the buffer; back-to-back pushes give a
gapless stream. When it runs out of data the line keeps its last level.
`busy` = symbol in progress or data pending.

### 6.2 RX engine (`RX_CFG`)
| Bits | Field |
|---|---|
| 2:0 | pin (uio index, synced) |
| 5:4 | mode: 0 NRZ, 1 NRZI (no transition = 1), 2 Manchester (IEEE 802.3: low→high = 1; `RX_DIV` = half-bit) |
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
passed) · bit15 0.

### 6.5 Peripheral ports and the FIFO
`PW #p,rb` writes rb to port *p*, `PR ra,#p` reads port *p* into ra. Unused
ports: a write does nothing, a read gives 0.

| Port | `PW` (write) | `PR` (read) |
|---|---|---|
| 0 | `FIFO_PUT rb`: push rb | `FIFO_GET ra`: pop into ra |
| 1 | `FIFO_CFG rb`: rb[9:0] base, rb[12:10] *n*: size 2^(*n*+1) words (2–256); empties the FIFO, clears drops | `FIFO_STAT ra`: ra ← drops[6:0] << 9 \| count[8:0] (no side effect) |
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
| 2 | `CAP_BUF` | rb[9:0] base, rb[13:10] *n*: buffer of 2^(*n*+1) words, the size-aligned block containing base. Reset: 512, 256 words |
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
  bits 9:0 the address the next word goes to (after done: the oldest
  word, once the buffer has wrapped). `CAP_TPOS`: the trigger word's address.

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
| 0x21 | bits 1:0 that PC's bits 9:8 · bit2 its thread, bit 0 · bit3 multi-thread mode · bit4 T0 stalled · bit5 T1 stalled · bit6 its thread, bit 1 |
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
| others | 0 |
