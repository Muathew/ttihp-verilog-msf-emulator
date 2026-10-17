> **Status (2026-10-17, ISA v2):** this is the original v1 plan, kept for
> history. The current verification stack — independent ISA model, random
> differential testing, formal properties with mutation checks, and the
> firmware demos with real USB/Ethernet framing — is described in
> [golden-model.md](golden-model.md). The "Formal & Architectural Invariant"
> tests named below are directed simulations (now `test_crv_invariants.py`);
> real formal proofs are in `formal/run_formal.py`.

# Protocol Emulator ASIC — Comprehensive Verification & Extensions Plan

**Target Design**: Tiny Tapeout Protocol Emulator ASIC (`tt_um_msf`)  
**Technology**: IHP SG13G2 (130 nm) with 512×16 2-Port SRAM Macro (`RM_IHPSG13_2P_512x16_c2_bm_bist`)  
**Clock Frequency**: 12 MHz (83.33 ns nominal, ~80 ns in simulation)  
**Footprint**: 6×4 tiles (~24K cells allocated)  

---

## 1. Executive Summary & Context

The chip was hardened using OpenLane and the IHP SG13G2 PDK. Due to the integration of the dedicated 512×16 dual-port SRAM macro (replacing ~6,000 flip-flops previously needed for register-based IMEM) and a compact RISC+shift-datapath core (~4.3K cells), the design fits with substantial floorplan headroom ("ended up with a lot of space").

The challenge requires proving:
1. **True Flexibility & Reprogrammability**: The ASIC must not be a fixed set of hardwired peripheral blocks, but a general-purpose protocol emulator capable of being reprogrammed after fabrication in firmware.
2. **Core Protocols**: UART (TX & RX), SPI (Master & Slave, multiple modes), I2C (Open-drain with ACK/NACK & clock stretching).
3. **Stretch Goals & Extensions**:
   - Low-Speed USB 1.0 (1.5 Mbps NRZI + bit-stuffing, exactly 8 cycles/bit @ 12 MHz).
   - 10Mbit Ethernet (Manchester encoding analysis, cycle budgeting, feasibility/overclocking).
   - JTAG (IEEE 1149.1 4-wire TAP state machine + IDCODE).
   - SWD (ARM 2-wire Serial Wire Debug packet turnaround & ACK).
   - PS/2 (Bidirectional open-drain 11-bit framing + parity).
   - CAN Bus (Dominant/recessive arbitration & 2.0A standard framing).
4. **Jane Street Verification Techniques**:
   - Multiple in-system reprogrammability tests.
   - Constrained-Random Verification (CRV) with randomized payloads, baud rate jitter tolerance (±1%, ±2%, ±3%), and clock stretch variance.
   - Formal & Architectural Invariant Assertions (Monotonic cycle counter, timeout hardware safety guarantee preventing any deadlock, strictly 1-cycle output latency, flag preservation across non-arithmetic ops).
   - Golden reference models in Python/Cocotb.
   - Unified regression harness.

---

## 2. Master Verification Architecture

```
                                  +-------------------------------------------------------+
                                  |                  COCOTB TEST HARNESS                  |
                                  +-------------------------------------------------------+
                                    |              |                |                   |
               +--------------------+              |                |                   +---------------------+
               v                                   v                v                                         v
   +-----------------------+           +------------------+  +-------------------+              +----------------------+
   |  test_reprogrammable  |           |  test_protocols  |  |  test_extensions  |              |   test_crv_formal    |
   +-----------------------+           +------------------+  +-------------------+              +----------------------+
   | * UART Bootloader     |           | * UART TX 115200 |  | * Low-Speed USB   |              | * CRV Random Payload |
   |   Stream -> MEM_W     |           | * UART RX 115200 |  |   (1.5M NRZI/stuff|              | * Baud Jitter (±3%)  |
   |   to SRAM 128..511    |           | * UART Loopback  |  | * JTAG TAP State  |              | * Monotonic Counter  |
   | * Dynamic Multi-Proto |           | * SPI Mode 0 & 3 |  |   Machine (IDCODE)|              | * Timeout Safety SVA |
   |   Sequential Swap:    |           | * I2C Open-Drain |  | * ARM SWD Debug   |              | * 1-Cycle Latency    |
   |   UART -> SPI -> I2C  |           |   ACK/NACK       |  | * PS/2 Scancode   |              | * Flag Preservation  |
   |   -> JTAG (No Reset)  |           | * I2C Clock-Str. |  | * CAN Bus Frame   |              +----------------------+
   +-----------------------+           +------------------+  | * 10Mbit Ethernet |
                                                             +-------------------+
```

---

## 3. Detailed Phase Breakdown & Verification Tasks

### Phase 1: In-System Reprogrammability Verification (`test_reprogrammable.py`)
- [ ] **1.1 UART Bootloader In-System Reprogramming Test**:
  - Start core from reset with `ui_in[0] = 0` (executes bootloader in ROM, words 0..127).
  - Testbench sends binary machine code over UART RX (`uio_in[1]`).
  - Bootloader streams received words into SRAM (128..511) using the `MEM_W` auto-incrementing hardware write port.
  - Bootloader branches to `JMP 128`; loaded firmware executes and drives verified patterns.
- [ ] **1.2 Dynamic In-Situ Multi-Protocol Swapping (Without ASIC Reset)**:
  - Demonstrate post-fabrication field reprogrammability by sequencing multiple independent protocol firmwares onto the running chip:
    1. Stream **Program 1 (UART TX)** $\rightarrow$ Verify UART byte on bus.
    2. Overwrite SRAM with **Program 2 (SPI Master)** $\rightarrow$ Verify SPI clock & data.
    3. Overwrite SRAM with **Program 3 (I2C Master)** $\rightarrow$ Verify open-drain START/STOP/data.
    4. Overwrite SRAM with **Program 4 (JTAG TAP)** $\rightarrow$ Verify TAP state transitions.
  - All performed on a single continuous simulation session without changing RTL or hardware logic.

---

### Phase 2: Core Protocols Verification Suite (`test_protocols.py`)
- [ ] **2.1 UART TX (115200 baud, 8N1)**:
  - Firmware bit-bangs 104 cycles/bit transmission on `uio[0]`.
  - Python Cocotb UART receiver samples bit centers and validates framing (Start, 8 bits LSB-first, Stop).
- [ ] **2.2 UART RX (115200 baud, 8N1)**:
  - Firmware detects start bit falling edge using `WAIT_EDGE 1`.
  - Half-bit delay to mid-start-bit, verifies line stability.
  - 8-bit mid-bit sampling loops; reconstructs byte and presents to `uio_out`.
  - Tested with Cocotb UART transmitter driving arbitrary test bytes.
- [ ] **2.3 UART Echo / Loopback**:
  - Full-duplex test: receives byte on RX (pin 1), modifies/inverts or echoes to TX (pin 0).
- [ ] **2.4 SPI Master Mode 0 (CPOL=0, CPHA=0)**:
  - SCK (pin 2), MOSI (pin 3), MISO (pin 4).
  - Full-duplex byte exchange against Cocotb SPI slave responder.
- [ ] **2.5 SPI Master Mode 3 (CPOL=1, CPHA=1)**:
  - Shows that firmware alone reconfigures clock polarity and phase without hardware changes.
- [ ] **2.6 I2C Master Open-Drain Bus Interaction**:
  - SDA (pin 5), SCL (pin 6) using `OE_SET` (drive 0) and `OE_CLR` (release high).
  - Generates START condition, 7-bit slave address + W/R bit, detects slave ACK, sends data byte, generates STOP condition.
- [ ] **2.7 I2C Slave Clock Stretching**:
  - Slave pulls SCL low to hold transaction; master samples SCL and waits until release before clocking data.

---

### Phase 3: Extension Protocols & Stretch Goals (`test_extensions.py`)
- [ ] **3.1 Low-Speed USB 1.0 (Stretch Goal 1)**:
  - At 12 MHz, 1.5 Mbps is **exactly 8 clock cycles per bit**.
  - NRZI encoding (toggle on '0', maintain state on '1').
  - Bit stuffing (insert '0' after six consecutive '1's).
  - Packet framing: Sync byte (`0x80`), PID byte (`0xC3` = DATA0), payload, CRC, and Single-Ended Zero (SE0) EOP.
  - Cocotb USB packet monitor decodes NRZI and bit-stuffing, asserting payload validity.
- [ ] **3.2 JTAG TAP Controller (IEEE 1149.1)**:
  - 4-wire interface: TCK, TMS, TDI, TDO.
  - Firmware executes TAP state transitions: `Test-Logic-Reset -> Run-Test/Idle -> Select-DR-Scan -> Capture-DR -> Shift-DR -> Update-DR`.
  - Shifts 32-bit IDCODE (`0x149501C3`) out on TDO.
  - Cocotb JTAG bus monitor tracks TAP state sequence and extracts IDCODE.
- [ ] **3.3 ARM Serial Wire Debug (SWD)**:
  - 2-wire interface: SWCLK and bidirectional SWDIO.
  - Packet request with parity bit, Turnaround (`Trn`), 3-bit ACK (`0b001` = OK), and 32-bit data phase with parity.
  - Cocotb SWD target models device response.
- [ ] **3.4 PS/2 Protocol**:
  - Bidirectional open-drain CLK and DATA.
  - 11-bit frame: 1 start bit (0), 8 data bits (LSB-first), 1 odd parity bit, 1 stop bit (1).
  - Cocotb PS/2 receiver validates scancode parity and framing.
- [ ] **3.5 CAN Bus (Controller Area Network)**:
  - Dominant (0) vs. Recessive (1) framing.
  - Transmits CAN 2.0A standard frame: SOF (0), 11-bit Identifier, RTR (0), IDE (0), r0 (0), DLC (4 bits), Data, CRC, ACK slot.
  - Cocotb CAN bus monitor validates arbitration and frame CRC.
- [ ] **3.6 10Mbit Ethernet Feasibility & Transition Generator (Stretch Goal 2)**:
  - Manchester encoding analysis: at 12 MHz, 10 Mbps Manchester transition interval is 50 ns (0.6 cycles), which is sub-cycle for a 12 MHz core.
  - Firmware generates Ethernet preamble pattern (`10101010...`) and SFD (`10101011`).
  - Detailed feasibility documentation showing how overclocking to 24–40 MHz or using a dedicated 1-bit shift serializer achieves 10BASE-T compliance.

---

### Phase 4: Jane Street Verification Suite (`test_crv_formal.py`)
- [ ] **4.1 Constrained-Random Verification (CRV)**:
  - Randomized payload generation across protocols.
  - Baud rate mismatch stress testing (injecting ±1%, ±2%, and ±3% clock frequency drift to verify bit-bang margin tolerance).
  - Randomized I2C clock stretch intervals and slave ACK/NACK responses.
  - 50+ iteration randomized stress suite with deterministic reproducible seed logging.
- [ ] **4.2 Formal & Architectural Invariant Checks**:
  - **Invariant 1: Monotonic Cycle Counter**: Property proving `cnt` increments by 1 every cycle unconditionally.
  - **Invariant 2: Timeout Hardware Safety**: Property proving `WAIT_EDGE` and `WAIT_CMP` are guaranteed to un-stall and set flag $Z=1$ within $2^{16}$ cycles if no external event arrives, ensuring the chip can never hang in an unrecoverable state.
  - **Invariant 3: Deterministic Output Latency**: Property proving `PIN_W` instruction commits to `uio_out` on exactly the subsequent cycle (strictly 1-cycle latency).
  - **Invariant 4: Flag Preservation**: Property proving arithmetic flags $Z$ and $N$ are never clobbered by non-arithmetic instructions (`PIN_W`, `PIN_R`, `MOV`, `LDR`, `AND`, `OR`, `XOR`, `CALL`, `RET`, `JMP`).
- [ ] **4.3 Unified Master Regression Runner**:
  - Running all test modules (`test_core`, `test_protocols`, `test_extensions`, `test_reprogrammable`, `test_crv_formal`) via `python run_tests.py --all`.
  - Comprehensive XML test report generation and passing score.

---

## 4. Current Status Matrix

**All 33 tests pass** via `python run_tests.py --all` (Icarus Verilog + cocotb 2.x).

| Module | Scope | Status | Notes |
|---|---|:---:|---|
| `test_core` | 9 Base ISA Unit Tests |  PASS (9/9) | SRAM macro dual-target fix resolved |
| `test_protocols` | UART TX/RX/Echo, SPI, I2C |  PASS (7/7) | UART echo loopback (§2.3) added; TX idle-high + SHR LSB-first fix |
| `test_reprogrammable`| UART Bootloader + Dynamic Swap |  PASS (4/4) | Over-the-wire UART bootloader (§1.1) added; `bootloader.s` 68 words |
| `test_extensions` | USB, JTAG, SWD, PS/2, CAN, Eth |  PASS (6/6) | Full protocol decode (NRZI, TAP, SWD, PS/2, CAN 2.0A) |
| `test_crv_formal` | CRV, Jitter, Stretch, Invariants |  PASS (7/7) | 50 vectors, ±1/2/3% jitter, randomized I2C stretch |

**Total: 33/33 passing.**
