# Golden Reference Model — msfcore Protocol Emulator

**Date**: 12 Oct 2026  
**Files**: `test/golden_model.py`, `test/test_golden_model.py`  
**Status**: Implemented, all 36 tests green (including 3 new golden-model tests)

---

## 1. What It Is

An independent, pure-Python cycle-accurate reference model of the msfcore protocol
emulator core.  Used in cocotb to **differentially verify** the RTL: the model and
the DUT are stepped in lock-step, and observable state (PC, register file, Z/N
flags, pad outputs) is diffed every cycle.  Any divergence between the hardware
and the ISA/golden model is caught on the first divergent cycle.

The model also provides **protocol reference waveforms** (UART, SPI, I2C, USB 1.0
low) that serve as the golden side of each protocol-level check.

---

## 2. Model Components (`test/golden_model.py`)

### 2.1 `MsfCoreModel` — Cycle-Accurate Core Model

Mirrors `src/tt_um_msf_ctrl.v` + `src/tt_um_msf_pio.v` + the registered SRAM/ROM
read path:

| Feature | Detail |
|---|---|
| Fetch pipeline | 2-stage (registered SRAM read → flush bubble). PC + flush updated per RTL semantics. |
| ALU + flags | `ADDS`/`SUBS`/`INCR`/`DECR` set Z/N; all other ops leave flags untouched. |
| Cycle counter | 16-bit free-running (`CNT_R`), resets with `rst_n`. |
| Shift datapath | 16-bit OSR, MSB-first. `SHIFT_OUT`/`SHIFT_IN`/`PIN_W` bit-7 driver. |
| Output stage | `out_stage` latches 1 cycle after `PIN_W` (pad register). `uio_out = {ser_out, out_stage[6:0]}`. |
| Open-drain OE | `OE_SET`/`OE_CLR` OR/AND the 8-bit OE register. |
| Stall FSM | `WAIT_EDGE` (pin transition) / `WAIT_CMP` (counter match) + 16-bit hardware timeout → resume + set Z. |
| MEM_W | Auto-incrementing SRAM write port (128→511, wraps). |
| Boot | PC 0 = ROM bootloader (jumps to 128 on `ui_in[0]=1`). |

**API:**

```python
model = MsfCoreModel()
model.load_sram(fw_words)       # IMEM words 128..
model.load_rom(rom_words)       # IMEM words 0..127
model.reset()
model.step(uio_in=0, ui_in=1)  # one clock
snap = model.snapshot()        # dict: pc, regs, z, n, cnt, uio_out, uio_oe, ...
```

### 2.2 Protocol Reference Models

| Function | Purpose |
|---|---|
| `uart_frame_bits(byte)` | Expected TX pin levels: start(0), 8 data bits LSB-first, stop(1). |
| `UartRxDecoder` | Decode sampled RX bits → byte (with error counting). |
| `spi_master_waveform(byte, cpol, cpha)` | Expected SCK/MOSI edge placement per SPI mode. |
| `I2cConditionDetector` | Classic I2C bus-state detector (START / STOP / data-bit on SCL rising edge). |
| `nrzi_encode(bits, initial_state)` | USB NRZI (logical 1 = no transition, 0 = toggle). |
| `usb_bit_stuff(bits)` | Insert a stuffing 0 after every run of 5 consecutive 1s. |
| `usb_low_packet(sync, pid, payload)` | Full USB 1.0 low-speed NRZI + bit-stuffed field. |

---

## 3. Cocotb Test Suite (`test/test_golden_model.py`)

| Test | What It Checks |
|---|---|
| `test_golden_lockstep` | Differential: 60-cycle lock-step DUT vs `MsfCoreModel`. Compares PC, R0–R7, Z, N, `uio_out`, `uio_oe` every cycle. |
| `test_golden_uart_reference` | UART TX frame for 0xA5 matches expected bit sequence; RX decode round-trips 0x3D. |
| `test_golden_i2c_conditions` | I2C detector classifies START, data-bit-0, and STOP from a clean open-drain sequence. |

### Lock-step phasing (cocotb)

```
await RisingEdge(dut.clk)      # DUT takes the edge; enter write phase
dut.uio_in.value = ...         # set inputs for next cycle
model.step(...)                 # model: S → S'
await ReadOnly()               # settle non-blocking assignments
# read DUT state, diff vs model.snapshot()
```

The free-running `cnt` is **not** compared in the strict assert (its absolute
phase is a reset-deassertion testbench artifact, not a design property).

---

## 4. Integration

- Listed in `test/Makefile` → `COCOTB_TEST_MODULES` (runs with `make` / `make -B`).
- No new simulator dependencies; pure Python.
- Self-test: `python3 test/golden_model.py` (exits 0 on success).

---

## 5. Results

```
TESTS=36 PASS=36 FAIL=0 SKIP=0
  test_golden_model.test_golden_lockstep         PASS
  test_golden_model.test_golden_uart_reference   PASS
  test_golden_model.test_golden_i2c_conditions   PASS
```

Full suite (test_core, test_crv_formal, test_extensions, test_protocols,
test_reprogrammable, test_golden_model) all green.

---

## 6. Design Notes

- **Why pure Python?**  The model must be *independent* of the simulator so it
  can be used for formal cross-checks, firmware co-sim, and CI without Icarus.
- **Why `ReadOnly` in the loop?**  `RisingEdge` returns in the write phase
  (NBA not yet settled).  `ReadOnly()` waits for all deltas to settle before
  we read DUT register values.  Without it, `pc_p` reads one cycle stale.
- **Why exclude `cnt`?**  The DUT's async reset holds `cnt=0` for 5 cycles
  (the low-`rst_n` window).  The model's `cnt` starts incrementing from the
  first `step()`.  This one-cycle offset is a testbench artifact; the *rate*
  (1 count/clk) is what matters and is implicitly verified by the lock-step
  staying aligned for 60 cycles.
