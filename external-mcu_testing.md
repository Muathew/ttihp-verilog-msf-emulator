# External MCU protocol testing

An LP-MSPM0G3519 is the controller and the FPGA core the target, over PMOD JA. The
core runs free at 12 MHz, not stepped by the PS, so unlike the C board tests this one
runs it at speed against a real device. CAN is out of scope.

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
run has 98 events (14 for the pass, 37 ok, 2 each for the 23 bad, 1 quiet-reset).

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

No new bitstream is needed: the A53 loads the firmware at run time. Both sides need
rebuilding after the changes above.

1. Turn on logging in PuTTY for both consoles, as a long run soon outgrows the
   scrollback: Session, Logging, *All session output*, with a file name.
2. FPGA: on the Vitis machine, `vitis -s fpga/ps/mcu_test/build_vitis.py`, then
   program the same bitstream and launch the ELF as before. Open PuTTY on the Zynq
   console (COM3) first.
3. MCU: rebuild `mcu_ext/protocol_tests` in CCS and flash it.
4. Open a second terminal on the LaunchPad's **XDS110 Application/User UART** port
   (Device Manager shows both XDS110 ports), 115200 8N1. J12 and J13 must be in their
   default left-and-centre position and RXD and TXD fitted on J101, as they are from
   the factory. Then reset the MCU, so its first pass is the one the FPGA's logic
   analyser records.
5. Let it run for at least 5 passes (one a second).
6. Hold the MCU's reset button for about 3 s and let go. With firmware v3 this should
   add no SPI bytes.

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
MCU I2C  expect=A5 rx=A5 -> PASS

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

That is the shape of a good pass. The FPGA lines are as the board printed them on
5 November, apart from the firmware version and MISO's idle level, which v3 changes.
The MCU lines come from the code, as no MCU log has been read yet.

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
| MCU `ADDRESS-NOT-ACKED`, no FPGA `address byte` line | SCL or SDA swapped or not connected, or MCU address wrong |
| FPGA `controller ACK`, then `WATCHDOG` | The MCU read is ACKing its last byte again |
| FPGA `WARNING: missed N events` once, right after the `LA` table | Expected: the table takes over a second to print (14 events, one pass, on 5 November) |

The `LA` table is an on-chip logic analyser: one line per change on the eight pins
as the FPGA sees them, starting just before the first UART start bit, 333 ns
resolution. It takes no bench equipment, so timing questions can be answered from
the copied text. For one, a driven MOSI changes near SCK's falling edges, and MISO just
after its rising edges.

## Status

**On the board:** firmware v2 and the MCU code of 4 November, in the first run above.
The MCU's log and the start of the FPGA's have not been read.

**Not on the board yet:** firmware v3 and the MCU's new SPI check. Run again as above:
the aim is 0 bad and `SPI ok` = 8 x `UART` from the first burst, with the MCU's totals
showing no failures.

Checked in simulation (RTL of both `src/` and `fpga/rtl`): one whole pass with the
MSPM0's timing, SPI and I2C limits, I2C SDA holds 1-60 cycles against 16 clock
phases, floating pins, and a MOSI that only follows MISO. The monitor's C code was run
on the PC on a real `uo_out` trace from that simulation. The MCU's new SPI code ran on
the PC against a stand-in for the SPI peripheral, but has not been through TI's
compiler. MSPM0 timing is modelled from its configuration and TI's documents, so its
real waveforms may still differ. That is what the `LA` table is for.

Still open:

- The core's timing at 12 MHz on the ZU3 has no Vivado report on record yet
  ([`docs/fpga.md`](docs/fpga.md)).
- The UART test still echoes the same byte both ways, as an echo does, so a TX picked
  up on RX would also read as a pass at the MCU. The FPGA log does show it received
  each byte, and the MCU's `spins` count should show the longer wait of a real echo.

PA0 (SDA) also drives the LaunchPad's red LED through J18, so that LED may flicker
with I2C traffic. It should be harmless; if SDA looks wrong in the table, try with J18
removed.

## Relevant files

- `firmware/mcu_protocol_target.s`: the FPGA peer; its header documents the limits
  and the event format.
- `fpga/ps/mcu_test/src/main.c`: A53 loader and event monitor.
- `mcu_ext/protocol_tests/`: MCU tests and serial log; `p_test.syscfg` for pins and
  rates; `spi_test.c` for the SPI bytes, which `test_mcu_peer_tables_match_the_mcu_code`
  compares with the firmware's.
- `test/test_targets.py` (`test_mcu_peer_*`) and
  `fpga/tests/run_mcu_monitor_test.py`: the tests.
