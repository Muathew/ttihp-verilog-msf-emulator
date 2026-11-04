# MSPM0 protocol-test Vitis app

This standalone A53 app loads [`firmware/mcu_protocol_target.s`](../../../firmware/mcu_protocol_target.s)
into msfcore and releases PS ownership so PMOD JA connects to all eight UIO
pins. The core runs UART echo (115200 8N1), I2C target address `0x50` with
register `0x00 = 0xA5`, and the SPI mode-0 response vector used by the
MSPM0G3517 tests. CAN is not part of this test.

## Build and run

The Vitis 2024.1 workspace and exported platform are under `fpga/build/vitis`.
From a Vitis command prompt initialized with `settings64.bat`, run this at the
repository root. Close any Vitis IDE session that currently has this
dedicated workspace open before rebuilding:

```sh
vitis -s fpga/ps/mcu_test/build_vitis.py
```

The script regenerates the firmware image header, creates or updates the
`mcu_test` standalone application for `psu_cortexa53_0`, imports the app and
shared PS sources, and builds it. The app uses its own workspace so it does not contend with the main Vitis
workspace. The deployment ELF is
`fpga/build/vitis/workspace_mcu_test/mcu_test/build/mcu_test.elf`.

Program the matching FPGA bitstream, launch the ELF on the A53 in Vitis, then
run `mcu_ext/protocol_tests` on the MSPM0 board. The app prints a ready message
after it has loaded the target image and connected PMOD JA. It then polls the
MSF `uo_out` monitor register and prints each newly received UART byte to the
Zynq serial console, for example `FPGA UART RX/echo: 0x01`. The SPI target
reports a MOSI mismatch as `FPGA SPI mismatch detected.` The monitor channel
uses `uo_out` for UART bytes, so it displays test bytes `0x00`-`0x7f`; this
covers the current incrementing MSPM0 UART test sequence.

The generated image must fit the loader's 384-word frame limit. The current
firmware is 208 words. Connect the MSPM0 signals as follows:

| MSPM0 signal | PMOD JA / UIO |
|---|---|
| UART RX / TX | UIO0 / UIO1 |
| I2C SCL / SDA | UIO2 / UIO3 |
| SPI SCLK / CS | UIO4 / UIO5 |
| SPI PICO (MOSI) / POCI (MISO) | UIO6 / UIO7 |

I2C SCL/SDA require external pull-ups. The SPI test currently clocks one byte
per CS-low frame; the target returns and checks the corresponding byte in the
fixed eight-byte vector across frames. For fast first-bit setup MISO remains
driven between frames; this target is intended for the dedicated single-device
PMOD bus, not a shared SPI bus.
