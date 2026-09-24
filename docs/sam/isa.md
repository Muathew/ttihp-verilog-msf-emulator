# ISA Spec — `msfcore` (Protocol Emulator Core) (private, v1.0)

> ISA for the **hybrid core**: RISC brain + 16-bit serial shift datapath + event-driven
> stalls + open-drain I/O + ROM-resident UART-RX bootloader (F1.5 footprint: 8 regs,
> 16-bit instr, 512-word IMEM, ~12 MHz). All decisions from `design-questions.md`
> (24 Sep 2026) are folded in. All numbers in cycles refer to one `clk` edge.

## 1. Machine model

- 16-bit little-endian instruction words; word-addressed.
- **Registers:** R0–R7 (16-bit). R0 is not special (unlike ARM) — keep firmware simple.
- **Flags:** `Z` (result == 0), `N` (MSB of result). Affected **only** by
  `ADDS`, `SUBS`, `INCR`, `DECR`. All other instructions leave flags untouched.
- **PC:** 9-bit word pointer (0–511), wraps.
- **Cycle counter:** 16-bit free-running, resets with `rst_n`, increments every cycle.
  Read-only via `CNT_R`. This is the timing primitive for all protocol firmware.
- **Shift datapath:** 16-bit serial in/out register (OSR), MSB-first (Q69/Q76).
  `SHIFT_OUT rs`: OSR MSB → serial pin, OSR ← {OSR[14:0], r[rs][0]}. `SHIFT_IN`:
  OSR ← {OSR[14:0], serial pin}. Serial pin = bit 7 (stretch pin, §8 item 3).
- **Event stalls (Q68):** `WAIT_EDGE #imm8` (stall until a transition on pin
  `imm8[2:0]`) and `WAIT_CMP rs` (stall until cycle counter == r[rs], full 16-bit
  range — no immediate needed). Each has a 16-bit hardware timeout: on timeout the
  core resumes and sets Z, so a hang can't be a dead chip.
- **Reset:** PC ← 0 (bootloader ROM), R0–R7 ← 0, flags undefined, cycle counter ← 0,
  `uio_out` ← 0, `uio_oe` ← 0 (all pins input mode after reset).
- **Instruction memory:** 512 × 16-bit, Tiny Tapeout SRAM macro (Q48). Synchronous
  **registered** read — fetch at edge N, valid at N+1 (see §7).
- **Bootloader (Q71/Q77):** words 0–127 of IMEM hold a ROM-resident UART-RX
  bootloader (128 words). On power-up the core runs it; it loads a firmware image
  into IMEM words 128–511 over the UART RX pin (bit 1) with **CRC-8 + fail-safe
  halt/blink on corruption** (competitor-research rec #3), then jumps to 128.
  This is the "reprogrammable after fabrication" path.

## 2. Pin interface model (Tiny Tapeout)

| Signal | Width | Role in the emulator |
|---|---|---|
| `ui_in` | 8 | dedicated inputs, readable as a raw bus (R15-style special reg — see §5: read from `r_in`) |
| `uio_in` | 8 | `uio` pins read (when `uio_oe[a]=0`) |
| `uio_out` | 8 | driven value when `uio_oe[a]=1` |
| `uio_oe` | 8 | 0 = input (wire undriven), 1 = output |
| `uo_out` | 8 | done/status flag (Q21): firmware writes a status byte (e.g. bit0 = idle/done, bit1 = framing error) |
| `clk`, `rst_n`, `ena` | 1 | clock / async reset (active low) / always-1 power enable |

**Emulator GPIO abstraction:** pin `a[2:0]` (0–7) on the `uio` bank is one "emulator GPIO".
Firmware owns the mapping (e.g. pin 0 = UART TX, pin 1 = UART RX, pin 2 = SCK, ...).
`ui_in` is exposed as a readable special register (16-bit read = `{8'b0, ui_in}`).

**Cycle-accurate output register (essential to the project):**
`PIN_W` / `OE_SET` / `OE_CLR` issue to an **output stage register** that latches on the
next `posedge(clk)` after the instruction executes. Net effect: a pin change caused by
instruction at cycle *N* is visible on the pad at cycle *N+1* (pad register), i.e.
timing is deterministic to ±1 cycle and schedulable via the cycle counter. Inputs
(`uio_in`) are sampled at the same edge — no skid in the read path.

## 3. Instruction encoding (16-bit)

Bit positions: `b15..b11` = opcode (5b), then per format:
- **R format:** `b10..b8` = rd, `b7..b5` = rs, `b4..b2` = rt, `b1..b0` reserved (0).
  (Order: rd, rs, rt in operand order, left to right.)
- **I format:** `b10..b8` = rd, `b7..b0` = imm8 (zero-extended to 16 bits).
- **B format:** `b10..b2` = rel9 (signed, two's complement; offset from PC+1), `b1..b0` reserved (0).
- **A format:** `b10..b0` = imm11 (absolute).

All reserved bits must be 0.

### Opcode table

| op | mnemonic | format | semantics (exec in 1 cycle) |
|---|---|---|---|
| 00000 | `LDR rd, #imm8` | I | rd ← imm8 (zero-extended; 0–255). |
| 00001 | `ADDS rd, rs, rt` | R | rd ← rs + rt (16b); sets Z, N. |
| 00010 | `SUBS rd, rs, rt` | R | rd ← rs − rt; sets Z, N. |
| 00011 | `AND rd, rs, rt` | R | rd ← rs & rt. |
| 00100 | `OR rd, rs, rt` | R | rd ← rs \| rt. |
| 00101 | `XOR rd, rs, rt` | R | rd ← rs ^ rt. |
| 00110 | `SHL rd, rs, #imm3` | R* | rd ← rs << imm3 (b4..b2 = imm3). |
| 00111 | `SHR rd, rs, #imm3` | R* | rd ← rs >> imm3 (logical). |
| 01000 | `MOV rd, rs` | R | rd ← rs. |
| 01001 | `JMP #imm11` | A | PC ← imm11 (0–2047; IMEM alias: low 9 bits). |
| 01010 | `JZ rel9` | B | if Z: PC ← PC+1+rel9. |
| 01011 | `JNZ rel9` | B | if !Z. |
| 01100 | `JP rel9` | B | if !N (result non-negative). |
| 01101 | `JN rel9` | B | if N (result negative). |
| 01110 | `NOP` | — | nothing. Timing adjustment. |
| 01111 | `MEM_W rs` | I | SRAM[waddr] ← r[rs]; waddr ← waddr+1 (auto-increment, wraps 128→511). Used by the bootloader to stream firmware into IMEM words 128–511. |
| 10000 | `PIN_W rs` | I | out_stage ← r[rs]; `uio_out` commits out_stage on next posedge (see §2). Whole 8-bit word. |
| 10001 | `PIN_R rd` | I | rd ← `{8'h00, uio_in}` (8-bit read, zero-extended). |
| 10010 | `OE_SET imm8` | I | `uio_oe` ← `uio_oe | imm8` (drives listed pins to output). |
| 10011 | `OE_CLR imm8` | I | `uio_oe` ← `uio_oe & ~imm8` (releases listed pins to input, open-drain use). |
| 10100 | `CNT_R rd` | I | rd ← cycle counter. |
| 10101 | `INCR rd` | I | rd ← rd + 1; sets Z, N. |
| 10110 | `DECR rd` | I | rd ← rd − 1; sets Z, N. (countdown loops) |
| 10111 | `IN_RD rd` | I | rd ← `{8'h00, ui_in}` (dedicated input bus). |
| 11000 | `CALL` | — | r7 ← PC+1; PC ← PC+1 (1-level call, R7 = return reg). |
| 11001 | `RET` | — | PC ← r7. |
| 11010 | `WAIT_EDGE #imm8` | I | stall until a transition on pin `imm8[2:0]`; 16-bit timeout → resume + set Z. |
| 11011 | `WAIT_CMP rs` | I | stall until cycle counter == r[rs]; 16-bit timeout → resume + set Z. |
| 11100 | `SHIFT_OUT rs` | I | OSR MSB → serial pin; OSR ← {OSR[14:0], r[rs][0]}. |
| 11101 | `SHIFT_IN` | — | OSR ← {OSR[14:0], serial pin}. |
| 11110 | reserved | — | decodes as NOP (single-cycle, no state change). |
| 11111 | reserved | — | decodes as NOP (single-cycle, no state change). |

\* `SHL`/`SHR` shift amount is `b4..b2` (3 bits; shift ≥ 8 yields 0 — acceptable).
All I-format opcodes use `b10..b8` = register, `b7..b0` = imm8 (zero-extended).

## 4. GPIO bit map (draft, 8 pins on `uio`)

No hardware pin select exists: `PIN_W` moves the whole 8-bit register, so firmware
maps protocol signals to **bit positions** and uses `OE_*` for direction. Draft map:

| Bit | Signal | Protocol |
|---|---|---|
| 0 | TX | UART |
| 1 | RX | UART |
| 2 | SCK | SPI |
| 3 | MOSI | SPI |
| 4 | MISO | SPI (read) |
| 5 | SDA | I2C |
| 6 | SCL | I2C |
| 7 | stretch | JTAG/SWD/PS2/USB-LS + serial shift pin |

Open-drain (I2C): firmware sets SDA/SCL via `PIN_W` then **releases** them with
`OE_CLR` so pull-ups win; drives Low by `OE_SET` + 0. Two-wire half-duplex fits
naturally with one 8-bit datapath. (Final map confirmed in M1.3.)

## 5. Worked example — blink bit 0, half-period 50 µs @ 12 MHz (600 cycles)

> 50 µs = 600 cycles. imm8 caps a single `LDR` loop at 255, so 600 = 2×255 + 90
> (two 255-loops + one 90-loop). Firmware convention (M1.3): a `delay(count)` macro
> expands to the optimal `DECR`/`JNZ` loop chain. Sketch (single-level, count ≤ 255):

```asm
        LDR   R1, #255      ; loop count (imm8)
        LDR   R2, #1        ; toggle mask
start:
loop:   XOR   R3, R3, R2    ; toggle state (R3 = 0 after reset)
        PIN_W R3            ; out_stage ← R3 (visible on pad next cycle)
        LDR   R4, #255
dly:    DECR  R4
        JNZ   dly           ; counts down to 0 (Z set on 0 → JNZ exits)
        JZ    start          ; (or JNZ start) back to loop
```

## 6. Cycle budgets for protocols @ 12 MHz

| Protocol | Bit time | Cycles/bit | Method |
|---|---|---|---|
| UART 115200 | 8.68 µs | 104 | `LDR #104` + `DECR`/`JNZ` loop; drift 0.17 %/bit (accepted, Q26) |
| UART 9600 | 104.2 µs | 1250 | nested loops (imm8 cap); baud 9600–115200 selectable (Q35) |
| SPI 1.0 Mbit | 1.0 µs | 12 | `SHIFT_OUT`/`SHIFT_IN` + SCK loop; CPOL/CPHA in firmware (Q29) |
| I2C 100 kHz | 10 µs | 120 | `LDR #120` loop; SCL/SDA open-drain via `OE_CLR` release |
| I2C 400 kHz | 2.5 µs | 30 | `LDR #30` loop (firmware option, Q28) |
| JTAG @1 MHz (demo) | 1 µs | 12 | firmware TAP state machine on pins 0–3 (Q74) |
| USB 1.0 low (stretch) | 1.5 Mbit: 667 ns | 8 | 8-cycle bit — DOABLE at 12 MHz, 1 cycle/bit-edge → USB **stretch is viable** |
| PS/2 @10 kbit (stretch) | 100 µs | 1200 | nested `DECR` loops |

(USB 1.0: 1.5 Mbit/s → 1 bit = 667 ns = 8 cycles exactly. Strong signal that 12 MHz
was the right call. **10 Mbit Ethernet: dropped** — 1.2 cycles/bit, infeasible (Q73).
8 MHz fallback if 12 MHz fails timing (Q25).)

## 7. Rules & synthesis constraints
- Single-cycle execute for every op (no multi-cycle ops → simplest control; no multiply, Q11).
- No latches: every `case`/`if` fully specified.
- IMEM is the Tiny Tapeout SRAM macro (Q48): synchronous **registered** read
  (fetch at edge N, valid at N+1). Budget the extra cycle — competitor pitfall #2:
  the IHP 256×8 macro forced a multi-cycle fetch on the IEEE Simple CPU (TTIHP26b #234).
- 12 MHz target, 5% timing margin (Q51); 8 MHz fallback (Q25).

## 7.5 M1.2 — Block diagram + area budget

```
                 +----------------------------------------------------------+
 ui_in(8) -------| IN_RD  |                                                |
 uio_in(8) ------| PIN_R  |                                                |
                 |        |                                                |
                 | +------+-------+   +-----------+   +------------------+ |
                 | |  FETCH      |   |  DECODE    |   |  EXECUTE (ALU)   | |
                 | | PC(9b)      |-->| opcode map |-->| R/I/B/A fmts     | |
                 | | +1, branch  |   |            |   | Z/N flags        | |
                 | | SRAM read   |   +-----------+   | SHL/SHR/flags    | |
                 | | (registered)|        |         +------------------+ |
                 | +-------------+        |                |             |
                 |        ^                 |                |           |
                 |        |                 v                v           |
                 | +------+----------------------+ +------------------+ |
                 | |  STALL LOGIC               | | REGISTER FILE      | |
                 | | WAIT_EDGE (pin transition)| | 8 x 16b, 2R/1W     | |
                 | | WAIT_CMP (cnt == r[rs])   | +------------------+ |
                 | | + 16b HW timeout (sets Z) |                      | |
                 | +-----------------------------------------------+ |
                 |        |                                            |
                 | +--------+--------+   +-----------+   +-----------+ |
                 | | PIN I/O         |   | CYCLE     |   | SHIFT     | |
                 | | out_stage (1c) |   | COUNTER   |   | DATAPATH  | |
                 | | OE_SET/OE_CLR  |   | 16b free- |   | OSR 16b   | |
                 | | -> uio_out/oe  |   | running   |   | MSB-first | |
                 | +----------------+   +-----------+   +-----------+ |
                 +----------------------------------------------------------+
 IMEM: 512x16 SRAM macro (words 0-127 = bootloader ROM, 128-511 = firmware)
```

Per-block cell estimate (IHP 130nm, 12 MHz, ~1.5K cells per 16b FF row of
register file, SRAM macro ~1.5K for 512x16):

| Block | Est. cells | Notes |
|---|---|---|
| Register file (8x16, 2R/1W) | ~350 | 128 FFs + muxes |
| ALU + flags | ~300 | 16b adder, sub, logic ops, shift |
| Decode/control | ~800 | 5b opcode case, format muxes, branch |
| Fetch (PC + SRAM read) | ~250 | 9b PC + registered SRAM read |
| Stall logic | ~500 | edge detect + compare + 16b timeout |
| Pin I/O | ~250 | out_stage, OE set/clear |
| Cycle counter | ~120 | 16b FF |
| Shift datapath | ~200 | 16b OSR + mux |
| IMEM SRAM macro (512x16) | ~1500 | IHP macro, registered read |
| Bootloader ROM (in IMEM) | 0 | lives in SRAM words 0-127 |
| **Total** | **~4.3K** | **< 18K gate met with large margin** |

Even with 2x pessimism the design is ~8.6K — well under the 18K budget.
The SRAM macro dominates; if the macro is unavailable in the flow, an
FF-based 512x16 RAM would add ~8K (still under 18K).

## 8. Open items (feed into M1.3 / decision)
1. ~~imm6 vs imm9~~ → **Decided: imm8** (I-format `b7..b0`); `WAIT_CMP` register-based (Q8).
2. ~~`CALL`/`RET`~~ → **Decided: adopted** — opcodes `11000`/`11001`, R7 = return reg (Q9).
3. Serial shift pin: fixed at bit 7 (stretch pin) or 3-bit pin select in the opcode?
   Default: **fixed bit 7** (keeps the encoding simple); revisit if JTAG/SWD need a second serial pair.
4. Bootloader CRC: **CRC-8 + halt/blink on corruption** (competitor-research rec #3) — confirm checksum width.
5. Optional (nanoPIO idea): per-instruction `delay` field (0–7 stall cycles) would fold
   bit-times into the instruction. **Out of scope for v1.0** — revisit only if firmware
   IMEM pressure shows up.
