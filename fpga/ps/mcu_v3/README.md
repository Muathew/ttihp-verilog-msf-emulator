# v3 board-test Vitis app

This standalone A53 app loads [`firmware/mcu_v3_peer.s`](../../../firmware/mcu_v3_peer.s)
into msfcore, gives PMOD JA to the core, and prints what the core tells the
LP-MSPM0G3519. The MCU runs [`mcu_ext/protocol_tests`](../../../mcu_ext/protocol_tests)
and is the judge. It notices the v3 peer by itself, so the same MCU build serves both
this and the v2 protocol test.

How to run it, what to send back and how to read the logs are in
[`external-mcu_testing.md`](../../../external-mcu_testing.md#the-v3-board-test).

## Build and run

As for [`mcu_test`](../mcu_test/README.md), from a Vitis command prompt at the
repository root:

```sh
vitis -s fpga/ps/mcu_v3/build_vitis.py
```

The ELF is `fpga/build/vitis/workspace_mcu_v3/mcu_v3/build/mcu_v3.elf`. It needs a
v3 or later bitstream. The firmware is loaded at run time (278 of the loader's 384
words), so the peer can change without a new bitstream.

For a bitstream built with `build.tcl -tclargs --mhz 48`, run `set MSF_CORE_MHZ=48`
in the prompt first: the script then assembles the peer with `CLK_SHIFT = 2`, and the
app's first line says `for a 48 MHz core clock`. An app built for the other clock
fails at once: the UART runs at a quarter or four times 115200. Make the platform
again after every new XSA (`vitis -s fpga/ps/build_platform.py`), then delete
`fpga/build/vitis/workspace_mcu_v3` before this script, so the app is made against it.
The steps for a whole run are in [docs/board-run-v4.md](../../../docs/board-run-v4.md).

The wiring is the v2 test's, unchanged. The test uses five of the eight wires:

| Wire | FPGA | MCU | Use in the v3 test |
|---|---|---|---|
| UIO1 | UART TX | PA9, UART1 RX | prompts and verdicts, 115200 8N1 |
| UIO6 | line in | PB17, SPI0 PICO | the line signals, drawn by the SPI at 16 Mbit/s |
| UIO7 | guarded output | PB19, read as GPIO | driven low, or off the bus |
| UIO3 | output | PA0, read as GPIO | driven low throughout the guard test |
| UIO4, UIO5 | inputs | PB18, PC7: inputs | not used; the MCU takes SCLK and CS off these pins |

## The monitor

The peer copies every byte it sends the MCU to `uo_out`, as events in
`mcu_protocol_target.s`'s format. The app follows the pass, which is a prompt and then
a fixed number of data bytes, and prints a summary when the pass ends with `P`:

```
FPGA ---- v3 pass 1: what the FPGA sent the MCU ----
FPGA cal  T=12288 cycles: MCU clock 32000 kHz
FPGA eth  0: status 81, 64 bytes after the SFD
...
FPGA usb  3: status 82, 36 bytes, SYNC 80 PID 4b
FPGA guard B: T0 late: UIO7 off the bus, LATE_RD 8011
FPGA totals: passes 1 | framing 0 | other events 0 | stray bytes 0
```

Tested on the PC by `fpga/tests/run_mcu_monitor_test.py`, and compile-checked against
the Vitis stubs by `fpga/tests/run_ps_loader_compile_test.sh`.
