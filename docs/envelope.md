# The envelope: how fast each class of protocol goes, and what sets the limit

Limits are in clock cycles, so they do not depend on the clock; the last column converts to a rate at
50 MHz, the clock v4 is signed off at in every corner (20 ns, 19 Nov: [`plan-v4.md`](plan-v4.md)
item 7). Every number is from a test in this repository, named in the
last column, or says "not measured". This table is made by hand from those tests: the sweep script the
plan wanted to generate it in CI is not built.

| Class | Example | Limit | What sets it | At 50 MHz | Where measured |
|---|---|---|---|---|---|
| Asynchronous serial, on the engine | UART receive | divisors 4 to 40 receive, at ±2 % baud error and ±1.5 cycles of jitter | the 16-bit divider, the input synchroniser | 12.5 Mbaud at divisor 4 (bit centres 2 cycles apart) | `test_peripherals` (RX at every divisor 4-40) |
| Synchronous, we are the clock | SPI master in firmware | 12 cycles a bit hand-written (`spi_master.s`); 32 cycles a clock from msfc, with hardware-timed edges | instructions between edges; for msfc, the compiler's checker refuses a shorter half period | 4 Mbit/s, 1.5 Mbit/s | `test_firmware`; `test_msfc` (HALF = 16 certified, 4 refused) |
| Synchronous, they are the clock | SPI target in firmware | 16 cycles a clock, every mode, no dummy byte | firmware work per bit | 3 Mbit/s | `test_targets` (a 16-cycle SCK works, 14 fails) |
| | SPI target, engine on the master's clock (v4) | receive from **2** cycles a bit, transmit from **6** (8 with a cycle of margin) | the synchroniser and the pad register: the bit is on the pad 3 cycles after the edge | 25 Mbit/s receive, 8 Mbit/s transmit (with margin: 6 Mbit/s) | `test_engine_options` (`test_external_clock_speed_limits`, all four modes) |
| Self-clocked | 10BASE-T receive (mode 3) | 5 cycles a bit with 802.3's 13.5 ns of edge jitter in fine mode (`RX_OPT` bit 6), 15 ns at worst; without it 10 ns spread at random, 5 ns at worst; at least 4 cycles a bit in the arithmetic | an edge is placed to half a cycle (fine mode samples on both clock edges) or to a cycle; the centre window is a quarter-bit either side | 10 Mbit/s | `test_eth10_rx`, `test/mode3_model.py` |
| NRZI with stuffing | USB full speed, both ways | 4 cycles a bit (at 48 MHz) | the engine's divider and de-stuffing | 12 Mbit/s at 48 MHz | `test_usb_fs` |
| | HDLC and CAN stuffing on the engine (v4) | tested at 7 cycles a bit; the engine's own limit not measured | the divider | not measured | `test_engine_options` (reference-checked, several *n*) |
| Pulse width and position | any edge that must be on its cycle | **one cycle, zero jitter with four threads running**, as long as the write arrives before its time | the slot holds one event per thread | 20 ns | `test_timed_pins` (1 to 4 threads, one stalled, one loading) |
| Reactive device | I2C target (EEPROM) | 100 kHz; 400 kHz with clock stretching | the reaction path in firmware (response latencies of 4 to 13 cycles, derived from the ISA costs) | | `test_targets` |
| | reply to an edge (1-Wire, SENT, IR) | exactly *delta* cycles after the edge, for any phase and 1 to 4 threads, with `TS` and `AT` | none: the hardware keeps it | | `test_timed_pins` (`test_reply_idiom_has_no_jitter`) |
| Parallel | HD44780, 8080 | not measured as a class | pin count | | `test_hd44780` shows the protocol, not the limit |
| Two at once | two UARTs, a bridge, a monitor on both directions | two protocols on two engine sets, and neither's timing depends on the other | one TX, RX and CRC set a thread | | `test_engine_sets` |
| Long gaps | DCF77, servos | a `WAIT_CMP` at most 65,280 cycles ahead (1.3 ms at 50 MHz); longer with loops, or with the 32-bit counter's high half (`CNT_H`) read by firmware | the 16-bit `CNT` | 1.3 ms a wait | `test_wait_cmp_late_boundaries`; `test_chip_identity_and_the_32_bit_counter` |

## What it cannot do

* **Anything faster than the clock and the pads allow.** The engines move at most one bit a cycle
  (50 Mbit/s at 50 MHz, logically) and the tests reach 25 Mbit/s receiving on an external clock and
  12 Mbit/s for USB, in zero-delay simulation. Whether the pads carry that is not known: the FAQ's
  estimate is about 66 MHz at an output, 10 ns round trip through pad and mux, and there is no
  silicon to measure. There is no PLL and no serialiser faster than the clock.
* **Anything needing analog levels:** no analog pads. USB's differential levels and 10BASE-T's
  signalling need external parts (a transceiver, magnetics); the chip drives the logic.
* **More than eight bidirectional pins.** 24 digital pins in all: eight bidirectional (`uio`), eight
  inputs (`ui`) and eight outputs (`uo`); v4's engines can use all of them (`RX_CFG` bit 3,
  `TX_CFG` bit 15), the instructions that read and write whole ports still address `uio`.
* **More than four threads, more than two engine sets.** A third protocol with its own engine shares
  one of the two sets.
* **Programs larger than 2,048 words, data included.** (No RISC-V core: plan §4.)

## What sets the limits, in one list

1. **The cycle.** One instruction a cycle for one thread; with *n* threads each gets every *n*-th.
   This is why the timed pins exist: they take the edge out of the instruction stream.
2. **The synchroniser.** A pad change is seen two cycles later and a pin write is on the pad one cycle
   later: three cycles from an input edge to the earliest answer on a pin the engine drives.
3. **The checker's reach.** A deadline the checker cannot follow is refused (`msfc`) or marked
   unchecked (`msf_timing.py`); it never passes on trust.
