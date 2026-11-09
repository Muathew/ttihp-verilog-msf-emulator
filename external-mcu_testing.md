# External MCU protocol testing

An LP-MSPM0G3519 is the controller and the FPGA core the target, over PMOD JA. The
core runs free at 12 MHz, not stepped by the PS, so unlike the C board tests this one
runs it at speed against a real device. CAN is out of scope.

Branch `v3` adds a second test on the same wires and the same MCU build: [the v3
board test](#the-v3-board-test), for what v3 added (RX mode 3, the line receivers at
the chip's cycles a bit, the late guard). The MCU notices which FPGA program it faces.

| Protocol | FPGA UIO | MCU pins | Expected behaviour |
| --- | --- | --- | --- |
| UART | UIO[0] RX, UIO[1] TX | UART1: PA8 TX, PA9 RX | 115200 8N1; each byte comes back |
| I2C | UIO[2] SCL, UIO[3] SDA | I2C0: PA1 SCL, PA0 SDA | Address `0x50`; register `0x00` reads `0xA5`, using a repeated START |
| SPI | UIO[4] SCLK, UIO[5] CS, UIO[6] MOSI, UIO[7] MISO | SPI0: PB18, PC7, PB17, PB19 | Mode 0, MSB first, 250 kHz, one byte per CS frame; the MCU sends `55 AA 12 34 DE AD BE EF` and the FPGA answers each with its complement, `AA 55 ED CB 21 52 41 10` |

The boards must share a ground. SCL and SDA need pull-ups to 3.3 V, and the LaunchPad
has its own on PA0 and PA1: **J16 and J17 must stay on 1-2, the default, which is
3.3 V. Position 2-3 is 5 V, which must never reach the FPGA's 3.3 V PMOD pins**
(TI SLAU947, Table 2-1). So external pull-ups are not needed, though they do no harm
if they go to 3.3 V.

## First board run, 5 November

This ran FPGA peer firmware v2, before the changes below. Only bursts 2600 to 2662 of
the FPGA console have been read; the start of that log and the MCU's log have not. The
totals at burst 2662:

| Protocol | Totals | Reading |
| --- | --- | --- |
| UART | 2662 bytes | One per burst, in sequence |
| I2C | 5324 address ACKs, 2662 pointers, 2662 read NACKs, 0 read ACKs, 0 watchdogs | Exactly right for 2662 passes |
| SPI | 21333 ok, 23 bad, 1 quiet-reset | Every byte in those 63 bursts was ok, but the totals hold 60 more bytes than the passes account for |

`lost 14` was the one pass missed while the `LA` table printed, as expected, and
`framing` was 0.

So traffic from the MCU reaches the FPGA on all three protocols, and the FPGA's I2C
ACKs reach the MCU, which would not have gone on without them. What the MCU received
(the UART echo, MISO, `0xA5`) is only in its own log.

The 60 extra bytes and the 23 bad ones all came before burst 2600. At least 37 of the
60 carried the right value, so they were not noise. The likely cause was the MCU's pins
floating during a flash or a reset, with the first bug in the next section. This was
reproduced in simulation but not confirmed on the board: if it is right, burst 1 of that
run has 98 events (14 for the pass, 37 ok, 2 each for the 23 bad, 1 quiet-reset). The
6 November logic analyser table does show that pin state on the board, below.

## Later board runs, 5 and 6 November

| Run | FPGA firmware | Up first | UART at the MCU | SPI at the MCU | I2C at the MCU |
| --- | --- | --- | --- | --- | --- |
| 5 Nov, second | v2 | MCU, about 15 passes ahead | `TIMEOUT` 314 of 314, with `OVERRUN` whenever the FPGA echoed | `FAIL` 314 of 314, as expected: the MCU wanted v3's bytes | `rx=A5` in the 59 passes shown with the FPGA up |
| 6 Nov, first | v3 | FPGA | `PASS` 39 of 39 | `PASS` 39 of 39 | `PASS` 37 of 39, `rx=--` on pass 1 of both MCU runs |
| 6 Nov, rerun with the MCU's fixes | v3 | FPGA | `PASS` 148 of 148 | `PASS` 148 of 148 | `PASS` 148 of 148 |

The first 6 November FPGA log covers the second MCU run (31 passes). It starts at the `LA`
table, so it has no boot lines or burst 1, though its totals include them. They end at
UART 31, SPI ok 248, bad 0, quiet-resets 0, I2C acked 62, read-nack 31, read-ack 0,
watchdog 0, framing 0, with every burst 14 events and `lost 14` for the pass missed
while the table printed. The `peer firmware v3` line is not in the file, but MISO idles
high and answers with the complement, which only v3 does.

What that run shows:

- **The wires carry a correct pass.** Decoding its `LA` table: UART `01` each way; eight
  SPI frames, MOSI `55 AA 12 34 DE AD BE EF` and MISO `AA 55 ED CB 21 52 41 10`, with
  MISO steady at least 2.3 us before every SCK rise; I2C `START, A0 ACK, 00 ACK, repeated
  START, A1 ACK, A5 NACK, STOP`.
- **The I2C failure is the MCU's test, not the bus.** The FPGA sent `0xA5` on the very
  pass the MCU marks as failed. The manual (SLAU846, "Bus Status Flags") has BUSY set
  only after the repeated START, about 12 us after the MCU starts the read, and the test
  checked BUSY after 1 us. So it looked in the FIFO about 190 us too early, found nothing
  on pass 1, and left that byte for pass 2 to read, and so on: every `PASS` was the byte
  of the pass before. `read MSR=00000040 BUS-BUSY` on all 39 passes is the sign, as a
  read that has ended with its STOP shows `IDLE`. The rerun bears this out: with the
  test waiting for `IDLE`, pass 1 passes and `stale=` stays 0.
- **The UART echo is real.** The MCU starts SPI 6 us after the middle of the echo's stop
  bit, and 82 us after that of its own byte, so it did wait for the FPGA's byte and not
  its own TX picked up on RX. `spins` is 565 to 567 on every pass.
- **Starting the FPGA first cured the UART, most likely.** The earlier failure never
  cleared once the MCU had started first, and it is gone with no MCU code change. The
  FPGA's firmware changed between those runs too, though only in SPI. Why that start
  stuck the UART is not known.
- **The floating-pin state is real.** The FPGA reads `SCK=1 CS=0 MOSI=1` until the MCU's
  first SPI frame after a reset, and then `SCK=0 CS=1 MOSI=0` between frames. SCK high
  with CS low is the state in which v2 counted false bytes; v3 counts none (bad 0,
  quiet-resets 0).

What the rerun shows: both log files start at pass and burst 33, so they have no banner,
version line or `LA` table, but the totals in them count from the start.

- **MCU:** the last totals are 148 passes each for UART, SPI and I2C, with no failures or
  timeouts, so pass 1 passed too. All 116 passes in the file show `rx=A5`, `stale=0`,
  `stray=0` and `read MSR=00000020 IDLE`.
- **FPGA:** the last totals are UART 148, SPI ok 1184, bad 0, quiet-resets 0, I2C acked
  296, ptr 148, read-nack 148, read-ack 0, watchdog 0, framing 0, and `lost 14` for the
  pass missed while the `LA` table printed. All 116 bursts in the file have 14 events,
  and `SPI ok` = 8 x `UART` and `I2C acked` = 2 x `UART` hold on every totals line. The
  last burst is the MCU's pass 149, one past the end of the MCU's file.

## What changed on 6 November

MCU code only, so the FPGA needs no rebuild.

| Where | Change | Why |
| --- | --- | --- |
| MCU, I2C | The read waits for `IDLE` instead of BUSY, then takes the byte. The FIFO is emptied at the start of each pass, and the log counts what was there as `stale=` | See above: the old wait ended at once, so the verdict was always a pass late |
| MCU, UART | Each pass starts by reading any byte left in the receive register | A full register sets no new interrupt, so a byte left there could block every later one. This should clear whatever the MCU-first start left behind |

## What changed on 5 November

| Where | Change | Why |
| --- | --- | --- |
| FPGA firmware v3, SPI | The bit loop now also checks whether its wait for SCK low timed out | With SCK stuck high and CS low, the wait for low gave up unnoticed and the wait for high ended at once, so a bit was counted every 5.5 ms and a byte reported every 44 ms, with no clock from anyone. PMOD JA has no pulls, so an MCU not driving its pins can look like this. 60 bytes is about 2.6 s of it. In simulation, MOSI following its neighbour MISO made these bytes read ok; a fixed MOSI made them bad |
| FPGA firmware v3 and MCU, SPI | The FPGA answers with the complement of each MOSI byte, instead of the same byte | With the same bytes both ways, a wire only picking up its neighbour read as correct. Now every bit differs, so a MOSI following MISO fails every byte at the FPGA, and a MISO following MOSI fails at the MCU |

The extra check takes the SPI limit from 375 kHz to 333 kHz. The MCU's 250 kHz is not
affected.

## What was wrong on 4 November

The board showed UART echoes, `FPGA SPI mismatch detected.` on every pass and
nothing about I2C. The SPI and I2C failures do not need a wiring fault to explain
them: the first three rows below fail in simulation with no board involved, and
`test_mcu_protocol_target`, the test added with this firmware, was already failing
on the I2C read.

| Where | Bug | What it did |
| --- | --- | --- |
| FPGA firmware, SPI | The bit loop waited for SCK high but never for SCK low, so it shifted several bits per clock | MISO was wrong and "mismatch" was raised at every SCK speed tried, 100 kHz to 1 MHz |
| FPGA firmware, I2C | SCL fall vs. START/STOP was told apart by the sign of a difference, which only works if SCL is the higher pin (as in `i2c_target.s`); here SDA is | A repeated START was never seen, so the read was never answered; with a short SDA hold, SDA rising as SCL fell looked like a STOP, so even the address got no ACK |
| 1 MHz SPI itself | With four threads running, a thread gets one instruction per 4 core cycles, so 1 MHz leaves 3 instructions a bit at a 12 MHz core; a correct bit loop needs 9 | It could never have worked; the MCU now runs at 250 kHz (the limit is 333 kHz) |
| MCU, I2C | `DL_I2C_CONTROLLER_ACK_ENABLE` on the read: the comments in TI's `dl_i2c.h` are the wrong way round (`hw_i2c.h` is right), and it ACKs the last byte | The target sends another byte and can hold SDA low through the STOP, hanging the bus |
| MCU, I2C | No wait after starting a transfer (TI errata I2C_ERR_13: BUSY is not set at once) | "Finished" could be read straight away, and the read started on top of the write |
| MCU, SPI | The test stopped at the first wrong byte | The MCU and the target went out of step for all later passes |
| MCU, main loop | `__WFI()` after one pass | Later passes only ran when a stray UART byte woke it, which is likely why the counters never made sense |
| A53 monitor | `uo_out` carried the UART byte and an error flag in bit 7, polled every millisecond or slower | The SPI flag hid most UART bytes before they were seen (hence `0x01`, `0x03`), and a byte `>= 0x80` would have read as an SPI mismatch |

The wrong-bit check in the old test (`bench.uo[-1] & 0x01` for a flag in bit 7) and
its SPI timing, far from what the MCU does, are also gone: `test_targets.py` now
uses the MSPM0's timing from its generated configuration.

## Running it

No new bitstream is needed: the A53 loads the firmware at run time. After 6 November
only the MCU needs rebuilding, if the FPGA's app is already on firmware v3.

1. Turn on logging in PuTTY for both consoles before anything starts, as a long run soon
   outgrows the scrollback: Session, Logging, *All session output*, with a file name.
2. FPGA: on the Vitis machine, `vitis -s fpga/ps/mcu_test/build_vitis.py`, then
   program the same bitstream and launch the ELF as before. Open PuTTY on the Zynq
   console (COM3) first. Load the FPGA before the MCU starts.
3. MCU: rebuild `mcu_ext/protocol_tests` in CCS and flash it.
4. Open a second terminal on the LaunchPad's **XDS110 Application/User UART** port
   (Device Manager shows both XDS110 ports), 115200 8N1. J12 and J13 must be in their
   default left-and-centre position and RXD and TXD fitted on J101, as they are from
   the factory. Then reset the MCU, so its first pass is the one the FPGA's logic
   analyser records.
5. Let it run for at least 5 passes (one a second).
6. Optional: with the FPGA left running, hold the LaunchPad's RESET button (S3) for about
   3 s and let go. This is the MCU's reset, not PB0 on the FPGA board (see [Resets](#resets)).
   The MCU starts again from pass 1. With firmware v3 this should add no SPI bytes: at the
   FPGA, `SPI ok` + `bad` stays 8 x `UART` and `quiet-resets` stays 0.
7. Optional: to test the UART's recovery, start the MCU with the FPGA not loaded, then load the
   FPGA. UART should pass from the first pass after that, perhaps with `stray=` above 0
   once. A UART that keeps timing out would mean the 6 November change did not cure it.

### What to send back

Both log files, by path if whoever reads them can reach them. Pasted into a message, a
log can be cut off (50,000 characters is about 60 FPGA bursts), so otherwise send:

- **MCU console**: the `MCU ====` banner, the last pass and its `MCU totals` line. The
  totals are cumulative, so any failure shows there.
- **FPGA console**: the last `FPGA totals` line, and everything except normal bursts,
  which includes the `LA` table (replace `fpga.log` with the log's name):

```powershell
Select-String -Path fpga.log -Pattern 'Loading|peer firmware|MISMATCH|went quiet|WARNING|WATCHDOG|not ours|hint|logic analyser|^LA |burst \d+: (?!14 events)'
```

Each log starts with its build time, and the FPGA's says `peer firmware v3`, which show
that the new code is what ran. If the MCU log is garbage at 115200 and readable at 9600,
SysConfig did not regenerate. It also prints the SPI rate read back from the hardware:
it should say 250 kHz.

### Reading them

```
MCU UART tx=01 rx=01 spins=... stray=0 -> PASS
MCU SPI  tx=    55 AA 12 34 DE AD BE EF
MCU SPI  expect=AA 55 ED CB 21 52 41 10
MCU SPI  rx=    AA 55 ED CB 21 52 41 10 stale=0 -> PASS
MCU I2C  before MSR=... IDLE
MCU I2C  write  MSR=... BUS-BUSY          (no STOP yet)
MCU I2C  read   MSR=... IDLE
MCU I2C  expect=A5 rx=A5 stale=0 -> PASS

FPGA peer firmware v3 is running
FPGA ---- burst 1: 14 events ----
FPGA UART got 0x01, echoed it
FPGA SPI  byte 0: MOSI 0x55 ok
FPGA I2C  address byte 0xA0 (write 0x50): ACK
FPGA I2C  pointer 0x00: ACK
FPGA I2C  repeated START
FPGA I2C  address byte 0xA1 (read 0x50): ACK
FPGA I2C  sent 0xA5, controller NACK (the normal last byte)
FPGA totals: UART 1 | SPI ok 8 bad 0 quiet-resets 0 | I2C acked 2 other 0 ptr 1 wr 0 read-nack 1 read-ack 0 | watchdog 0 lost 0 framing 0
FPGA pins RX=1 TX=1 SCL=1 SDA=1 SCK=0 CS=1 MOSI=0 MISO=1 moved: RX TX SCL SDA SCK CS MOSI MISO
```

That is the shape of a good pass. The lines are as the board printed them on 6 November,
except for `peer firmware v3`, which came before those log files start.

A normal burst is 14 events: 1 UART, 8 SPI and 5 I2C. The totals should keep these
sums: `SPI ok` + `bad` = 8 x `UART`, and `I2C acked` = 2 x `UART`. One pass is lost
while the `LA` table prints, so after that the UART byte is the burst number + 1, modulo
256.

`moved:` also lists TX and MISO, but those are the FPGA's own outputs, so it says
nothing about those wires reaching the MCU.

The two sides together say where a fault is:

| Seen | Points at |
| --- | --- |
| A pin missing from `moved:` | That wire, or the MCU pin behind it |
| `SCL=0` or `SDA=0` with `moved: none`, and a hint | J16 or J17 removed, a bad wire, or a controller stopped mid-transfer |
| MCU `FRAMING-ERROR` or wrong bytes, FPGA got the right ones | FPGA TX to MCU RX: PA9, ground |
| FPGA `MOSI ... MISMATCH` | MOSI or SCK wiring or noise; compare with the `LA` table |
| Every FPGA byte `MISMATCH`, each MOSI the complement of the one expected; `MOSI=1` at idle | MOSI is only picking up MISO: the MOSI wire, or the MCU pin behind it |
| MCU `rx=` wrong, FPGA says MOSI ok | MISO wire, or MISO timing; compare with the `LA` table |
| MCU `rx=` the same as `tx=` | The FPGA is on old firmware (no `v3` line), or MISO is only picking up MOSI: the MISO wire |
| `SPI ok` + `bad` above 8 x `UART`, or a burst well over 14 events | SPI bytes no pass sent. With v3 that means SCK really toggles while the MCU is not driving it (its reset, say), which would take pulls on the JA pins in the XDC and a new bitstream |
| MCU I2C `rx=--` or `stale=` above 0, FPGA says `sent 0xA5` | The byte was on the wire and the MCU did not take it in time: its test, not the bus. It should not happen since 6 November |
| MCU UART `stray=` above 0 once, then 0 | Bytes came in with no test waiting, as when the MCU starts before the FPGA. Harmless |
| MCU UART `TIMEOUT` and `OVERRUN` on every pass, FPGA says `UART got` | Echoes reach the MCU and its interrupt does not take them, as on 5 November. It should not happen since 6 November |
| MCU `ADDRESS-NOT-ACKED`, no FPGA `address byte` line | SCL or SDA swapped or not connected, or MCU address wrong |
| FPGA `controller ACK`, then `WATCHDOG` | The MCU read is ACKing its last byte again |
| FPGA `WARNING: missed N events` once, right after the `LA` table | Expected: the table takes over a second to print (14 events, one pass, on 5 November) |
| FPGA `WARNING: nothing from the peer: uo=0x01 uio_out=0x01 uio_oe=0x01`, and the MCU's tests fail | The core is in its boot ROM: PB0 was pressed with SW0 down. See [Resets](#resets) |

The `LA` table is an on-chip logic analyser: one line per change on the eight pins
as the FPGA sees them, starting just before the first UART start bit, 333 ns
resolution. It takes no bench equipment, so timing questions can be answered from
the copied text. For one, a driven MOSI changes near SCK's falling edges, and MISO just
after its rising edges.

## Resets

The core has one reset, `rst_n`, with three sources in `fpga/rtl/aup_zu3_io.v`: the PS's
`pl_resetn0`, `RUN` (EMIO bit 9) and PB0. PB0 appears twice there because it asserts at
once and releases through a synchroniser. Every `if (!rst_n)` in the core's modules is
that same net. It restarts the logic but does not clear the SRAM:
`fpga/rtl/tt_um_msf_sram.v` does not use it. What follows depends on who picks the core
up:

| Reset | Resets | Then |
| --- | --- | --- |
| LaunchPad RESET (S3), or CCS | The MCU only | The MCU starts again from pass 1; the core keeps running and its totals keep counting |
| Launching the app again from Vitis | The app, and the core through `RUN` | The A53 takes the pins, writes the firmware again and starts it; the totals start from 0 |
| PB0 | The core only | Nothing reloads it and the app just keeps watching. `ui_in` comes from the switches, so SW0 picks what runs |

**PB0 with SW0 down** starts the boot ROM's loader, which waits for a frame. That is
`uo=0x01`, and `uio_out=0x01 uio_oe=0x01` is its UART TX idling on UIO[0]. After a few
seconds the app prints `WARNING: nothing from the peer`, and the MCU's tests fail, as
nothing is driving the echo, MISO or the I2C ACK. It is not a fault in the core, and the
board showed exactly this on 6 November.

Do not leave the boards in that state. UIO[0] is also the MCU's TX wire, so the FPGA
holds it high against each low bit the MCU sends, up to about 78 us a pass. Whether
either board limits that current is not known to us: the XDC sets no drive strength, and
the AUP-ZU3's PMOD circuit has not been checked. Launching the app again ends it, as the
A53 takes the pins and JA goes high-Z. The cause is that the peer's UART pins are the
reverse of the boot ROM's (RX on UIO[0] and TX on UIO[1], against TX on UIO[0] and RX on
UIO[1]).

**PB0 with only SW0 up** restarts the peer from the SRAM. Keep SW2 down, as high during
reset selects the debug view. The peer does not read `ui_in`, so the switches do not
matter once it runs. The console then prints the `peer firmware v3` line and the `LA`
table again, loses a pass to the table, and prints one false `missed N events`, because
the peer's event count starts again. `lost` means nothing after a press, though `SPI ok`
= 8 x `UART` still holds.

Both cases were checked in a throwaway simulation of `aup_zu3_io` with the peer and a
model of the MCU. With SW0 down the A53's reads are those same three values, no firmware
word changes and UART, SPI and I2C all fail; with SW0 up all three pass again. The false
`missed` line comes from replaying that run through the monitor. The SW0-up restart has
not been tried on the board.

## Status

**On the board, 6 November:** firmware v3 and the MCU code with its I2C and UART changes,
FPGA started first. UART, SPI and I2C pass both ways on all 148 passes at the MCU, from
pass 1, with 0 bad and `SPI ok` = 8 x `UART` at the FPGA.

**Not run, and not needed:** steps 6 and 7. Step 6 would only stretch what has already
been seen: the first 6 November `LA` table caught the floating pins with v3 counting
nothing, and the rerun's count is exact from pass 1 with the FPGA up first. PB0 is a
different reset and does not test this. Step 7 only matters if the MCU starts first, and
the steps above start the FPGA first. Neither result could change the chip's design.

Checked in simulation (RTL of both `src/` and `fpga/rtl`): one whole pass with the
MSPM0's timing, SPI and I2C limits, I2C SDA holds 1-60 cycles against 16 clock
phases, floating pins, and a MOSI that only follows MISO. The monitor's C code was run
on the PC on a real `uo_out` trace from that simulation. MSPM0 timing is modelled from
its configuration and TI's documents, so its real waveforms may still differ. That is
what the `LA` table is for.

Before the rerun, the MCU's 6 November changes ran on the PC against a stand-in for its
UART, SPI and I2C, written from TI's manual and the board's timing. On it the old I2C code
prints the board's lines, with `rx=--` on pass 1 and `BUS-BUSY` throughout, and the new
code passes from pass 1 and also handles an address NACK, a read that never ends and a
stale byte. Those error cases and a stuck UART, set up by hand, have only been shown on
the stand-in.

Vivado reports timing met at 12 MHz inside the FPGA, though it does not time the pins
([`docs/fpga.md`](docs/fpga.md#vivado-results)).

Still open: why starting the MCU first stuck the UART is not known, and neither TI's manual
nor its errata describe it. Step 7 tests the cure, not the cause.

PA0 (SDA) also drives the LaunchPad's red LED through J18, so that LED may flicker
with I2C traffic. It should be harmless; if SDA looks wrong in the table, try with J18
removed.

## The v3 board test

For branch `v3`. The FPGA runs [`firmware/mcu_v3_peer.s`](firmware/mcu_v3_peer.s),
loaded by [`fpga/ps/mcu_v3`](fpga/ps/mcu_v3/README.md). The MCU runs the same
`mcu_ext/protocol_tests` build as above: it starts with v2 protocol passes and switches
to v3 passes for good once the peer asks for calibration (two `S` bytes in a row on
UART1, which the v2 peer never sends). The MCU is the judge: it knows what it sent.

| v3 part | What the MCU does, each pass | A pass means |
|---|---|---|
| RX mode 3 (phase-tracking Manchester) | Sends five Ethernet frames: 64 bytes at 0, +100 and -100 ppm, 1518 bytes at -100 ppm, and 64 bytes with one bit flipped | The four good frames come back `81` with their length; the flipped one `82` |
| NRZI receiver, de-stuffing, resync | Sends four USB DATA1 packets (36 bytes, with a run of ones to stuff): 0, +2500 and -2500 ppm (USB allows 0.25 %), and one with a bit flipped | `81`, 36 bytes, SYNC `80`, PID `4B`; the flipped one `82` |
| Late guard | In each of the peer's four states, reads POCI (UIO7) with its pull-up, then its pull-down, and reads SCL (UIO2) | Driven low reads 0 both ways. Off the bus follows the pull. Only state B, where T0 is late and named, takes UIO7 off; UIO2 stays driven throughout |
| The pipeline | Everything above runs on it | |

**How the line signals are made.** The MCU's SPI draws them on MOSI (UIO6): 16 Mbit/s,
16-bit words fed by the CPU with interrupts off. First the MCU sends a known pattern
of 513 transitions, 32 SPI bits apart. The FPGA times it and returns T, the core
cycles it took. One SPI bit is then T / 16384 core cycles, so the MCU can put every
symbol where the FPGA's clock wants it, whatever its own oscillator does (the
LaunchPad runs on SYSOSC, good to a few per cent). Each edge lands on the nearest SPI
bit: up to 31 ns from where it belongs, at random. SCLK and CS are taken off their
pins, so no 16 MHz clock runs beside MOSI.

**What it shows, and what it does not.** The FPGA clocks the core at 12 MHz, so the
frames run at 2.4 Mbit/s and the packets at 3 Mbit/s. That is 5 and 4 core cycles a
bit, the same as 10BASE-T at 50 MHz and USB full speed at 48 MHz. So the core does
cycle for cycle what the chip would, against a peer with its own clock. But these
are not 10 and 12 Mbit/s: the chip's speed comes from the sign-off STA, not from
this test. The edge spread, up to 31 ns at 12 MHz, scales to 7.5 ns at 50 MHz;
`test_eth10_rx` covers 10 ns at 50 MHz in simulation.

### Running it

1. Program the v3 bitstream: `fpga/rtl` from branch `v3`, built as before
   (`fpga/vivado/build.tcl`). The board wrapper and block design are unchanged.
2. On the Vitis machine: `vitis -s fpga/ps/mcu_v3/build_vitis.py`, then launch
   `mcu_v3.elf` with PuTTY logging on the Zynq console. It prints `Loading the v3
   board peer (274 words)`.
3. In CCS, rebuild `mcu_ext/protocol_tests` and flash it. The project has two new
   files, `v3_lines.c` and `v3_test.c`, so let CCS rebuild its makefiles. Log the
   XDS110 Application/User UART at 115200 8N1, as before.
4. Reset the MCU. After about a second it prints `MCU ==== v3 board test`, then a pass
   every second or so. Let it run at least 20 passes.

The wiring is the same as for the v2 test, J16 and J17 included. J18 can stay: the
test reads SCL, not SDA, because SDA's LED could hold a released line low.

### What to send back

The MCU's log: its banner, the last pass and the `MCU v3 totals` line, or the whole
file. The FPGA's log: the last pass summary and its `FPGA totals` line.

### Reading them

A good pass at the MCU. Each `stream` figure is the CPU cycles the SPI took over the
cycles its words should take:

```
MCU ---- v3 pass 1 ----
MCU cal  T=12288 (MCU clock 32000 kHz against the FPGA's 12 MHz) stream 33088/33088 cycles -> PASS
MCU eth  0: 64 bytes +0 ppm: want 81, got 81 length 64 stream .../... cycles -> PASS
MCU eth  4: 64 bytes +0 ppm, a bit flipped: want 82, got 82 length 64 ... -> PASS
MCU usb  0: +0 ppm: want 81, got 81 bytes 36 SYNC 80 PID 4B ... -> PASS
MCU guard B T0 late, guarded:    want up=1 down=0 SCL=0 LATE=8011, got up=1 down=0 SCL=0 LATE=8011 -> PASS
MCU v3 totals: cal pass=1 fail=0 timeout=0 invalid=0 | eth pass=5 ... | usb pass=4 ... | guard pass=4 ...
```

`T` and the MCU clock depend on the LaunchPad's SYSOSC: anything from about 31.4 to
32.6 MHz is normal.

| Seen | Points at |
|---|---|
| No v3 banner; v2 passes that time out | The FPGA is not running `mcu_v3`, or the UIO1-PA9 wire |
| `NOT VALID (the stream had gaps)` | The SPI paused between words, or the CPU fell behind: that stream's result says nothing about the FPGA. Every line on every pass means the MCU's SPI cannot stream gapless, and the line tests need another generator |
| `cal ... FAIL` with T well away from 12288, or T=0 | The FPGA did not see the pattern's transitions: the MOSI wire (PB17-UIO6) or ground |
| `TIMEOUT` on eth or usb | No verdict came back: UIO1-PA9 |
| Status `84` | The FPGA saw no frame or packet, or not all of one: MOSI |
| Status `83` | No SFD where one should be: the frame's start was damaged |
| `82` on good frames or packets | Bits damaged on the way in. If it follows the ppm (only +100, say), the receiver's tracking. If it comes and goes, the wire |
| guard A or C or D `up=1` | UIO7 was not driven when it should have been: the peer, or the PB19 wire |
| guard B `up=0` | UIO7 stayed driven while T0 was late and guarded: the guard did not act. Is the bitstream v3? |
| guard D `up=1` | The guard took UIO7 for a thread it does not name (F15's planted bug, on the board) |
| `SCL=1` | UIO2 was not driven: the guard took a pin it does not name, or the PA1 wire |
| `LATE=` other than shown | Read the FPGA's log: it prints the same `LATE_RD` value |

The FPGA's summary repeats what it sent the MCU. Where the two logs disagree on a
byte, the fault is on that wire.

### Checked before the board

- `test/test_mcu_v3_peer.py`: the peer on the RTL against the MCU's side. It builds
  `v3_lines.c` on the PC and plays the MCU's exact SPI words into MOSI, from an MCU
  clock 1.3 % fast and then 1.7 % slow. It decodes the UART as the MCU does, and
  reads the guard pins with the MCU's pulls. Every verdict must match.
- `mcu_ext/tests/run_compile_test.sh`: the MCU's v3 code compiles with every warning on.
  Every DriverLib call and pin name it uses was checked against TI's MSPM0 SDK (the
  headers and the SysConfig templates that name the pins, 9 Nov 2026).
- `fpga/tests/run_mcu_monitor_test.py` and `run_ps_loader_compile_test.sh`: the A53 app's
  summary on a pass's bytes, and its compile against the Vitis stubs.

Not checked before the board: whether the MSPM0's SPI really sends its words back to
back. TI's forum has reports of gaps, so every stream is timed and a stream with gaps
is reported as not valid. Also unchecked: the real MCU's pin set-up, beyond what the
compiler and the SDK's headers show.

## Relevant files

- `firmware/mcu_protocol_target.s`: the FPGA peer; its header documents the limits
  and the event format.
- `firmware/mcu_v3_peer.s`, `fpga/ps/mcu_v3/`, `mcu_ext/protocol_tests/v3_test.c` and
  `v3_lines.c`, `test/test_mcu_v3_peer.py`: the v3 board test.
- `fpga/ps/mcu_test/src/main.c`: A53 loader and event monitor.
- `mcu_ext/protocol_tests/`: MCU tests and serial log; `p_test.syscfg` for pins and
  rates; `spi_test.c` for the SPI bytes, which `test_mcu_peer_tables_match_the_mcu_code`
  compares with the firmware's.
- `test/test_targets.py` (`test_mcu_peer_*`) and
  `fpga/tests/run_mcu_monitor_test.py`: the tests.
