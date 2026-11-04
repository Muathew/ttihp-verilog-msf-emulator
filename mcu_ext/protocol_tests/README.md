## MSPM0 protocol tests

This project runs the UART, SPI, and I2C controller tests against the FPGA
protocol target. UART0 is not used for the PMOD tests; UART1 is the test UART.

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

Connect grounds and use external pull-ups on the I2C lines. The test results
are recorded in `protocolResults[]` for debugger inspection; they are not
printed on the Zynq PuTTY console.
