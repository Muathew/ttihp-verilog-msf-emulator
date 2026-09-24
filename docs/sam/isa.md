# ISA Spec — `msfcore` (Protocol Emulator Core) (private draft)

> Draft ISA for the PRU-style RISC core, **F1 footprint baseline** (8 regs, 16-bit instr,
> 256-word SRAM, ~12 MHz). Locked only once the architecture decision is confirmed.
> All numbers in cycles refer to one `clk` edge.

## 1. Machine model

- 16-bit little-endian instruction words; word-addressed.
- **Registers:** R0–R7 (16-bit). R0 is not special (unlike ARM) — keep firmware simple.
- **Flags:** `Z` (result == 0), `N` (MSB of result). Affected **only** by
  `ADDS`, `SUBS`, `INCR`, `DECR`. All other instructions leave flags untouched.
- **PC:** 8-bit word pointer (0–255), wraps.
- **Cycle counter:** 16-bit free-running, resets with `rst_n`, increments every cycle.
  Read-only via `CNT_R`. This is the timing primitive for all protocol firmware.
- **Reset:** PC ← 0, R0–R7 ← 0, flags undefined, cycle counter ← 0,
  `uio_out` ← 0, `uio_oe` ← 0 (all pins input mode after reset).
- **Instruction memory:** 256 × 16-bit SRAM, written via master load port (loading is
  done by the testbench/assembler before run; the core only reads).

## 2. Pin interface model (Tiny Tapeout)

| Signal | Width | Role in the emulator |
|---|---|---|
| `ui_in` | 8 | dedicated inputs, readable as a raw bus (R15-style special reg — see §5: read from `r_in`) |
| `uio_in` | 8 | `uio` pins read (when `uio_oe[a]=0`) |
| `uio_out` | 8 | driven value when `uio_oe[a]=1` |
| `uio_oe` | 8 | 0 = input (wire undriven), 1 = output |
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
- **R format:** `b10..b8` = rd, `b9..b7`? — no: `b10..b8` = rs, `b7..b5` = rt, `b4..b0` reserved (0).
  (Order: rd, rs, rt in operand order, left to right.)
- **I format:** `b10..b8` = rd, `b7..b2` = imm6, `b1..b0` reserved (0).
- **B format:** `b10..b2` = rel9 (signed, two's complement; offset from PC+1), `b1..b0` reserved (0).
- **A format:** `b10..b0` = imm11 (absolute).

All reserved bits must be 0.

### Opcode table

| op | mnemonic | format | semantics (exec in 1 cycle) |
|---|---|---|---|
| 00000 | `LDR rd, #imm6` | I | rd ← imm6 (zero-ext). |
| 00001 | `ADDS rd, rs, rt` | R | rd ← rs + rt (16b); sets Z, N. |
| 00010 | `SUBS rd, rs, rt` | R | rd ← rs − rt; sets Z, N. |
| 00011 | `AND rd, rs, rt` | R | rd ← rs & rt. |
| 00100 | `OR rd, rs, rt` | R | rd ← rs \| rt. |
| 00101 | `XOR rd, rs, rt` | R | rd ← rs ^ rt. |
| 00110 | `SHL rd, rs, #imm3` | R* | rd ← rs << imm3 (b4..b2 = imm3). |
| 00111 | `SHR rd, rs, #imm3` | R* | rd ← rs >> imm3 (logical). |
| 01000 | `MOV rd, rs` | R | rd ← rs. |
| 01001 | `JMP #imm11` | A | PC ← imm11 (0–2047; SRAM alias: low 8 bits). |
| 01010 | `JZ rel9` | B | if Z: PC ← PC+1+rel9. |
| 01011 | `JNZ rel9` | B | if !Z. |
| 01100 | `JP rel9` | B | if !N (result non-negative). |
| 01101 | `JN rel9` | B | if N (result negative). |
| 01110 | `NOP` | — | nothing. Timing adjustment. |
| 01111 | reserved | — | — |
| 10000 | `PIN_W rs` | I | out_stage ← r[rs]; `uio_out` commits out_stage on next posedge (see §2). Whole 8-bit word. |
| 10001 | `PIN_R rd` | I | rd ← `{8'h00, uio_in}` (8-bit read, zero-extended). |
| 10010 | `OE_SET imm8` | I | `uio_oe` ← `uio_oe | imm8` (drives listed pins to output). |
| 10011 | `OE_CLR imm8` | I | `uio_oe` ← `uio_oe & ~imm8` (releases listed pins to input, open-drain use). |
| 10100 | `CNT_R rd` | I | rd ← cycle counter. |
| 10101 | `INCR rd` | I | rd ← rd + 1; sets Z, N. |
| 10110 | `DECR rd` | I | rd ← rd − 1; sets Z, N. (countdown loops) |
| 10111 | `IN_RD rd` | I | rd ← `{8'h00, ui_in}` (dedicated input bus). |
| 11000 | `CALL` | — | r7 ← PC+1 (reserved), PC ← PC+1+??? — see §8 item 3 (reserve only). |
| 11xxx | reserved | — | unimplemented ops decode as NOP (single-cycle, no state change). |

\* `SHL`/`SHR` shift amount is `b4..b2` (3 bits; shift ≥ 8 yields 0 — acceptable).
All I-format opcodes use `b10..b8` = register, `b7..b2` = imm (wider where noted),
`b1..b0` = 0.

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
| 7 | reserved | stretch (e.g. USB or RNG) |

Open-drain (I2C): firmware sets SDA/SCL via `PIN_W` then **releases** them with
`OE_CLR` so pull-ups win; drives Low by `OE_SET` + 0. Two-wire half-duplex fits
naturally with one 8-bit datapath. (Final map confirmed in M1.3.)

## 5. Worked example — blink bit 0, half-period 50 µs @ 12 MHz (600 cycles)

> 50 µs = 600 cycles; imm6 caps `LDR` at 63, so the delay is a `DECR`/`JNZ` loop:
> 600 = 63 × 9 + 33 → 9 full iterations of 63 + one of 33, or simply 2×63+474 with a
> nested loop. Firmware convention (M1.3): a `delay(count)` macro expands to the optimal
> nested loop. Sketch (single-level, count ≤ 63):

```asm
        LDR   R1, #63       ; loop count
        LDR   R2, #1        ; toggle mask
start:
loop:   XOR   R3, R3, R2    ; toggle state (R3 = 0 after reset)
        PIN_W R3            ; out_stage ← R3 (visible on pad next cycle)
        LDR   R4, #63
dly:    DECR  R4
        JNZ   dly           ; counts down to 0 (Z set on 0 → JNZ exits)
        JZ    start          ; (or JNZ start) back to loop
```

## 6. Cycle budgets for protocols @ 12 MHz

| Protocol | Bit time | Cycles/bit | Method |
|---|---|---|---|
| UART 115200 | 8.68 µs | 104 | `LDR`#63 × loop `DECR`/`JNZ` + residue; drift 0.17 %/bit |
| SPI 1.0 Mbit | 1.0 µs | 12 | tight loop, SCK on pad-pair |
| I2C 100 kHz | 10 µs | 120 | SCL/SDA open-drain via `OE_CLR` release |
| USB 1.0 low (stretch) | 1.5 Mbit: 667 ns | 8 | 8-cycle bit — DOABLE at 12 MHz, 1 cycle/bit-edge → USB **stretch is viable** |

(USB 1.0: 1.5 Mbit/s → 1 bit = 667 ns = 8 cycles exactly. Strong signal that 12 MHz
was the right call.)

## 7. Rules & synthesis constraints
- Single-cycle execute for every op (no multi-cycle ops in F1 → simplest control).
- No latches: every `case`/`if` fully specified.
- SRAM is a synchronous 1-cycle read (fetch pipeline: fetch at edge N, use at N).
  → 1-cycle penalty vs combinational mem; accounted in budgets above.

## 8. Open items (feed into M1.3 / decision)
1. imm6 (LDR) vs imm9 (LDR) — currently imm6, loops for long delays.
2. Pin-pair convention (§4) needs the final GPIO mapping.
3. Should `CALL`/`RET` land in reserved opcodes now (cost: 1 free reg as stack)?
   Cheap to add: `CALL` pushes PC into **R7 as dedicated call stack (1 level)** →
   suggest reserving op `11000`/`11001` for `CALL`/`RET` immediately (no RTL cost
   until M2).
