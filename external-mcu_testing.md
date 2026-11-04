# External MCU Protocol Testing Findings

## Purpose and expected traffic

The test setup connects an MSPM0 board to the FPGA through PMOD JA and uses
the FPGA protocol target to exercise UART, I2C, and SPI. CAN is out of scope.

| Protocol | FPGA UIO | Expected behavior |
| --- | --- | --- |
| UART | UIO[0] RX, UIO[1] TX | UART1 at 115200 baud, 8-N-1; incrementing bytes are echoed |
| I2C | UIO[2] SCL, UIO[3] SDA | FPGA target address `0x50`; register read at `0x00` returns `0xA5` with repeated START |
| SPI | UIO[4] SCLK, UIO[5] CS, UIO[6] MOSI, UIO[7] MISO | Mode 0, MSB first, 1 MHz; echo `55 AA 12 34 DE AD BE EF` |

The MSPM0 project uses UART1 (not UART0) on PA8 TX / PA9 RX, I2C0 on PA1
SCL / PA0 SDA, and SPI0 on PB18 SCLK / PC7 CS / PB17 PICO(MOSI) / PB19
POCI(MISO). The I2C bus requires pull-ups and the boards must share ground.
The user clarified that a prior failure was caused by incorrectly connected
wires after changing jumper type; it was not a pin-mapping change.

## Implementation and build status

- The MSPM0 firmware runs UART, I2C, and SPI tests and records protocol
  counters in `protocolResults[]`; it does not print those results to PuTTY.
- The I2C test uses the controller's advanced transfer sequence to generate
  the register-address write followed by a repeated-START read, and NACKs the
  final byte.
- SysConfig and the MSPM0 firmware build were reported successful. XDS110
  programming and image verification succeeded; a later flash-and-run also
  verified the image and reported `Running... Success`.
- The Vitis A53 monitor polls FPGA UIO output and reports UART bytes to the
  Zynq serial console (PuTTY on COM3). The FPGA peer firmware is loaded by the
  A53 monitor app. The monitor only prints nonzero changed UART values; it
  reserves UIO output bit 7 for an SPI mismatch indication.

## Test evidence and reruns

- An earlier run passed UART intermittently, and PuTTY showed FPGA echoes
  including `0x01` and `0x03`. SPI and I2C failed in that run.
- Later runs were inconsistent: one memory snapshot showed UART 0 pass / 1
  fail, SPI 0 pass / 1 fail, and I2C 0 pass / 1 fail. UART state included RX
  `0x00`, ready `1`, expected `0x02`.
- After the user corrected the physical wiring, the MSPM0 image was flashed
  and launched again. The subsequent XDS110 SRAM read showed:

  | Test | Pass | Fail | Timeout | Total |
  | --- | ---: | ---: | ---: | ---: |
  | UART | 0 | 5 | 4 | 9 |
  | SPI | 0 | 9 | 0 | 9 |
  | I2C | 0 | 9 | 0 | 9 |

  These are cumulative firmware counters from the memory snapshot, not a
  clean per-run baseline.

## Limits and diagnosis

The latest counters do not prove the corrected wiring or MSPM0 firmware is
the cause. During attempts to restore the FPGA monitor, the A53 FSBL failed
to reach its expected breakpoint; PS initialization stalled, and XSCT later
reported that Cortex-A53 #0 could not resume (`EDITR not ready`). Therefore,
the FPGA peer was not confirmed running during the final MCU rerun. With the
peer unavailable or unverified, protocol failures are inconclusive.

The physical board is an LP-MSPM0G3519, matching the CCS target configuration
and generated SysConfig. Earlier PuTTY capture attempts also captured the VS
Code window rather than the serial console, so there is no verified console
capture for the latest rerun.

## Recommended next steps

1. Recover or power-cycle the FPGA board and verify the bitstream and A53
   monitor app are running. Confirm the PuTTY startup banner before testing.
2. Clear or reset the MCU test counters, run the protocol suite once, and
   read back counters immediately.
3. Correlate that single run with FPGA UART output and protocol-side evidence;
   investigate UART, SPI, and I2C separately.
4. No MCU device/board target mismatch remains; the LP-MSPM0G3519 matches the
   configured MSPM0G3519 target.

## Relevant project files

- `mcu_ext/protocol_tests/p_test.syscfg` — MCU peripheral and pin setup.
- `mcu_ext/protocol_tests/uart_test.c` — UART1 echo test.
- `mcu_ext/protocol_tests/i2c_test.c` — repeated-START register read test.
- `mcu_ext/protocol_tests/spi_test.c` — SPI echo test.
- `mcu_ext/protocol_tests/protocol_test.c` — per-protocol counters.
- `firmware/mcu_protocol_target.s` — FPGA UART/I2C/SPI protocol peer.
- `fpga/ps/mcu_test/src/main.c` — A53 peer loader and UART monitor.
