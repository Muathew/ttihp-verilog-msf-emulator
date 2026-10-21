# Instruments: device emulation, bus monitors, fault injection

The brief asks for a tool to debug and reverse-engineer digital protocols.
This page covers the firmware that makes msfcore one: **targets** (the chip
plays a device), **monitors** (it listens to a bus and flags timing
faults), a **bridge** (a host drives SPI through the UART, one thread per
side) and **timing-fault knobs** (the controllers break a device's timing
by exact amounts). None of it needs protocol hardware: it is firmware on
the core's level waits, cycle counter, shift register and SERDES, loaded at
run time. Every claim below is checked at the pins in simulation by the
test named next to it; the timing figures are core clock cycles (83 ns at
12 MHz).

## How timing works out

All the response times follow from the ISA's cost rules (`isa.md` §5): a
pad change is seen 2 cycles later (synchroniser), a level wait releases in
the cycle it sees it, and an output written in cycle *t* is on the pad from
*t* + 1. So a pin or OE write made by the *n*-th cycle after a wait
releases reaches the pad **3 + *n*** cycles after the edge that released
the wait; a shift-register output (`XFER`, `SHIFT_OUT`) **4 + *n***. In
dual-thread mode an instruction is 2 cycles.

## 1. Targets

### SPI target — `firmware/spi_target.s` (`test_targets.py`)

A 64-register device, SPI modes 0-3: a command byte (bit 7 read, bits 5..0
address), then any number of data bytes, the address incrementing after
each. A read returns the register **in the byte right after the command**:
there is no dummy byte, although the address's last bit arrives on the
command's last clock. The firmware loads both registers that address can
still be before that clock and puts the even one's first bit out with it;
if the address is odd, it swaps in the odd register within the turnaround.

| Response (SCK sampling edge to the next MISO bit) | Cycles | Path |
|---|---|---|
| next bit inside a byte | 5 | `XFER` right after the wait |
| first bit of the next data byte | 8 | `SHIFT_IN`, `SR_RD`, `SR_LD`, `SHIFT_OUT` |
| first data bit, even address | 5 | the guess was right: `XFER` |
| first data bit, odd address | 13 | the guess replaced (9 cycles to `SHIFT_OUT`) |
| MISO driven after CS falls | 4 | `OE_SET` right after the wait |

Shortest SCK period: **16 cycles** (750 kHz) in every mode, set by the two
register loads and the guess that must fit between the last two command
bits; the test also shows 14 fails. CS low to the first edge ≥ 8, CS high ≥
32; CS high ends a transfer anywhere, a byte cut short is not stored.

### I2C target — `firmware/i2c_target.s` (`test_targets.py`)

A 128-byte EEPROM-style device at a configurable address: pointer writes,
sequential writes, current and random reads with repeated START; a START or
STOP in the middle of a byte drops that byte. Open drain (OE pulls low).

| Response (SCL falling to SDA at the pin) | Cycles |
|---|---|
| a read data bit | 10 (0) / 11 (1) |
| first bit of a read byte | 9 after the address ACK, 12 after the controller's ACK |
| ACK after the pointer or a data byte | 11..15 |
| ACK after the address byte | 17..21 (write) / 19..23 (read) |

A byte ends in a 5-cycle loop that watches both lines for START and STOP,
hence the ranges. At 100 kHz these leave over 40 cycles of SCL low. At
400 kHz (SCL low 16 cycles at a 12 MHz core) the address ACK does not fit:
with `STRETCH=1` the target holds SCL low while it prepares, and every
transfer works; with `STRETCH=0` the test sees the late ACK.

## 2. Bus monitors

The chip listens without driving anything. **Thread 0** captures the bus;
**thread 1** reports each event to the host as two UART bytes (a code,
then data) at 1 Mbaud on `uio[0]` (`firmware/report.inc`). Between them is a
queue in data RAM (`firmware/ring.inc`) where a slot holds 0 while it is
free: the writer fills a free slot, the reader takes a word and writes the
0 back, so each slot has one writer at a time and nothing needs locking. A
full queue drops words and the reporter says how many (`'O'`).

One rule shaped all three monitors: thread 0 must be waiting for the next
edge before it comes, or a bit is lost. With two threads running, every
instruction takes 2 cycles and a queue write about 22, so thread 0 queues
as little as possible, never at the moment it matters most, and measures
every phase from level waits (which cannot miss an edge).

### UART — `firmware/mon_uart.s` (`test_monitors.py`, 3 tests)

The RX engine decodes every byte while thread 0 times the same frame edge
by edge. Events: `'U'` byte, `'F'` byte with a low stop bit, `'B'` the
frame's first rising edge off its nominal place by more than `TOL` (signed
cycles: the baud error), `'G'` a glitch shorter than half a bit. Breaks and
back-to-back frames are decoded.

### I2C — `firmware/mon_i2c.s` (`test_monitors.py`, 6 tests)

Events: `'S'` / `'P'` START / STOP, `'A'` / `'N'` a byte acknowledged or
not, `'X'` a START or STOP inside a byte (with the bits seen), and the
timing checks of standard mode (defines `LMIN` = 56, `HMIN` = 48 cycles):
`'L'` an SCL low shorter than 4.7 µs, `'H'` a high shorter than 4.0 µs,
each with its length in cycles.

* **No false alarms.** Every length is counted from the cycles the waits
  on SCL released; where an edge may have come before its wait began, from
  the earliest it can have been. A phase is reported only when even its
  longest possible length is too short. Sweeps of 192 legal bytes (lows
  56..400, highs 48..120 cycles) raised no event at all.
* **Accuracy.** A high: ±1 cycle (16 for anything shorter). A low: −1 to
  +10 (the +10 when SCL fell between a look at the lines and the wait that
  follows it, on highs over 52 cycles); lows shorter than thread 0's own
  turnaround read about 40..54, still reported.
* **Never queue at an SCL fall.** A byte, and a timing word, are queued
  after the *next* rise, once SDA is sampled; so the first bit of each byte
  is not timed (its high, nor the low after it when that high is under ~56
  cycles), and a short phase on a byte's 9th bit is flagged after the byte
  (`'L'` / `'H'` with data 0) instead of measured.
* **One timing event per byte.** After the first short phase thread 0
  decodes the rest of the byte untimed, which bounds both its own work and
  the report rate.
* **Decode envelope.** With every bit out of spec it still decodes every
  byte whenever the SCL lows are at least 34 cycles and the bits at least
  72 (6 µs; the legal minimums are 56 and 104).

Isolated faults (one short phase per byte, on every bit, at 100 kHz and at
the legal minimum) are each reported with their length; `test_mon_i2c_*`
also checks a 70 000-cycle SCL low (past the 16-bit counter's wrap) reads
as fine.

### SPI — `firmware/mon_spi.s` (`test_monitors.py`, 3 tests)

Events per transfer: `'>'` a byte on MOSI then `'<'` the byte on MISO; at
CS high `'M'` if SCK was not at the mode's idle level when CS fell (the
controller uses another mode) and `'E'` with the bits of a byte left over.
Thread 0 keeps up with SCK periods of 28 cycles inside a byte and 60
between bytes (430 / 200 kHz), with 16 cycles from CS low to the first
sampling edge. Sustained traffic is bounded by the report link (two events,
~480 cycles a byte); bursts of up to 32 bytes are buffered.

## 3. UART to SPI bridge — `firmware/bridge.s` (`test_bridge.py`)

A serial-to-SPI adapter on the two threads. Thread 0 receives UART bytes
(RX engine) and queues them; thread 1 runs the SPI transfers they announce
(a length byte *n*, then *n* bytes: CS low, each byte shifted out as it
arrives, the byte read back sent on the UART TX, CS high after the last).
The queue is the only link: neither thread waits for the other except for
data. Tested in all four modes against a shift-register device model.

## 4. Timing-fault injection — `test_faults.py`

The controllers' timing is set by assembler defines whose defaults keep
the normal waveforms; other values move edges by exact amounts, so the chip
can break a device's timing on purpose and find how much it tolerates.

| Firmware | Knob | Effect at the pins |
|---|---|---|
| `uart_tx.s` | `BIT`, `STOP` | every bit lasts `BIT` cycles, the stop bit `STOP` (schedule on `WAIT_CMP`: no drift) |
| `i2c_master.s` | `QL`, `QH` | SCL low / high phases change by 3 cycles per count |
| `bridge.s` | `SH`, `SL` | SCK high 4 + 4 `SH` cycles, low 10 + 4 `SL` (mode 0) |

The tests measure each knob at the pins, and sweep the UART bit time
against a receiver that samples every bit at its nominal centre: it decodes
for 99..109 cycles per bit and fails at 98 and 110, which is exactly where
its stop-bit sample (9.5 nominal bits in) and bit 7's (8.5) leave their
bits, a ±4.8 % tolerance. One cycle of knob moves the result where the
arithmetic says it should.

On hardware a fault sweep needs two parts, one injecting and one listening:
two chips (one runs a controller with its knobs set, the other a monitor),
or one chip against the device under test with the demo board's RP2040
reading the reports.

## 5. Compared with the RP2040's PIO

The RP2040 is on the Tiny Tapeout demo board, and its PIO blocks are the
best-known programmable I/O. Three examples from the Raspberry Pi
`pico-examples` repository against our firmware for the same jobs
(`ws2812.s` is checked by `test_fw_ws2812`):

| Job | PIO program | PIO cycles a bit | msfcore firmware | Cycles a bit (12 MHz) | How |
|---|---|---|---|---|---|
| WS2812 LEDs | `ws2812.pio`, 4 instructions | 10 | `ws2812.s`, 52 words (16 a table) | 15 | TX engine sending 5-cycle NRZ symbols, a table turns each nibble into 12 |
| UART TX 8N1 | `uart_tx.pio`, 4 instructions | 8 | `uart_tx.s`, 29 words | 104 at 115200 (any) | `WAIT_CMP` on absolute times: no drift over a message |
| SPI controller | `spi.pio`, 2 (CPHA 0) / 3 (CPHA 1) instructions | 4 | `spi_master.s`, 57 words, all modes | 12 | `XFER` shifts MOSI out and MISO in at once |

The PIO programs are smaller and faster per bit, and an RP2040 runs them
at up to 133 MHz. But a PIO program only moves bits: the C program on the
ARM cores fills its FIFO, frames the bytes and makes the protocol's
decisions. Our images are the whole job on the chip alone (data handling,
framing, the done flag), and the same core runs what needs decisions at
bit rate: the SPI target's address guess, the I2C target's ACKs, the
monitors' timing checks. The SERDES and CRC unit do what PIO leaves to
software (NRZI with bit stuffing and CRC16 for USB, Manchester and CRC-32
for 10BASE-T).

What we would take from PIO next time:

* **Side-set:** driving a pin as part of any instruction would fold most
  of our `PIN_TGL` / `NOP` sequences (SPI, the bridge) into the data
  instructions.
* **Per-instruction delays** (`[n]`) instead of `NOP` chains and delay
  loops, which also cost code space.
* **FIFOs between engines** instead of a queue in data RAM: the I2C monitor
  spends about 22 cycles on each queue write, and its whole design works
  around that.
* **More engines:** PIO has four state machines per block; with two
  threads, a monitor spends one just on reporting.
