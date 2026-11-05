# MCU protocol-test Vitis app

This standalone A53 app loads [`firmware/mcu_protocol_target.s`](../../../firmware/mcu_protocol_target.s)
into msfcore, connects all eight UIO pins to PMOD JA, and prints what the core
reports. The core runs free at 12 MHz: UART echo (115200 8N1), an I2C target at
address `0x50` with register `0x00 = 0xA5`, and an SPI mode-0 target that checks the
bytes sent by the LP-MSPM0G3519 tests in
[`mcu_ext/protocol_tests`](../../../mcu_ext/protocol_tests) and answers each with its
complement. CAN is not part of this test.

How to run the whole test, what to send back and how to read the logs are in
[`external-mcu_testing.md`](../../../external-mcu_testing.md).

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

Program the matching FPGA bitstream, launch the ELF on the A53 in Vitis, then run
`mcu_ext/protocol_tests` on the MCU. The firmware is loaded at run time, so changing
it needs a new ELF but no new bitstream.

The image must fit the loader's 384-word limit, which also keeps it below the
logic analyser's buffer at SRAM 512. The current firmware is 352 words. Connect the
MCU signals as follows:

| MCU signal | MCU pin | PMOD JA / UIO |
|---|---|---|
| UART1 TX / RX | PA8 / PA9 | UIO0 / UIO1 |
| I2C0 SCL / SDA | PA1 / PA0 | UIO2 / UIO3 |
| SPI0 SCLK / CS0 | PB18 / PC7 | UIO4 / UIO5 |
| SPI0 PICO (MOSI) / POCI (MISO) | PB17 / PB19 | UIO6 / UIO7 |

The boards need a common ground. SCL and SDA are pulled up by the LaunchPad: keep its
J16 and J17 on 1-2 (3.3 V), because 2-3 would put 5 V on the FPGA's 3.3 V pins. MISO
stays driven between frames, so this target is for the dedicated PMOD bus, not a
shared SPI bus.

## The monitor

The core has no way to wait for the A53 while the board has the pins, and a line on
the console takes milliseconds. So the firmware sends its events on `uo_out`, in a
format that shows every new value (its header documents it), and the app only
collects while traffic is arriving. At the first firmware heartbeat (about 0.2 s
apart) with no traffic since the one before, it prints the burst, then a line with the
pin levels and which pins moved. Heartbeats also say how many events were sent, so
any missed while printing are reported.

Limits of the target at a 12 MHz core, also in the firmware's header and checked by
`test_mcu_peer_spi_limits`: SCK period at least 36 cycles (333 kHz), and 10 us from a
frame's last rising SCK edge to the next frame's first.

## Tests

No board needed:

```sh
python test/run_tests.py -m test_targets -t mcu_peer_one_pass   # and the other test_mcu_peer_* tests
python fpga/tests/run_mcu_monitor_test.py                       # the monitor's C code, on the PC
sh fpga/tests/run_ps_loader_compile_test.sh                     # against the Vitis stubs
```
