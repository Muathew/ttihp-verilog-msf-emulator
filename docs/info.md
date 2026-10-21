<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

**msfcore** is a reprogrammable protocol emulator: a small 16-bit CPU whose
instruction set is built for reading pins, writing pins, counting cycles and
hitting timing exactly, so that protocols run as **firmware** loaded at run
time instead of fixed logic. The chip has no UART, SPI or I2C block; it has
generic building blocks that firmware composes into protocols.

### Architecture

| Block | What it does |
|---|---|
| Core | 16-bit RISC, 8 registers per thread, 5-bit opcodes + a 32-entry extension group, single-cycle execute, deterministic cycle costs |
| 2 hardware threads | barrel-interleaved (XMOS-style): when T1 runs, cycles alternate T0/T1; each thread has its own PC, registers, flags, timeout and stall state, and its timing does not depend on the other thread |
| FIFO | a queue between the threads, kept in data RAM with its positions in hardware: one instruction to push (`FIFO_PUT`, 1 cycle) or pop (`FIFO_GET`, 2 cycles), words dropped by a full FIFO counted |
| Blocking ops | `WAIT_EDGE` (any/rising/falling edge or level, on `uio` or `ui` pins), `WAIT_CMP` (cycle counter == register), `LD`, `TX_PUSH`, `TX_WAIT`, `RX_POP`, `CRC_W`, `CRC_RDL` stall the thread with a programmable timeout (Z=1 on timeout) |
| Memory | 1024 x 16 SRAM in two IHP two-port macros (512 words each) + 128-word boot ROM. Fetch: 0-127 ROM, 128-1023 SRAM. Data (`LD`/`ST`/`MEM_W`/FIFO): all 1024 SRAM words, so 0-127 is free data RAM |
| Shift register | 16-bit SR, load/read, `SHIFT_OUT`/`SHIFT_IN`/`XFER` (full duplex) on configurable pins, MSB- or LSB-first |
| SERDES TX | NRZ / NRZI / Manchester, bit stuffing, differential pin pair, 1-16 bit words, programmable symbol period, double buffered (gapless), release mode for exact idle/EOP timing |
| SERDES RX | NRZ / NRZI, de-stuffing, edge resync, continuous or async-character (UART-style) start, overrun flag |
| CRC unit | any CRC up to 32 bits, normal or reflected, 1-16 bits per write, bit-serial in the background (CRC-8, CRC-5/16 USB, CRC-32 Ethernet ...) |
| Pins | `uio[7:0]` with per-bit set/clear/toggle/write of OUT and OE (open drain = OE), `uo_out[7:0]` written by firmware, `ui_in[7:0]` readable; all inputs pass a 2-flip-flop synchroniser |

### Timing model (what makes bit-banging exact)

* Single-thread mode: every instruction takes 1 cycle; a taken branch, `JMP`,
  `CALL`, `RET`, `JR` take 2; `LD` takes 2. Dual-thread mode: every
  instruction takes one slot of its thread (2 cycles), branches included.
* A pin write executed in cycle *t* is on the pad from *t+1*; a pad change in
  cycle *t* is visible to instructions from *t+2*.
* `WAIT_CMP R` releases exactly when the 16-bit cycle counter equals `R`, so a
  bit schedule `target += period; WAIT_CMP target; PIN_W bit` has zero
  accumulated drift.

The full ISA is in `docs/isa.md` (repository).

### Memory map and boot

| Address | Fetch | Data |
|---|---|---|
| 0-127 | boot ROM | data RAM |
| 128-1023 | SRAM (firmware; bank 1 from 512) | SRAM |

At reset `ui_in[0]` selects: **1** = run firmware already in SRAM at 128;
**0** = boot loader. `ui_in[1]` selects the loader's byte source: **0** = UART
on `uio[1]` (115200 8N1 at 12 MHz), **1** = parallel bytes on `ui_in[7:0]`
with a two-phase strobe on `uio[1]` (byte *k* valid when strobe = *k* mod 2;
acknowledged on `uo_out[7]`). Frames:

```
A5  cmd  addrL addrH  lenL lenH  (wordL wordH)*len  crc8      crc8 = CRC-8/0x07 over cmd..payload
cmd 'W' (0x57): write len words at addr     cmd 'G' (0x47): jump to addr (len 0)
```

Each frame is answered with ACK `0x06` / NAK `0x15` on `uio[0]` (UART) and on
`uo_out[3:0]` (bit0 waiting, bit1 OK, bit2 CRC error, bit3 starting firmware).
`tools/msf_boot.py firmware.s --port COMx` assembles and loads a program.

### Debug view

Hold `ui_in[2]` high during reset and `uo_out` shows, instead of the
firmware's outputs, the byte of internal state selected by `ui_in[7:2]`:
either thread's registers, the PC and word of the last instruction issued,
flags, stall state, `CNT`, `SR`, the pin registers and the FIFO (full table
in `docs/isa.md` §8). The select works without a clock edge, so with the
demo board's RP2040 clocking the chip one cycle at a time you can halt,
single-step, stop at a PC and read every register.

## How to test

1. Assemble a demo from `firmware/` (or your own program) with
   `test/assembler.py`, and load it with `tools/msf_boot.py` over a 3.3 V
   USB-UART on `uio[1]` (RX) / `uio[0]` (TX), with `ui_in[1:0] = 00` at reset.
   With the demo board's RP2040 you can use the parallel loader instead
   (`ui_in[1:0] = 10`, strobe on `uio[1]`).
2. The demos (all checked in simulation at the pins by `test/test_firmware.py`):

| Firmware | Clock | Pins | What to observe |
|---|---|---|---|
| `uart_tx.s` | 12 MHz | uio0 TX | 115200 8N1 string, drift-free |
| `spi_master.s` | 12 MHz | uio2 SCK, uio3 MOSI, uio4 MISO, uio5 CS | 1 Mbit/s, modes 0-3, full duplex |
| `i2c_master.s` | 12 MHz | uio5 SDA, uio6 SCL (pull-ups) | 100 kHz write/read, ACK/NACK, clock stretching |
| `usb_ls_tx.s` / `usb_ls_rx.s` | 12 MHz | uio0 D-, uio1 D+ | low-speed packet with CRC16 and exact EOP; receive + CRC check |
| `eth_10baset_tx.s` | 20 MHz | uio2 TD+, uio3 TD- | 10BASE-T frame (preamble, SFD, CRC-32 FCS, TP_IDL) and link pulses |
| `thread_demo.s` | 12 MHz | uio7, uio0/1 | exact square wave on T0 while T1 echoes UART |
| `ws2812.s` | 12 MHz | uio0 | WS2812 LED chain at 800 kHz from the TX engine |

3. Instruments (details and figures in `docs/instruments.md`): the chip as
   a device, as a bus monitor reporting events at 1 Mbaud on `uio0`, as a
   serial-to-SPI adapter, and as a controller that breaks timing on purpose.

| Firmware | Clock | Pins | What to observe |
|---|---|---|---|
| `spi_target.s` | 12 MHz | uio2 SCK, uio3 MOSI, uio4 MISO, uio5 CS | a 64-register SPI device, modes 0-3, up to 750 kHz, no dummy byte |
| `i2c_target.s` | 12 MHz | uio5 SDA, uio6 SCL (pull-ups) | a 128-byte EEPROM at address 0x50; 400 kHz with clock stretching |
| `mon_uart.s` | 12 MHz | uio1 line, uio0 reports | bytes, framing errors, baud error, glitches |
| `mon_i2c.s` | 12 MHz | uio5 SDA, uio6 SCL, uio0 reports | START/STOP, bytes and ACKs, SCL phases shorter than standard mode with their lengths |
| `mon_spi.s` | 12 MHz | uio2 SCK, uio3 MOSI, uio7 MISO, uio5 CS, uio0 reports | MOSI and MISO bytes, mode mismatch, partial bytes |
| `bridge.s` | 12 MHz | uio1 RX, uio0 TX; SPI as `spi_master.s` | length-prefixed SPI transfers driven over the UART, bytes read back returned |

   Timing knobs (assembler defines) let `uart_tx.s`, `i2c_master.s` and
   `bridge.s` move edges by exact amounts: bit time and stop bit, SCL low
   and high, SCK high and low.

4. In simulation: `cd test && make` (or `python run_tests.py --all` on
   Windows) runs 140 cocotb tests including a random differential check of
   the RTL against an independent ISA model; `python formal/run_formal.py`
   runs the formal properties; `python tools/mutation.py` measures how many
   seeded RTL faults the tests catch (38 of 40; the two left cannot change behaviour).

## External hardware

* USB-UART adapter (3.3 V) for loading firmware, or the TT demo board's RP2040.
* Pull-up resistors (e.g. 4.7 kOhm) for I2C on `uio[5]`/`uio[6]`.
* USB low speed: 1.5 kOhm pull-up on D- (device side) and series resistors.
* 10BASE-T: TD+/TD- through a resistor network into an Ethernet magnetics
  module (transmit only).
