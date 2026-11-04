# External MCU protocol testing

An LP-MSPM0G3519 is the controller and the FPGA core the target, over PMOD JA.
CAN is out of scope.

| Protocol | FPGA UIO | MCU pins | Expected behaviour |
| --- | --- | --- | --- |
| UART | UIO[0] RX, UIO[1] TX | UART1: PA8 TX, PA9 RX | 115200 8N1; each byte comes back |
| I2C | UIO[2] SCL, UIO[3] SDA | I2C0: PA1 SCL, PA0 SDA | Address `0x50`; register `0x00` reads `0xA5`, using a repeated START |
| SPI | UIO[4] SCLK, UIO[5] CS, UIO[6] MOSI, UIO[7] MISO | SPI0: PB18, PC7, PB17, PB19 | Mode 0, MSB first, 250 kHz; `55 AA 12 34 DE AD BE EF` both ways, one byte per CS frame |

The boards must share a ground. SCL and SDA need pull-ups to 3.3 V, and the LaunchPad
has its own on PA0 and PA1: **J16 and J17 must stay on 1-2, the default, which is
3.3 V. Position 2-3 is 5 V, which must never reach the FPGA's 3.3 V PMOD pins**
(TI SLAU947, Table 2-1). So external pull-ups are not needed, though they do no harm
if they go to 3.3 V.

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
| 1 MHz SPI itself | With four threads running, a thread gets one instruction per 4 core cycles, so 1 MHz leaves 3 instructions a bit at a 12 MHz core; a correct bit loop needs 8 | It could never have worked; the MCU now runs at 250 kHz (the limit is 375 kHz) |
| MCU, I2C | `DL_I2C_CONTROLLER_ACK_ENABLE` on the read: the comments in TI's `dl_i2c.h` are the wrong way round (`hw_i2c.h` is right), and it ACKs the last byte | The target sends another byte and can hold SDA low through the STOP, hanging the bus |
| MCU, I2C | No wait after starting a transfer (TI errata I2C_ERR_13: BUSY is not set at once) | "Finished" could be read straight away, and the read started on top of the write |
| MCU, SPI | The test stopped at the first wrong byte | The MCU and the target went out of step for all later passes |
| MCU, main loop | `__WFI()` after one pass | Later passes only ran when a stray UART byte woke it, which is likely why the counters never made sense |
| A53 monitor | `uo_out` carried the UART byte and an error flag in bit 7, polled every millisecond or slower | The SPI flag hid most UART bytes before they were seen (hence `0x01`, `0x03`), and a byte `>= 0x80` would have read as an SPI mismatch |

The wrong-bit check in the old test (`bench.uo[-1] & 0x01` for a flag in bit 7) and
its SPI timing, far from what the MCU does, are also gone: `test_targets.py` now
uses the MSPM0's timing from its generated configuration.

## Running it

No new bitstream is needed: the A53 loads the firmware at run time.

1. FPGA: on the Vitis machine, `vitis -s fpga/ps/mcu_test/build_vitis.py`, then
   program the same bitstream and launch the ELF as before. Open PuTTY on the Zynq
   console (COM3) first.
2. MCU: rebuild `mcu_ext/protocol_tests` in CCS (`p_test.syscfg` changed, so
   SysConfig regenerates the project) and flash it.
3. Open a second terminal on the LaunchPad's **XDS110 Application/User UART** port
   (Device Manager shows both XDS110 ports), 115200 8N1. J12 and J13 must be in their
   default left-and-centre position and RXD and TXD fitted on J101, as they are from
   the factory. Then reset the MCU, so its first pass is the one the FPGA's logic
   analyser records.
4. Let it run for at least 5 passes (one a second).

### What to send back

Both logs, whole (in PuTTY: window menu, *Copy All to Clipboard*):

- **FPGA console**: lines starting `FPGA` and the `LA` table, printed once per load.
- **MCU console**: lines starting `MCU`.

Each log starts with its build time, which shows that the new code is what ran. If
the MCU log is garbage at 115200 and readable at 9600, SysConfig did not
regenerate. It also prints the SPI rate read back from the hardware: it should say
250 kHz.

### Reading them

```
MCU UART tx=01 rx=01 spins=... stray=0 -> PASS
MCU SPI  tx=55 AA 12 34 DE AD BE EF
MCU SPI  rx=55 AA 12 34 DE AD BE EF stale=0 -> PASS
MCU I2C  before MSR=... IDLE
MCU I2C  write  MSR=... BUS-BUSY          (no STOP yet)
MCU I2C  read   MSR=... IDLE
MCU I2C  expect=A5 rx=A5 -> PASS

FPGA UART got 0x01, echoed it
FPGA SPI  byte 0: MOSI 0x55 ok
FPGA I2C  address byte 0xa0 (write 0x50): ACK
FPGA I2C  pointer 0x00: ACK
FPGA I2C  repeated START
FPGA I2C  address byte 0xa1 (read 0x50): ACK
FPGA I2C  sent 0xa5, controller NACK (the normal last byte)
FPGA pins RX=1 TX=1 SCL=1 SDA=1 SCK=0 CS=1 MOSI=0 MISO=0 moved: RX TX SCL SDA SCK CS MOSI MISO
```

That is the shape of a good pass; the FPGA lines are from the RTL simulation, the
MCU lines from the code. The two sides together say where a fault is:

| Seen | Points at |
| --- | --- |
| A pin missing from `moved:` | That wire, or the MCU pin behind it |
| `SCL=0` or `SDA=0` with `moved: none`, and a hint | J16 or J17 removed, a bad wire, or a controller stopped mid-transfer |
| MCU `FRAMING-ERROR` or wrong bytes, FPGA got the right ones | FPGA TX to MCU RX: PA9, ground |
| FPGA `MOSI ... MISMATCH` | MOSI or SCK wiring or noise; compare with the `LA` table |
| MCU `rx=` wrong, FPGA says MOSI ok | MISO wire, or MISO timing; compare with the `LA` table |
| MCU `ADDRESS-NOT-ACKED`, no FPGA `address byte` line | SCL or SDA swapped or not connected, or MCU address wrong |
| FPGA `controller ACK`, then `WATCHDOG` | The MCU read is ACKing its last byte again |
| FPGA `WARNING: missed N events` once, right after the `LA` table | Expected: the table takes over a second to print |

The `LA` table is an on-chip logic analyser: one line per change on the eight pins
as the FPGA sees them, starting just before the first UART start bit, 333 ns
resolution. It takes no bench equipment, so timing questions can be answered from
the copied text.

## Status

Checked in simulation (RTL of both `src/` and `fpga/rtl`): one whole pass with the
MSPM0's timing, SPI and I2C limits, I2C SDA holds 1-60 cycles against 16 clock
phases. The monitor's C code was run on the PC on a real `uo_out` trace from that
simulation. The MCU code was type-checked against the TI SDK's headers.

**None of this has run on the board yet**, and the MCU changes have not been through
TI's compiler. MSPM0 timing is modelled from its configuration and TI's documents,
so its real waveforms may still differ. That is what the `LA` table is for.

PA0 (SDA) also drives the LaunchPad's red LED through J18, so that LED may flicker
with I2C traffic. It should be harmless; if SDA looks wrong in the table, try with J18
removed.

## Relevant files

- `firmware/mcu_protocol_target.s`: the FPGA peer; its header documents the limits
  and the event format.
- `fpga/ps/mcu_test/src/main.c`: A53 loader and event monitor.
- `mcu_ext/protocol_tests/`: MCU tests and serial log; `p_test.syscfg` for pins and
  rates.
- `test/test_targets.py` (`test_mcu_peer_*`) and
  `fpga/tests/run_mcu_monitor_test.py`: the tests.
