# Instruments: device emulation, bus monitors, fault injection

The brief asks for a tool to debug and reverse-engineer digital protocols.
This page covers the firmware that makes msfcore one: **targets** (the chip
plays a device), **monitors** (it listens to a bus and flags timing
faults), a **bridge** (a host drives SPI through the UART, one thread per
side), **timing-fault knobs** (the controllers break a device's timing
by exact amounts) and a **logic analyser** (the capture engine records 8
pins with a trigger), and from §9 on the tools of a reverse-engineering session:
a **pin finder**, **replay**, **learning a protocol from a recording**, a **flight
recorder**, a **margin finder** and an **in-line interposer**. None of it is
protocol hardware: it is firmware on
the core's level waits, cycle counter, shift register, SERDES and capture
engine, loaded at run time. Every claim below is checked at the pins in simulation by the
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

### SPI flash — `firmware/spi_flash.s` (`test_targets.py`, 4 tests)

The chip answers as a small 25xx-style serial NOR flash, so a host that
boots from or programs a flash can be run against it and watched. It
implements the commands such hosts use: JEDEC ID (`9F`, `9E`; the ID is a
define), read status (`05`), write enable and disable (`06`, `04`), `READ`
(`03`) and `FAST_READ` (`0B`) with a 3-byte address, page program (`02`:
each byte is ANDed in, as programming only clears bits, and the address
wraps inside its 256-byte page) and the erases (`20`, `52`, `D8`, `60`,
`C7`). The store is 512 bytes, one byte a word in SRAM 512-1023, mirrored
over the 24-bit address space. An erase runs while CS is high and pauses
while it is low, so a host polling the status sees WIP set and then clear,
as from a real part; WEL clears when a program or erase ends.

As in the SPI target, the reply to `05` or `9F` starts in the byte right
after the command: the firmware guesses it from the command's first seven
bits and puts its first bit out with the eighth. Commands are dispatched
through a 16-entry jump table on the low nibble, so every command takes the
same path. The longest path is from a `READ`'s last address bit to the wait
for the first data bit, which sets the shortest SCK period: **28 cycles**
(430 kHz at 12 MHz) in every mode; the test also shows 26 fails. Writing it
showed one more limit: the SCK waits time out to check CS, and a timeout's
check (7 cycles) on that path made it miss an edge, so the timeout (24) is
longer than half the fastest SCK. CS low to the first edge ≥ 16 (≥ 32 while
an erase runs), CS high ≥ 64.

Tested against an SPI controller model and a reference model written from
the firmware's header: ID, status, reads across the 512-byte wrap and with
the high address bits set, `FAST_READ`, a transfer cut short and an unknown
command, in all four modes at the fastest SCK and at one where every wait
times out; program with and without WEL, the page wrap and the AND; each
erase command with and without WEL, with the status polled during it.
Not modelled: commands other than these, write protection (BP bits, `01`
write status), power-down, and refusing commands while WIP is set.

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
then data) at 1 Mbaud on `uio[0]` (`firmware/report.inc`). Between them is
the hardware FIFO (`isa.md` §6.5): its words are in data RAM and its
positions in hardware, so a push and a pop are one instruction each and
nothing needs locking. A full FIFO drops words and counts them, and the
reporter says how many (`'O'`).

One rule shaped all three monitors: thread 0 must be waiting for the next
edge before it comes, or a bit is lost. With two threads running, every
instruction takes 2 cycles, so thread 0 queues as little as possible,
never at the moment it matters most, and measures every phase from level
waits (which cannot miss an edge). The first versions kept the queue in
data RAM in software (about 22 cycles a write); moving it to the hardware
FIFO made a push 2 cycles, the monitors 40 to 70 words smaller, and the
limits below wider (the earlier figures are in `history.md` §9).

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
  turnaround read about 38..54, still reported.
* **Never queue at an SCL fall.** A byte, and a timing word, are queued
  after the *next* rise, once SDA is sampled; so the high of each byte's
  first bit is not timed (the low after it is, whenever that high lasts at
  least 36 cycles, which every legal high does), and a short phase on a
  byte's 9th bit is flagged after the byte (`'L'` / `'H'` with data 0)
  instead of measured.
* **One timing event per byte.** After the first short phase thread 0
  decodes the rest of the byte untimed, which bounds both its own work and
  the report rate.
* **Decode envelope.** With every bit out of spec it still decodes every
  byte whenever the SCL lows are at least 16 cycles and the bits at least
  72 (6 µs; the legal minimums are 56 and 104): checked on 196 low / high
  pairs with lows of 16 to 76 cycles and bits of 72 to 84.

Isolated faults (one short phase per byte, on every bit, at 100 kHz and at
the legal minimum) are each reported with their length; `test_mon_i2c_*`
also checks a 70 000-cycle SCL low (past the 16-bit counter's wrap) reads
as fine.

### SPI — `firmware/mon_spi.s` (`test_monitors.py`, 3 tests)

Events per transfer: `'>'` a byte on MOSI then `'<'` the byte on MISO; at
CS high `'M'` if SCK was not at the mode's idle level when CS fell (the
controller uses another mode) and `'E'` with the bits of a byte left over.
Thread 0 keeps up with SCK periods of 28 cycles inside a byte and 36
between bytes (430 / 330 kHz), with 16 cycles from CS low to the first
sampling edge. Sustained traffic is bounded by the report link (two events,
~480 cycles a byte); bursts of up to 32 bytes are buffered.

## 3. UART to SPI bridge — `firmware/bridge.s` (`test_bridge.py`)

A serial-to-SPI adapter on the two threads. Thread 0 receives UART bytes
(RX engine) and pushes them into the FIFO; thread 1 runs the SPI transfers they announce
(a length byte *n*, then *n* bytes: CS low, each byte shifted out as it
arrives, the byte read back sent on the UART TX, CS high after the last).
The FIFO is the only link: neither thread waits for the other except for
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

## 5. Logic analyser — `firmware/logic_analyzer.s` (`test_capture.py`)

The capture engine (`isa.md` §6.6) records 8 pins into an SRAM ring
buffer while the core goes on with anything else; the firmware arms it,
waits for the trigger and the words after it, and sends the buffer to the
host, oldest first, with the cycle count just before arming, so every
sample's time is known exactly.

* **Sample mode:** every *div* cycles (1 to 65535), two samples a word: at
  12 MHz and *div* 1, 12 Msamples/s, 1 024 samples in a 512-word buffer.
* **Change mode:** a word per change with the samples since the last one,
  so a quiet bus fills the buffer slowly: one word per 255 samples while
  nothing moves.
* **Trigger:** a pattern on any of the 8 pins, as a level or an edge, with
  history before it and a chosen number of words after.
* It never changes the core's timing (`test_cap_never_changes_core_timing`:
  a data-RAM-heavy loop takes the same number of cycles recording or not,
  and the capture is intact), and it says when a long burst of the core's
  own data-RAM use cost it a word (`test_cap_overflow_flag`).

The tests check every recorded word against a model of the rules in the
spec: both modes, every *div* tried, level and edge triggers, the ring
with pre-trigger history, `ui` as the source, and the firmware's UART
dump decoded back into the samples driven.

## 6. Debug ports

### JTAG probe — `firmware/jtag.s` (`test_debugports.py`, 3 tests)

An IEEE 1149.1 master a host drives over the UART (115200 at 12 MHz): `R`
resets the TAP into Run-Test/Idle, and `I n data…` / `D n data…` run IR and
DR scans of 1–255 bits, starting and ending in Run-Test/Idle; the bits read
from TDO come back LSB first, a byte at a time. Pins: TCK `uio[2]`, TDI
`uio[3]`, TDO `uio[4]`, TMS `uio[5]` (the SPI header), UART on `uio[0]`/`uio[1]`.
The shift register does the data: one `XFER` per bit drives TDI and samples
TDO while TCK is low, LSB first, and TMS rises with the last bit. TCK is
high 4 cycles and low at least 13 (about 700 kHz at 12 MHz; `HALF` slows it).

The tests run it against TAP controllers modelled from the standard's
16-state diagram (4-bit IR capturing `0001`, IDCODE after reset, BYPASS):
a reset and a 32-bit DR scan read the IDCODE; an IR scan returns the
capture value and loads BYPASS, after which data comes back one bit late
(also for a 13-bit scan ending in a partial byte); with two devices a
64-bit scan reads both IDCODEs and a scan of ones after BYPASS comes back
with two leading zeros, which is how a host counts a chain. The models also
check that TDI and TMS change only while TCK is low and settle at least two
cycles before it rises.

### SWD probe — `firmware/swd.s` (`test_debugports.py`, 3 tests)

An ARM ADIv5 Serial Wire Debug host, also driven over the UART: `L` sends
a line reset, the JTAG-to-SWD sequence `0xE79E`, another line reset and
idle cycles; `T h [data]` runs one transaction on the DP or an AP. The
probe builds the 8-bit request (start, APnDP, RnW, A[3:2], parity, stop,
park) from the 4-bit header, releases SWDIO for the turnaround, reads the
ACK, and then reads 32 bits and their parity or writes 32 bits with the
parity it computes; after WAIT, FAULT or no answer (ACK 7: the pull-up)
there is no data phase, as the specification requires. SWCLK is `uio[2]`;
SWDIO `uio[3]` is both the shift register's output and input, driven only
while the host owns it. The edges follow CMSIS-DAP: the host changes SWDIO
while SWCLK is high and reads it while low.

Tested against an ADIv5 SW-DP with a MEM-AP modelled from the
specification (which also flags any cycle where both sides drive SWDIO): it
reads DPIDR; selects AP bank 0xF and reads the IDR through RDBUFF (AP reads
are posted); writes CSW and TAR, reads a word of the target's memory and
writes one; gets WAIT and succeeds on the retry; and reads ACK 7 with
nothing on the wire. A real target for the board is the TI microcontroller
we already have.

## 7. Other buses

### PS/2 keyboard — `firmware/ps2_device.s` (`test_buses.py`, 3 tests)

The chip plays a keyboard: bytes from the UART go to the PS/2 host as
scancodes, the host's commands get a keyboard's answers (`FF` → `FA AA`,
`EE` → `EE`, `F2` → `FA AB 83`, `ED`/`F3` and their argument → `FA`,
others `FA`, bad parity `FE`), and every byte the host sends is reported on
the UART, so the probe also shows what a host asks of a keyboard. CLK
`uio[2]` and DATA `uio[3]` are open drain (the chip only pulls low). The
device makes the clock in both directions on `WAIT_CMP` deadlines: low and
high exactly 2Q each (Q = 240 cycles: 12.5 kHz at 12 MHz). The host comes
first: a frame it interrupts by holding CLK low before the 11th clock is
sent again whole. Thread 1 only moves UART bytes into the send queue (the
FIFO), so scancodes typed faster than PS/2 can send them are not lost.

Tested against a PS/2 host modelled from the protocol: frames decoded on
falling edges with start, odd parity and stop checked, every clock phase in
a frame within the standard's 30–50 µs, DATA changing only while CLK is
high; commands sent with inhibit and request-to-send, each ACKed in the
11th clock; a frame interrupted after 4 bits arrives whole afterwards.
`tools/msf_timing.py` certifies its 10 deadlines (two threads, worst slack
207 cycles).

### 1-Wire master — `firmware/onewire.s` (`test_buses.py`, 2 tests)

A UART-driven master for Maxim/Dallas 1-Wire parts (temperature sensors,
ID chips, EEPROMs) at standard speed. DQ is `uio[3]`, open drain with a
pull-up. Commands: `R` resets the bus and answers `P` if a device sent a
presence pulse, `N` if not; `W n b…` writes n bytes; `r n` reads n bytes and
returns them; `S` runs SEARCH ROM (Maxim application note 187) and returns
`D` and the 8 ROM bytes of every device on the bus, then `E`. Every slot
edge is a `WAIT_CMP` deadline from the slot's falling edge: reset 480 µs
low, presence sampled at 70 µs; write 1 is 6 µs low and write 0 60 µs, in
a 70 µs slot; a read is 6 µs low and sampled at 15 µs.

Tested against 1-Wire devices modelled from the specification (presence
pulse 30 µs after the reset, a device bit read 30 µs into each slot, ROM
commands READ, MATCH, SKIP and SEARCH, READ SCRATCHPAD with its CRC-8):
reset with and without a device, READ ROM with a correct CRC-8, SKIP ROM
then READ SCRATCHPAD, and SEARCH ROM finding each of three devices (two
of the same family with consecutive serial numbers) exactly once. A host has to wait
for each command's reply before sending the next: the UART receive buffer
holds one byte, and a read of n bytes takes n × 0.56 ms. `tools/msf_timing.py`
certifies its 8 deadlines (worst slack 63 cycles).

### CAN 2.0A node — `firmware/can.s` (`test_buses.py`, 3 tests)

A classic CAN node (11-bit identifiers) behind a transceiver: TXD on
`uio[2]` (0 = dominant), RXD on `uio[7]`. `T idh idl dlc d0…` sends a data
frame once the bus has been idle for 11 bits, with arbitration: a node that
loses goes on receiving and sends again after the other frame; with no ACK
or an error it tries 8 times in all, then replies `N` (`K` when sent and
ACKed). Every frame on the bus with a good CRC is ACKed and reported as
`R idh idl dlc d0…`; a bad CRC is reported as `E C` and not ACKed. One bit
engine runs whether the node is sending or not, as in a CAN controller: it
drives TXD at each bit start and samples the bus at the sample point, both
on `WAIT_CMP` deadlines; it removes stuff bits and checks the stuffing
rule, and the CRC unit computes CRC-15 (0x4599) one bit at a time. A second
thread only moves UART bytes into the command queue (the FIFO). The bit
time is a define: 480 cycles (50 kbit/s at 24 MHz, 25 kbit/s at 12 MHz) is
the smallest that `tools/msf_timing.py` certifies (6 deadlines, worst slack
27 cycles). With two threads each instruction takes 2 cycles, and the
longest path from a sample point to the next bit start is 84 instructions.
Hard synchronisation on SOF only, no resynchronisation (fine for one frame
at crystal accuracy); no error frames, error counters, remote-frame
answers or 29-bit identifiers.

Tested against CAN nodes modelled from the specification (bus integration,
stuffing, CRC-15, arbitration, ACK): two good frames received, ACKed and
reported, and a bad-CRC frame reported and not ACKed; an 8-byte frame sent
and received by the node with a good CRC; arbitration lost in the
identifier to a node joining the same SOF, its frame reported, then ours
sent again and ACKed; and with no node to ACK, 8 tries and `N`. The chip
reports a frame over the UART at 115200 baud, so a frame that starts while
it is still reporting the last one is missed.

## 8. Compared with the RP2040's PIO

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
  spent about 22 cycles on each queue write, and its whole design works
  around that. *Done on the `rtl-changes` branch:* a hardware FIFO (one
  instruction to push or pop) now carries the monitors' events.
* **More engines:** PIO has four state machines per block; with two
  threads, a monitor spends one just on reporting.

## 9. A reverse-engineering session, with one chip

Sections 9 to 13 go together. On a board nobody documented, one chip and no other
instrument can: find the debug and UART pins (§10), record the traffic (§11), learn
the protocol and speak it (§12), watch the bus and keep the waveform around the first
fault (§13), find how far a device can be pushed before it breaks (§14), and sit in
the wire to change one message (§15). Only the learning needs a PC, to run the tool.
Each is firmware or a host tool: none needs anything the chip does not already have.

## 10. Pin finder — `firmware/pinfind.s` (`test_pinfind.py`, `tools/pinfind_all.py`)

Which of eight unknown wires are a UART, an SWD port or a JTAG port? Connect `uio[7:0]`
and a ground, load this, and read the report on `uo_out[0]`, 115200 baud at 12 MHz, as
all of `uio` is in use. It leaves the results in data RAM too.

* **It listens first and drives nothing.** A direct loop over `PIN_R` catches every
  pin that moves in the window, and a pin that moves by itself is being driven, so it
  is never driven by this program.
* **UART:** the capture engine records the window; each moving pin's shortest stable
  run is taken for a bit time, and the RX engine has to confirm it on what follows:
  past the first 4 frames (it may start mid-stream), at least 2 good, none bad, and
  more than one value, as a clock line reads as a stream of `0x55`.
* **SWD:** every ordered pair of the quiet wires as (SWCLK, SWDIO): line reset,
  JTAG-to-SWD, DPIDR read twice. It needs ACK OK, good parity and the same value twice.
* **JTAG:** every ordered pair as (TCK, TMS): reset the TAP and shift out the IDCODE
  twice, reading every other pin at once, so TDO needs no search of its own. Bit 0 set,
  not all ones, the same both times. Then each pin left is tried as TDI: ones into IR
  to select BYPASS, and a pattern sent through DR has to come back on TDO up to 8 clocks
  late.

`OE_SET` and friends take immediates, so a program that picks its pins at run time
writes the instruction it is about to run (`isa.md` §1 allows that, at least 2
instructions ahead).

Tested against the same SW-DP and TAP models as the probes, with their pins renamed to
where the firmware cannot know them: a sample on the RTL, and **every one of the 56
ordered pairs for SWD and for JTAG** on the ISA model (`tools/pinfind_all.py`, about 4
minutes; the model cannot run the listen step, which needs the capture engine). No false
finds with all wires floating high, all stuck low, or two toggling at random; a square
wave is not reported as a UART; a wire that was moving is never driven
(`test_pinfind_uart_and_leaves_it_alone`).

**Limits.** Driving unknown pins can fight what is on them. Each try is short and a wire
that moves is left alone, but a real target needs series resistors and a matching
voltage. A pin driven to a constant level looks like a free one. The UART line must be
busy, with more than one value, during and after the listen. It is 831 words, which fills
the firmware space up to its 64-word capture ring.

## 11. Capture tools — `tools/msf_capture.py`, `firmware/la_stream.s`, `firmware/replay.s` (`test_capture_tools.py`)

* **Export.** `msf_capture.py export` turns the logic analyser's dump, in either mode,
  into a VCD or a sigrok session, for PulseView and `sigrok-cli`. Round trips are tested
  against what the pins carried, and `sigrok_check.py` reads a session the chip recorded
  with sigrok's own UART decoder.
* **Streaming.** `la_stream.s` sends each change word as it appears, so a recording isn't
  limited to the buffer: 50,000 words a second at 1 Mbaud, with bursts up to the ring's
  size. A word goes out every 255 ticks even with no change, so at *div* 1 an idle line
  uses nearly all of that: raise *div* for a long recording. If it falls behind it sends
  the word `0x0000` and sets `uo_out`, and everything before that is true
  (`test_stream_says_when_it_falls_behind`).
* **Replay.** `replay.s` re-drives a recording on the absolute cycle counter. Its table is
  the capture engine's change-mode format, so what the chip recorded can be played back as
  it stands (`test_replay_what_the_chip_recorded`). `msf_capture.py replay` builds a table
  from a capture, a VCD or a sigrok session. The loop keeps changes at least 16 cycles
  apart, which `msf_timing.py` certifies from a declared range that the tool enforces on
  the data; closer changes are refused, or with `--max-shift` moved later by a reported
  amount. A table that breaks that promise anyway ends with `uo_out` = `0xEE`, not a silent
  wrong waveform. An I2C transaction replays edge for edge within that bound, with no drift.

**Limits.** Replay is push-pull: `OE` has no register form, so an open-drain line is not
replayed as one, and a table holds 511 changes.

## 12. Learning a protocol from a recording — `tools/msf_learn.py`, `tools/msf_gen.py` (`test_learn.py`, `test_learn_tool.py`)

Point the logic analyser at a transmitter nobody documented. `msf_learn.py` works out how
its bits are coded and what its frames hold, from the timing alone, and `msf_gen.py` turns
that into firmware, so the chip can send a message the transmitter never sent. Escapement
and openPIO have this too; the method (try several line codes, keep the one that writes the
recording down in the fewest bits, refuse what fits none) is theirs, the code is ours.

* **Line codes:** pulse distance, pulse length, pulse width, Manchester and UART serial. A
  carrier under the marks is taken off first and remembered.
* **Check fields:** a byte that in every frame is the complement of another, or the XOR, sum
  or negated sum of those before it. A CRC of any polynomial, computed from the frames with
  a gcd over GF(2), in either bit order. A rule is claimed only if its inputs took at least
  three values: a constant proves nothing. A new message gets them recomputed.
* **The generated firmware** waits for each phase's deadline and changes the pin at once, so
  no path moves an edge, and `msf_timing.py` must certify it or it is refused.
* **On the RTL:** a NEC remote built from the specification presses four buttons; the chip
  records them with `logic_analyzer.s`; the tool finds pulse distance, the 32 bits and the
  command's complement, and not the address's, which never changed; and the chip sends a
  command the remote never sent. A receiver written from the specification reads it within
  NEC's windows, and so does sigrok's `ir_nec` decoder (the timing is scaled down 20 times
  to keep the simulation short, and sigrok is told the rate is 20 times lower). The same for
  Manchester and UART, and a carrier comes back on the cycle.
* **Tests of the tool alone,** 176 cases: random pulse codes, Manchester and serial must be
  read word for word; with 10 % jitter, read or refused, never wrong; noise, square waves,
  servo pulses of any width and tone bursts must be refused.
* **Real recordings:** 390 files from sigrok-dumps under 70 kB (IR and RC remotes, DALI, DMX512,
  DCC, AM230x and DHT11 sensors, LED strips, gray code, Wiegand, and more), every channel. It
  refused 138 channels and made a claim on 82 channels of 56 files. Of those, 42 have the
  protocol's own framing and words that agree across repeats (NEC remotes, with the carrier
  taken off where the raw channel was recorded; RC-5 at 14 bits and a half bit of 877-883 us;
  Sony SIRC's pulse lengths; DALI's 17 Manchester bits at 420 us), 25 have a plausible shape whose
  values were not checked (the sensors' 40-bit replies after the host's start pulse, Bose's 15-bit
  Manchester), and **15 are wrong**: DMX512 read as 6N1 or 7E1 serial at 225-253 kbaud (it is 250
  kbaud 8N2), RC-6 (Manchester with a double-length trailer bit) read as serial, an LED strip's
  PWM and clock lines read as serial. The run found four rules that were too loose (a pulse code
  where one symbol appears once, DCC taken as Manchester, Wiegand's two values taken as serial,
  a coarse recording's bit time), which were tightened; the 15 wrong claims stand, and say
  that the serial model accepts too much. The right and wrong calls are read off the protocols'
  known framing, not checked with sigrok's decoders, and the files are not committed.

**Limits.** Single-wire, self-timed codes. Clocked buses, conversations and generated
receivers are not done. On real recordings, about one claim in five is wrong (above). The plan's
step 5, the measured spread of each symbol driving the margin finder, is not done. Manchester's bit polarity is a convention the waveform can't settle.
A recording is limited by §11. The first version failed many of its random tests in the
ways Escapement's did: runs of equal symbols read as a carrier, chains of near lengths read
as symbols. Each is fixed by a rule, not a special case, and the tests stay.

## 13. Flight recorder — `firmware/flight_*.s`, `flight.inc` (`test_flight.py`)

A monitor says a device broke the timing, but not what the waveform looked like. The
flight versions of the three monitors also keep the capture engine recording into a ring,
and the first time an event counts as a fault they keep 32 more words, stop, and send the
ring after the fault's events: 256 words of change records, oldest first. Reporting then
goes on, and the ring stays stopped on that first fault.

The stop uses the engine's trigger: armed with a condition that can never hold (mask 0,
value 1), then `CAP_TRIG` with mask 0, value 0 makes the next sample the trigger word, and
the engine stops by itself `POST` words later, keeping its place in the ring. Writing
`CAP_CTL` would stop it as well, but resets its place.

Faults: I2C `H`, `L`, `X`; SPI `M`, and `E` with bits left over; UART `F`, `B`, `G`. The
watching threads' code is the plain monitors' (`mon_*.inc`), and a thread's timing depends
only on which threads run, so `test_flight_watching_thread_is_mon_i2c` gets exactly
`mon_i2c.s`'s events with the ring in between, and §2's accuracy figures carry over. The ring
holds the fault at its true length, to the cycle. A legal bus never dumps.

**Limits.** It records every `uio` pin, so the reporter's own UART takes a share of the ring.
While the ring is being sent, about 5 ms, events wait in the FIFO, and past its 64 words they
are counted and reported (`O`), as ever.

## 14. Margin finder — `firmware/margin_*.s`, `margin.inc` (`test_margin.py`)

§4 finds a receiver's limits with Python driving the sweep. This does it on the chip alone:
load one program, connect the device, and read a table over UART. For each value of one timing
parameter it runs a transaction a set number of times, and reports `P`, the value, the passes;
then `R`, the unbroken run that always passed. It is left in data RAM as well.

| Firmware | Parameter | Passes if | Known answer, against a model with that limit |
|---|---|---|---|
| `margin_uart.s` | bit time we send at | the device echoes the byte | 99..109 cycles against a 104-cycle receiver; 76..84 against an 80-cycle one |
| `margin_i2c.s` | SCL low or SCL high | the device ACKs its address | 40 and 33, set in the model, found exactly |
| `margin_spi.s` | SCK half period | the next byte reads back as the device means | 28, set in the model, found exactly |

It found an error in the test that checks it: the arithmetic said 77 cycles for the 80-cycle
receiver, and the chip said 76, which is right, since the stop sample may land in the idle line
after the stop bit.

Every edge is on the cycle counter, so a phase is exactly the value swept. MarcosAsh's
`demo/margins.py` sweeps real parts past their datasheet limits with a Pico, a logic analyser
and a PC; this needs none of them. A device that answers nothing reports `lo = 0`.

**Limits.** A step is one core cycle, 83 ns at 12 MHz, about 1 % of a bit at 115200 baud.
`SWEEP_LO` must leave time for the bit loop: the checker refuses less. I2C clock stretching
isn't followed, so a stretching device reads as a fail.

## 15. In-line interposer — `firmware/interpose_*.s` (`test_interpose.py`)

To see how a system reacts when one message goes wrong, something has to sit in the wire. These
pass the bus through, so that neither side can tell, and change exactly one thing, with no host.

* **UART,** both directions: flip bit *K* of frame *N* going from A to B, or drop frame *N*, with
  the line held high for it and no glitch. Thread 0 is the wire: a 16-cycle loop, so an edge comes
  out 15 to 30 cycles late, on every phase of the loop, measured
  (`test_uart_lateness_over_every_phase`): at 104 cycles a bit a receiver has room for that.
  Thread 1 reads the same line with the RX engine, counts frames, and tells thread 0 what to change
  through one word of data RAM.
* **SPI:** SCK, MOSI and CS pass from `ui` to `uo` read and written together, so they keep their
  relationship to the cycle, and MISO comes back. Thread 1 counts SCK's falling edges and flips one
  MOSI bit. How fast it goes, as half periods measured on the RTL, with SCK at 12 MHz: 35 cycles,
  171 kHz, for a controller that reads MISO back, which has to wait for the round trip through the
  loop twice; 12 cycles, 500 kHz, for MOSI alone; 22 cycles, 272 kHz, with the flip set. One cycle
  below the first and the last fails, and the test holds that.

**Limits.** I2C is left out: open-drain lines need direction sensing. The lateness is real, and
costs a receiver margin. What to change is set when the firmware is assembled.

## 16. Protocols drawn at random — `firmware/draw_*.s` (`test_draw_*.py`, `docs/draw/`)

The brief asks for new protocols after fabrication. To show it rather than assert it, six were
drawn from a public pool with a drand round fixed beforehand (`tools/protocol_draw.py`, the rule
committed in a34b7eb before the round fell), and written for the finished chip: `src/` did not
change. The plan was two blind agents per protocol, one for the firmware and one for the model of
the other side. Sam stopped the agents before any reported, so one session wrote all six, firmware
and test each, from the specifications (`docs/draw/README.md` says what that costs: the test's peer
is no longer an independent judge). What judges each one: the decoder from sigrok, written by other
people and not read, and the specification as it stands; `msf_timing.py` for the deadlines.

| Drawn | The chip is | Time | Words | Clock | RTL runs | Deadlines, worst slack | sigrok's decoder |
|---|---|---:|---:|---|---:|---|---|
| `sda2506` | controller of a Siemens SDA 2506-5 EEPROM | ~9 min | 153 | 12 MHz | 2 | 16, 44 cycles | **disagrees on the bytes read**: agrees on every command, address and the data written, reads 5A as B5 and A7 as 4F |
| `spdif` | S/PDIF transmitter, 48 kHz, 16-bit stereo | 7 min | 141 | 30.72 MHz | 3 | none: the TX engine | agrees: 2443 lines, every preamble, sample and channel status bit |
| `em4100` | EM4100 tag, ID 1A 2B3C4D5E | 22 min | 104 | 12 MHz | 3 | none: the TX engine | agrees: five tags `1A2B3C4D5E` |
| `microwire` | controller of a 93C46 EEPROM | 8 min | 150 | 12 MHz | 2 | 6, 3 cycles | agrees: EWEN, WRITE 5 = BEEF, READ 5 = BEEF, READ 6 = 1234, EWDS |
| `cec` | HDMI-CEC device at logical address 4 | 31 min | 315 | 12 MHz | 2 | 10, 93 cycles | agrees: Image View On ACK, Active Source, four attempts to address 8 NACK, Give Physical Address from the TV acknowledged, Report Physical Address |
| `iec` | Commodore serial bus controller, drive 8 | 10 min | 273 | 12 MHz | 2 | 8, 93 cycles | agrees: LISTEN 8, 0x6F, `I0` with EOI, UNLISTEN, TALK 8, 0x6F, `00, OK,00,00` CR with EOI, UNTALK |

Every one also passes its own test on the RTL, against a peer that checks the specification's
timing (the test files list what each checks). The times run from the first line written to the
decoder's verdict; the specifications of SDA 2506, EM4100 and CEC were looked up before, about ten
minutes together. CEC's 31 minutes include two 10-minute simulations (0.55 s of bus, 6.4 million
cycles), during which S/PDIF and SDA 2506 were written. MarcosAsh's blind runs took 24 to 68
minutes each.

**What went wrong, and what the sources disagreed on.**
* EM4100: my bug, an `AND` followed by `JZ`. `AND` sets no flags, so the branch read a stale Z and
  three nibbles came out 0. The RTL test caught it on the first run.
* Microwire: the checker could not bound SK's deadlines while the deadline register ran through
  subroutine calls (it joins the callers' states), so each routine now starts from `CNT_R`. The bus is
  static, so a gap between routines only stretches a phase.
* SDA 2506: the decoder takes a read's bits one pulse early. The data sheet's Read figure (UED01268)
  has D0 come out tDD = 2.5 us after the first clock pulse's trailing edge, D1 after the second, as
  the text says; the firmware and its peer follow that, and the decoder shows the line before D0,
  then D0-D6. The test pins the decoder's reading of the right bytes, `(b << 1 | 1) & 0xFF`, so the
  check still fails on any other change. The figure has its own slip: it labels the bit after the
  eighth pulse D8.
* IEC: Wikipedia's Commodore bus article says a 1 bit is the line pulled ("1=true=ground"). The C64
  KERNAL source (`serial4.0`, in mist64/cbmsrc) sends a 1 with `DATAHI`, released. The firmware follows
  the KERNAL, and the decoder reads every byte.
* S/PDIF: the decoder finds nothing in a capture that starts with the line idle (its rate estimate
  comes out 45 times too high), and locks when the capture starts in the stream, as any real capture
  of an S/PDIF line does. Its "Signal Bitrate" line is a start-up estimate (36-45 kHz shown) and is
  not judged; the test's edges, all 31,216 on the 5-cycle grid at 30.72 MHz, give 48,000 frames a
  second exactly.
* CEC: four attempts in all is the CEC 2.0 suggestion as the Linux kernel's `cec-adap.c` gives it;
  the HDMI specification itself was not read, so the test allows two to six.

**What it shows.** Six protocols the design never had in mind, picked by a public random round, fit
the finished chip: the largest takes 315 of 896 words, all certify, and none needed a change to the
silicon. Two use the TX engine for timing to the cycle: EM4100's Manchester at RF/64, and S/PDIF's
biphase-mark as NRZI, with the clock chosen so that 5 cycles make a half-cell at 48 kHz. **What it
does not:** it is not blind, so it shows the chip and the ISA are enough, not that the documentation
alone is; the peers share the firmware's author; and each protocol is checked in simulation only.
