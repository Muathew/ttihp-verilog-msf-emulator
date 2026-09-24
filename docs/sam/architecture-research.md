# Architecture Research — Reprogrammable Protocol-Emulator Core (private)

> Subagent research report (2026-09-24). Sources: RP2040 Datasheet
> (`datasheets.raspberrypi.com/rp2040`), pico-examples PIO (`github.com/raspberrypi/pico-examples/pio/*`),
> TI PRU-ICSS TRM (`ti.com/lit/gpn/sprui673`), PicoRV32 (`github.com/Yves-Beziau/picorv32`),
> Tiny Tapeout rules (`tinytapeout.com`), Hardcaml / Advent-of-FPGA (`hardcaml.org`,
> `blog.janestreet.com/advent-of-fpga-challenge-2025-results`).

**Scope:** IHP 130nm, ~24K logic cells (target ≤18K), 8 bidirectional I/O pins, ~12 MHz.
Goal: a tiny CPU whose ISA is built for pin R/W + cycle-accurate timing, so protocols
(UART/SPI/I2C required; USB-1.0-LS / JTAG / SWD / PS2 / CAN stretch) are **firmware**,
not fixed logic, and the chip stays **reprogrammable after fab**.

Cycle budget anchor: **12 MHz → 83.33 ns/cycle.**

| Protocol | Bit time | Cycles/bit @12 MHz | Feasible? |
|---|---|---|---|
| UART 115200 | 8.68 µs | 104 | ✅ (0.17 % drift) |
| SPI 1 Mbit | 1 µs | 12 | ✅ |
| I2C 100 kHz | 10 µs | 120 | ✅ |
| I2C 400 kHz | 2.5 µs | 30 | ✅ |
| JTAG @1 MHz | 1 µs | 12 | ✅ (4-wire fits) |
| SWD @1 MHz | 1 µs | 12 | ✅ (2-wire) |
| PS/2 @10 kbit | 100 µs | 1200 | ✅ trivial |
| CAN 1 Mbit | 1 µs | 12 | ✅ w/ bit-stuffing aid |
| USB-1.0-LS 1.5 Mbit | 667 ns | 8 | ✅ (stretch, tight) |
| 10 Mbit Eth | 100 ns | 1.2 | ❌ (drop; <2 cycles/bit + pin count) |

**Bottom line up front:** Use a **hybrid** — a small RISC "brain" (F1 footprint) **+** a
**hardware serial shift datapath** **+** **event-driven stall instructions**
(`WAIT_EDGE`, `WAIT_CMP`) **+** **open-drain I/O** **+** a **serial bootloader in ROM**.
This is exactly "what we'd do differently from PIO/PRU": PIO has no general register
file/ALU and no branches; PRU has no shift datapath and no pin-edge waits. We take the
*best* of both and add the two things neither has (event-driven stalls + a post-fab
boot path).

---

## 1. RP2040 PIO Deep Dive

**Architecture (per state machine, ×4 per PIO block):**
- **32-bit Output Shift Register (OSR)** + **32-bit Input Shift Register (ISR)**. These
  are the "shift datapath." Configurable shift direction, auto-push/auto-pull thresholds,
  FIFO join (a block can pool its 4 SMs' FIFOs into one 8-deep FIFO).
- **Instruction set (single-instruction, 1-cycle):** `SET` (8-bit value → pins/ISR/OSR/X/Y),
  `OUT` (shift OSR → pins/X/Y/OSR/ISR/null), `IN` (sample pins/X/Y/ISR → ISR), `PUSH`
  (ISR → RX FIFO), `PULL` (TX FIFO → OSR), `MOV` (move between X/Y/OSR/ISR/pins),
  `JMP` (conditional on X/Y/pin/OSR-empty/ISR-nonempty), `WAIT` (stall on pin level or
  IRQ), `DELAY` (fixed n-cycle stall), `IRQ` (raise/clear).
- **Per-bit vs per-cycle control:** `clkdiv` (fractional, 16.16) scales the SM clock;
  `DELAY` gives fixed-cycle stalls; `WAIT` gives event stalls. A bit is typically 8 SM
  cycles (see UART below).
- **Pin direction control:** `SET pindirs` writes the per-pin direction register directly
  — this is how **open-drain** is done (drive the pin, then release it so a pull-up wins).
- **IRQs:** per-SM IRQs (FIFO thresholds, SM-0–SM-3, SM-4–SM-7) → system INTC.
- **Auto-idle:** when the OSR empties / ISR fills and no data is pending, the SM idles
  (e.g. UART TX holds the line high).

**Concrete implementations (pico-examples, `raspberrypi/pico-examples`):**
- **UART TX** (`pio/uart_tx/uart_tx.pio`): `PULL` (stall on empty, asserts stop/idle) →
  `SET x,7` (bit counter, asserts start) → loop `OUT pins,1` + `JMP x--` ×8.
  **1 bit = 8 SM cycles**; `clkdiv = sysclk/(8·baud)`. FIFO-join-TX gives an 8-deep queue.
- **UART RX** (`pio/uart_rx/uart_rx.pio`): `WAIT 0 pin` (start bit) → `SET x,7` → loop
  `IN pins,1` + `JMP x--` ×8 → stop-bit check via `JMP pin`. Auto-push threshold 8.
  Same 8-cycles/bit.
- **SPI** (`pio/spi/spi.pio`): full-duplex, SCK on a **side-set** pin, MOSI = `OUT`,
  MISO = `IN`. Separate programs for CPHA=0/1; **CPOL done in the IO-mux inversion**
  (not the SM). Auto chip-select variant asserts CSn on FIFO non-empty. 4-cycle SCK period.
- **I2C** (`pio/i2c/i2c.pio`): the most instructive. **Open-drain via `SET pindirs` +
  inverted OE override** (`GPIO_OVERRIDE_INVERT` on the OE output) — drive-low by
  asserting direction, release by clearing it so the pull-up wins. Handles **clock
  stretching** (`WAIT 1 pin` on SCL), **ACK/NAK** (`JMP pin` on SDA), and lets the host
  CPU inject START/STOP/RESTART as FIFO "instruction records." 32 SM-cycles per bit
  (100 kHz).

**Limits (the part that matters for us):**
- **4 SMs per block** (8 total on RP2040). Each is a *fixed* shift engine — you cannot
  add a 5th protocol without stealing an SM.
- **32-bit shift register** width; no general register file — the only "registers" are
  X, Y, OSR, ISR. **No ALU.** Any CRC, framing, or arithmetic must be done by the host
  CPU, not the SM.
- **No real branches** — `JMP` is a single conditional jump to a fixed target; you
  cannot build a general state machine *inside* one SM. Complex protocols (JTAG TAP
  state machine, USB token/handshake) are painful.
- **Timing is "hard" per-SM** (clkdiv + DELAY) but **there is no free-running global
  cycle counter you can read** — each SM is its own island. Cross-protocol coordination
  (e.g. "I2C SCL must not toggle while USB is mid-bit") is awkward.
- **No boot path** — the SM program is loaded by the host CPU at boot; the chip is not
  reprogrammable on its own.

**What PIO does well:** area-efficiency (a UART is ~10 instructions), cycle-accuracy,
open-drain, and per-bit control. **What it does poorly:** general logic, >4 concurrent
protocols, and post-fab reprogramming. That's the gap our "brain + shift + event"
hybrid fills.

---

## 2. TI PRU Deep Dive

**PRU-ICSS (AM33xx / AM57xx / AM6x) — the "cycle-accurate RISC" reference:**
- **Register file:** 16 × 32-bit — `R0–R13` general, `R14 = SP`, `R15 = PC`. No data
  memory; the *only* storage is these 16 registers + MMIO.
- **Instruction format:** 32-bit, single-cycle execute. Opcode + 16-bit immediate + two
  4-bit register fields. **No load/store** — data movement is register↔register and
  register↔MMIO.
- **Cycle-accurate:** every instruction is exactly 1 cycle; the unit is deterministic and
  used precisely for that (Ethernet, CAN, industrial timing).
- **Memory-mapped I/O:** `MMW`/`MMR` read/write external GPIO/peripheral registers.
  This is how it touches pins and protocol peripherals.
- **No branches:** control flow is `JUMP` (unconditional, via immediate) and
  **conditional *moves*** (e.g. "if condition, move value into PC") — branches are
  emulated, not native.
- **Boot path (what makes it reprogrammable):** a **1 KB boot ROM** runs at reset; the
  host CPU hands firmware to the PRU over **mailboxes** (8 mailboxes for
  inter-processor comms). The PRU then executes host-loaded firmware. This is the model
  for our "serial bootloader."

**Why PRU is reprogrammable:** ordinary (if branch-poor) assembly, a real register file,
MMIO to any pin, and a host-loadable firmware image. **Weaknesses for us:** no shift
datapath (serial in/out is manual bit-shifting in the ALU), no pin-edge event waits
(timing is MMIO-poll loops), 16 registers is tight, and MMIO is multi-cycle (not
cycle-accurate at the pin edge).

**PRU vs PIO in one line:** PRU = "RISC brain, no shift, no events"; PIO = "shift
engine, no brain, no events." **We want brain + shift + events.**

---

## 3. Other Prior Art

| Design | What it does well | Poor for our constraints |
|---|---|---|
| **AVR / 8051 bit-banging** (the classic convention) | Proves *any* protocol can be firmware on a tiny core; huge body of reference code; `IN`/`OUT` + `DELAY` is the mental model we inherit. | Loop-delay timing drifts; no open-drain on most pins; no cycle counter. |
| **PicoRV32** (Yves-Beziau, `github.com/Yves-Beziau/picorv32`) | Tiny RV32I, ~1.5–2.5K gates, single-cycle, optional mul/div + IMEM/DMEM + interrupts. Proves a *real* RISC core fits in a few K cells. | Full RV32 is overkill (32-bit regs, 32-bit instr) for 8 pins; no pin/timing instructions; no shift datapath. |
| **ePAPR / soft-serial soft-cores** | Show serial I/O as a soft peripheral. | Usually fixed protocol, not reprogrammable. |
| **FPGA soft-cores as protocol emulators** (e.g. JTAG-to-serial bridges, logic-analyser PIO) | JTAG TAP state machines, bit-banged bridges. | FPGA area is not our budget; fixed logic, not firmware. |
| **Academic "reconfigurable protocol processor" papers** | The "protocol in firmware" thesis; table-driven protocol engines. | Table-driven = fixed opcode set; not a general CPU. |

**Takeaway:** the *convention* we should steal is AVR/8051 bit-banging (protocol =
firmware over `IN`/`OUT` + timing), the *datapath* we should steal is PIO's shift
register, and the *brain* we should steal is PRU's (small RISC + MMIO + boot ROM).
PicoRV32 confirms a RISC brain fits in our budget.

---

## 4. Key Design Axes (ranked for reprogrammability)

Ranked by how much each axis determines "can we add a *new* protocol post-fab with
*no RTL change*":

1. **(d) Program-memory size + boot/load path** — *the* reprogrammability axis. If
   firmware can't be loaded post-fab, nothing else matters. A serial bootloader +
   ≥512-word IMEM is the single biggest lever.
2. **(a) General RISC vs PIO-shift vs hybrid** — determines how much *new protocol
   logic* (CRC, state machines, framing) firmware can express. Hybrid (RISC brain +
   shift datapath) is the most flexible.
3. **(b) Timer architecture** — free-running counter + compare-and-stall beats
   "multiple timer channels" (fixed) and beats "loop delays" (drift). Event-driven
   `WAIT_CMP`/`WAIT_EDGE` is what makes timing *cycle-accurate* and *robust*.
4. **(c) Open-drain / bus-sharing I/O** — mandatory for I2C, USB, SWD, and any
   multi-master bus. Without it, half the stretch protocols are impossible.
5. **(f) Shift datapath vs ALU-only** — a hardware serial in/out register makes
   UART/SPI/JTAG/PS2 *trivial* and frees the ALU for CRC/framing. ALU-only works but
   is slower and more firmware.
6. **(e) Clock strategy for cycle accuracy** — a single free-running 16-bit cycle
   counter (readable, comparable) is the cheapest correct answer; a fractional divider
   (PIO-style) is only needed if we chase non-integer bauds.

---

## 5. "What We'd Do Differently" — concrete novel ideas

| # | Idea | What it enables | Area cost | Risk |
|---|---|---|---|---|
| 1 | **Event-driven stall instructions** — `WAIT_EDGE pin` (stall until a pin transitions) and `WAIT_CMP` (stall until free-running counter == imm). A hardware-assisted bit-bang loop. | Cycle-accurate timing *without* `DECR`/`JNZ` delay loops; CPU is free to run CRC/state-machine while waiting; robust RX (sample on edge, not fixed delay). This is the single most valuable "different from PIO/PRU" feature. | ~0.5–1 K cells (edge detector + counter-compare + stall mux) | Stall logic must handle "edge already happened" (level vs edge) and a **timeout** (else a hang = dead chip). |
| 2 | **Programmable bit-bang micro-sequencer** — a tiny hardware sequencer that steps through a bit-pattern table (per-bit SCK/CS/data schedule). | USB bit-stuffing, CAN bit-stuffing, Manchester — things that are painful in pure firmware. | ~1–2 K | Complexity + verification; only needed for stretch. Make it *optional* (compile-out). |
| 3 | **Hardware bit-stuffing / CRC assist** (optional blocks) | USB-1.0, CAN, Ethernet-FCS without firmware cost. | ~1–2 K each | Area; gate behind the stretch goal. |
| 4 | **Serial bootloader in ROM** (UART RX, ~64–128 words) | **Genuine post-fab reprogramming** — the chip's whole point. New protocol = new firmware image over a pin. | ~1–2 K (ROM + UART-RX) | Must be bulletproof (it's the only way in); timing must close at 12 MHz. |
| 5 | **Protocol "libraries" in firmware** (shared CRC, framing, TAP state machine) | More protocols in the same IMEM; JTAG/SWD/PS2 become firmware with *zero* RTL change. | 0 (firmware) | IMEM pressure — argues for 512 words, not 256. |

**Net:** ideas 1 + 4 + 5 are the core (cheap, high value). Ideas 2 + 3 are stretch-only
and should be *parameterizable out* to protect the 18 K budget.

---

## 6. Recommendation — concrete architecture

**A hybrid "brain + shift + events" core, F1.5 footprint:**

- **Brain (RISC):** 8 × 16-bit regs, 16-bit instructions, single-cycle
  fetch/decode/execute. ALU: `ADD/SUB/AND/OR/XOR/SHL/SHR`, `MOV`, `LDR`,
  `INCR/DECR`, flags `Z/N`. Branches: `JMP`, `JZ/JNZ/JP/JN`. `CALL/RET` (1-level, R7
  as return reg — cheap, adopt now).
- **Pin I/O (cycle-accurate):** `PIN_W` (whole 8-bit word → out-stage reg, latched next
  posedge), `PIN_R`, `IN_RD` (dedicated `ui_in`), **`OE_SET imm8` / `OE_CLR imm8`**
  (open-drain: drive-low by set+0, release by clear so pull-up wins). 1-cycle pad
  latency is the timing contract.
- **Timing (event-driven):** 16-bit **free-running cycle counter** + `CNT_R` (read) +
  **`WAIT_CMP imm`** (stall until counter==imm) + **`WAIT_EDGE pin`** (stall until pin
  transition, with timeout). This is the "hardware-assisted bit-bang" — bit times become
  *exact* and the CPU idles in hardware.
- **Shift datapath:** 16-bit **hardware serial in/out register** (MSB-first, direction
  configurable) with `SHIFT_OUT` / `SHIFT_IN` instructions. Makes UART/SPI/JTAG/PS2
  one-shift-per-bit and frees the ALU for CRC.
- **I/O map (8 `uio` bits, firmware-owned):** bit0 TX, bit1 RX, bit2 SCK, bit3 MOSI,
  bit4 MISO, bit5 SDA, bit6 SCL, bit7 stretch (USB/SWD/PS2). Open-drain via `OE_*`.
- **Memory:** **512 × 16-bit IMEM** (1 KB) — 256 is too tight for bootloader + 3
  protocols; 512 keeps JTAG/SWD/PS2 as firmware. FF-based SRAM (safe for the flow).
- **Boot path:** **ROM-resident UART-RX bootloader** (~64–128 words) → loads firmware
  into IMEM over a pin → jumps to 0. This is what makes the chip reprogrammable after
  fab.
- **Clock:** single 12 MHz clock; free-running counter; **no** fractional divider
  (104-cycle UART bit is fine; document the 0.17 % drift).

**Area budget (est., IHP 130nm, gate-equivalent cells):**

| Block | Cells |
|---|---|
| RISC core (fetch/decode/ALU/8-reg RF/branches) | ~3–4 K |
| 512×16 FF-SRAM | ~1.5–2 K |
| Pin I/O (out-stage, OE, in-sync, open-drain) | ~0.5 K |
| Cycle counter + `WAIT_CMP`/`WAIT_EDGE` stall | ~0.5–1 K |
| 16-bit serial shift datapath | ~0.5–1 K |
| ROM bootloader (UART-RX) | ~1–2 K |
| **Total** | **~7–10 K** |

→ **~8 K typical, ≤10 K worst case** — comfortably under the 18 K ceiling, leaving P&R
headroom.

**Justification against the 6 axes:**
- **(a) Hybrid** — RISC brain (general protocol logic) + PIO-style shift datapath
  (serial I/O). Beats pure RISC (no shift) and pure PIO (no brain/branches).
- **(b) Free-running counter + compare-and-stall** — cycle-accurate, no drift, CPU
  free during waits. Beats loop-delays and fixed timer channels.
- **(c) Open-drain I/O** — `OE_SET/OE_CLR` + pull-up model → I2C, USB, SWD,
  multi-master CAN all possible.
- **(d) 512-word IMEM + serial bootloader** — the reprogrammability core; new protocol
  = new image, no RTL change.
- **(e) Single free-running counter** — cheapest correct cycle-accuracy; no divider
  needed at 12 MHz.
- **(f) Hardware shift datapath** — serial in/out makes UART/SPI/JTAG/PS2 trivial;
  ALU reserved for CRC/framing.

**Protocol coverage check:**
- **UART/SPI/I2C:** comfortable (104 / 12 / 120 cycles/bit; I2C open-drain via `OE_*`).
- **JTAG / SWD / PS2:** firmware-only, **no RTL change** (4-wire/2-wire fit in 8 pins;
  12 cycles/bit @1 MHz; PS2 trivial). ✅
- **USB-1.0-LS stretch:** 8 cycles/bit @12 MHz — *alive*, with the optional
  bit-stuffing block if area permits. ✅
- **10 Mbit Ethernet:** **drop** (1.2 cycles/bit + pin count). Document as out-of-scope.
- **CAN:** 12 cycles/bit + optional bit-stuffing block → stretch.

---

## Top 5 Decisions We Should Make

1. **Adopt the hybrid: RISC brain + hardware shift datapath + event-driven stalls.**
   (Resolves the `arch-compare.md` model question — it's *C (hybrid)*, but with the
   shift engine as a cheap serial register, not a full PIO SM.)
2. **Add `WAIT_EDGE` + `WAIT_CMP` (with timeout) to the ISA now** — this is the
   defining "different from PIO/PRU" feature and the cheapest way to get cycle-accurate,
   robust timing. Lock it in `isa.md` before M2.
3. **Make I/O open-drain (`OE_SET/OE_CLR` + pull-up model)** — non-negotiable for
   I2C/USB/SWD; cheap, adopt immediately.
4. **Go to 512-word IMEM + a ROM UART-RX bootloader** — this is what makes the chip
   genuinely reprogrammable post-fab (the competition's explicit goal). Budget ~1–2 K
   cells for the bootloader.
5. **Gate the bit-stuffing/CRC/micro-sequencer blocks behind the stretch goal**
   (compile-out by default) to protect the 18 K ceiling; keep the core at ~8 K.
