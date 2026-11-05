## MCU protocol tests

This project runs the UART, SPI and I2C controller tests against the FPGA protocol
target, one pass a second. UART1 is the test UART. UART0 is the serial log.

How to run the whole test, what to send back and how to read the logs are in
[`external-mcu_testing.md`](../../external-mcu_testing.md).

## FPGA PMOD JA wiring

| MCU signal | MCU pin | FPGA signal |
| --- | --- | --- |
| UART1 TX | PA8 | UIO[0] (FPGA RX) |
| UART1 RX | PA9 | UIO[1] (FPGA TX) |
| I2C0 SCL | PA1 | UIO[2] |
| I2C0 SDA | PA0 | UIO[3] |
| SPI0 SCLK | PB18 | UIO[4] |
| SPI0 CS0 | PC7 | UIO[5] |
| SPI0 PICO/MOSI | PB17 | UIO[6] |
| SPI0 POCI/MISO | PB19 | UIO[7] |

Connect the grounds. The LaunchPad pulls up PA0 and PA1 itself: **keep J16 and J17 on
1-2, the default, which is 3.3 V. Position 2-3 is 5 V, which must never reach the
FPGA's 3.3 V PMOD pins** (TI SLAU947, Table 2-1). PA0 also drives the red LED through
J18.

## Serial log

UART0 (PA10 TX, PA11 RX) is the LaunchPad's back-channel: with J12 and J13 left and
centre and RXD and TXD fitted on J101 (all the default), it is the **XDS110
Application/User UART** COM port on the PC. Open it at 115200 8N1. Each pass prints what was sent and received on each
protocol, the I2C controller's status register at each step with its flags named,
and running totals. The same counters stay in `protocolResults[]` for the debugger.

The SPI test sends `55 AA 12 34 DE AD BE EF` and expects the complement of each byte
back, `AA 55 ED CB 21 52 41 10`, so MISO can not pass by picking up MOSI. A `rx=` line
equal to `tx=` means the FPGA is on old firmware or MISO is doing just that.

A pass runs all three tests before it logs anything, so that its traffic on the wires
is one short burst. `SPI_FRAME_GAP_CYCLES` in `spi_test.c` and the rates in
`p_test.syscfg` follow the FPGA target's limits (`firmware/mcu_protocol_target.s`).

The CCS build regenerates the `Debug/` files from `p_test.syscfg`. To add a source
file, let CCS rebuild its makefiles; a plain `gmake` in `Debug/` will not see it.
