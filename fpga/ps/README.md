# Zynq PS software for the AUP-ZU3 port

Bare-metal (Vitis standalone) C for the Cortex-A53 that programs and tests
the msfcore in the PL. It talks to the core only through 32 PS EMIO GPIO bits
(bank 3) wired to [`fpga/rtl/aup_zu3_io.v`](../rtl/aup_zu3_io.v).

| Path | Contents |
|---|---|
| `common/msf_emio.h` | EMIO bit map (shared with the wrapper) and the four-call HAL |
| `common/msf_emio_xgpiops.c` | HAL on the PS GPIO controller (board) |
| `common/msf_link.[ch]` | reset, pin ownership, single-step, boot ROM parallel protocol |
| `loader/` | loads one image (`generate_image.py`), starts it, hands the pins to PMOD JA |
| `mcu_test/` | Vitis app that loads the UART/I2C/SPI peer for an LP-MSPM0G3519 on PMOD JA and prints what it reports |
| `hil/msf_proto.[ch]` | protocol models in C: UART, SPI slave, I2C slave, USB LS, 10BASE-T, CRCs |
| `hil/msf_tests.[ch]` | the firmware tests of `test/test_firmware.py` and the parallel boot tests |
| `hil/firmware/` | HIL helpers: `dump_ram.s` (reads data RAM back), `boot_probe.s` |
| `hil/gen_images.py` | assembles `firmware/*.s` and `hil/firmware/*.s` into `msf_images.h` |
| `hil/src/main.c` | Vitis app that runs every test |
| `sim/msf_emio_sim.c` | host HAL: runs the same C against the RTL in cocotb |

## EMIO map (bank 3, EMIO 0-31 = PS GPIO 78-109)

| Bits | PS → PL | | Bits | PL → PS |
|---|---|---|---|---|
| 7:0 | `ui_in` while the PS owns the pins | | 2:0 | 0 |
| 8 | OWN: PS owns `ui_in`/`uio_in`, PMOD JA high-Z | | 10:3 | `uo_out` |
| 9 | RUN: core reset release | | 11 | ACK: = REQ once the step has run |
| 10 | STB: parallel-boot strobe (ORed into `uio_in[1]`) | | 19:12 | `uio_out` |
| 11 | STEP: step mode | | 27:20 | `uio_oe` |
| 12 | REQ: each toggle runs one core cycle | | 28 | core `rst_n` |
| 20:13 | `uio_in` while the PS owns the pins | | 29 | step mode active |

Bits [10:0] keep their original meaning, and `uo_out` is still on [10:3].

## Step mode

With STEP set, the core clock goes through a `BUFGCE` and runs only when the
PS toggles REQ: exactly one core edge per toggle, then the wrapper captures
`{uio_oe, uio_out, uo_out}` and flips ACK. `msf_step(uio)` drives `uio_in`,
toggles REQ and returns the pins after that cycle, so a C peer model sees
every cycle and answers in time, however slow the PS is. This makes a test on
the board cycle-for-cycle the same as the cocotb test it ports: same
firmware, same reset, same per-cycle sampling. Free run (STEP clear) is the
normal 12 MHz operation the loader uses.

## Running the tests

In simulation (no board; needs gcc, iverilog and the `test/` venv):

```sh
python fpga/tests/run_ps_hil.py                        # all C tests
python fpga/tests/run_ps_hil.py -t spi_all_modes_full_duplex
```

This builds the C with `sim/msf_emio_sim.c` into a shared library and steps
it in lockstep with `fpga/rtl` (wrapper + core) under cocotb. Each PS GPIO
access takes `--access-cycles` PL clocks (default 2).

On the board:

1. Build the bitstream from the block design and export the XSA (see
   [the FPGA port doc](../../docs/fpga.md)).
2. `python fpga/ps/hil/gen_images.py` (writes `hil/src/msf_images.h`).
3. In Vitis, create a standalone application for `psu_cortexa53_0` and add
   `hil/src/main.c`, `hil/msf_tests.c`, `hil/msf_proto.c`,
   `common/msf_link.c` and `common/msf_emio_xgpiops.c`, with include paths
   `common`, `hil` and `hil/src`.
4. Run it. Results print on the PS UART (115200 8N1), one PASS/FAIL line per
   test, then `ALL PASSED` or `FAILED: n`.

**Execution digest.** Before each PASS/FAIL line the runner prints
`msf-hil: digest <test> <crc32> over <n> steps`: a CRC-32 of every step the
test took (`ui_in`, `uio_in`, `uo_out`, `uio_out`, `uio_oe` per core cycle,
`msf_digest()` in `msf_link.c`). The C code is the same on the board and in
simulation, so a core that is cycle-exact on the board gives the same
digest as `run_ps_hil.py` for every test: comparing the two logs' digest
lines compares the runs cycle for cycle, without a trace. Free-run samples
(the boot loader's status polls) are left out, because how many there are
depends on the PS's speed.

The loader app is built the same way from `loader/src/main.c` plus the two
`common` sources, after `python fpga/ps/loader/generate_image.py <file.s>`.
A good first image is `loader/blink.s` (Muath's smoke test): LED0 toggles
every 6,000,000 cycles, so it blinks at exactly 1 Hz on the 12 MHz
`pl_clk0`, with nothing connected to PMOD JA. To re-run whatever is
already in SRAM without reloading it, call `msf_restart()`.

To run the LP-MSPM0G3519 UART, I2C and SPI tests, follow
[`mcu_test/README.md`](mcu_test/README.md). Its Vitis app loads
`firmware/mcu_protocol_target.s`, releases PS ownership so PMOD JA
connects to UIO[0:7], and prints the events the firmware sends on `uo_out`.
Unlike the C tests below, the core runs free at 12 MHz there, not stepped.

## Tests

| C test | Ports (cocotb) | Checks at the pins |
|---|---|---|
| `crc_check_values` | — | CRC-8/0x07, USB CRC16, Ethernet FCS check values (no hardware) |
| `boot_parallel_go` | `test_boot.test_parallel_load_and_run` | W + G frames through the boot ROM; firmware runs |
| `boot_bad_crc_then_retry` | `test_boot.test_uart_bad_crc_nak_then_retry` | junk bytes, corrupted frame NAKed (status bit 2), retry loads and runs |
| `restart_direct_boot` | — | `msf_restart()` re-runs SRAM via direct boot (no ROM status bits) |
| `uart_tx_drift_free` | `test_fw_uart_tx_drift_free` | 8N1 text on uio0; every edge on the 104-cycle grid |
| `spi_all_modes_full_duplex` | `test_fw_spi_all_modes_full_duplex` | modes 0-3 against the SPI slave model; MISO bytes read back from data RAM |
| `i2c_write_ack` / `i2c_address_nack` / `i2c_write_then_read_repeated_start` | the same names | I2C slave model: data, ACK/NACK, S/Sr/P events, read data from data RAM |
| `i2c_constrained_random_with_stretching` | the same name | 6 random transactions, slave stretches SCL |
| `usb_ls_tx_packet` | `test_fw_usb_ls_tx_packet` | NRZI + stuffing decode, CRC16, 8-cycle grid, SE0 = 16, J = 8, release |
| `usb_ls_rx_packet` | `test_fw_usb_ls_rx_packet` | PS drives D-/D+ at 0 and ±1.5 % clock error, plus a corrupted packet |
| `eth_10baset_frame_fcs_tpidl_nlp` | `test_fw_10baset_frame_fcs_tpidl_nlp` | Manchester decode, preamble/SFD, frame, FCS, TP_IDL = 6, NLP width and period |
| `threads_blink_and_echo` | `test_fw_threads_blink_and_echo` | PS is the UART host on uio1; T1 echo on uio0; T0 half period exactly 500 |

The tests above passed on the AUP-ZU3 (23 Oct 2026). These were ported
since, and all 67 then passed on the board in a later run
([the FPGA port doc](../../docs/fpga.md)):

| C tests | Ports (cocotb) | Checks at the pins |
|---|---|---|
| `jtag_probe`, `swd_probe` | `test_debugports.py` | TAP chain, SW-DP with a MEM-AP, modelled in C |
| `onewire_probe` | `test_buses.py` | 1-Wire devices modelled in C |
| `spi_target_regs`, `spi_flash`, `i2c_target_eeprom` | `test_targets.py` | the chip as SPI device, 25xx flash and I2C EEPROM (with clock stretching) |
| `four_threads_fifo` | `test_threads.py` | three exact square waves at once; T0's counts reach T3 through the FIFO |
| `mon_uart_*` 3, `mon_i2c_*` 6, `mon_spi_*` 3 | `test_monitors.py` | the bus monitors: the same waves, faults and accuracy checks |
| `bridge_modes` | `test_bridge.py` | UART-to-SPI bridge, SPI modes 0-3 |
| `fw_logic_analyzer`, `cap_*` 8 | `test_capture.py` | capture engine word for word against `cap_ref`; pins driven as a function of CNT |
| `ps2_device_*` 3, `can_*` 3 | `test_buses.py` | PS/2 keyboard against a host model; CAN node against nodes with CRC-15 and stuffing |
| `fault_*` 4 | `test_faults.py` | UART, I2C and SPI timing knobs move edges by exact amounts |
| `fw_ws2812` | `test_firmware.py` | 15-cycle bits, 10/5 high, colours in order, latch gap |
| `filter_*` 4, `rx_manchester_*` 2 | `test_peripherals.py` | glitch filter (widths, every pin, delay, spiky UART); Manchester RX with jitter and loopback |
| `debug_*` 5, `tmo_per_thread` | `test_debug.py`, `test_threads.py` | the debug view (one core cycle a read: the wrapper captures the pins after a step); per-thread timeouts |
| `swd_session_model` | — | `hil/firmware/swd_session.s` against the SW-DP model set up as a Cortex-M4: the script and checks of `swd_mcu`, plus WAIT retries |

Where a Python test reads registers or SRAM, small firmware in
`hil/firmware/` stores the result in RAM and the C side reads it back with
`dump_ram()` (any address), or reads it on the debug view.

Random data comes from a fixed-seed xorshift PRNG, so the byte values differ
from the Python tests but every run is the same.

## SWD with a microcontroller on PMOD JA (`swd_mcu`)

The one test with a real part: the core is the SWD probe and any Cortex-M
with an SW-DP is the target (TI's TM4C, MSP432, MSPM0 and others; not the
MSP430, which has Spy-Bi-Wire instead). It is never in the full run; build
the `hil` app with `MSF_HIL_MCU` defined (Vitis: C/C++ build settings,
symbols) and it runs `swd_mcu` alone.

Wiring (PMOD JA is 3.3 V, so a 3.3 V target): SWCLK to JA bit 2 (`uio[2]`),
SWDIO to JA bit 3 (`uio[3]`), and ground to ground; power the target from
its own supply. With the usual Pmod numbering bits 0-3 are pins 1-4; check
it against the board's manual. The target's own debugger must not drive the
two lines: on a LaunchPad with an XDS110, remove the SWDIO and SWCLK
jumpers of the isolation block and wire to the target side.

The PS cannot reach the core's UART while PMOD JA is connected (the wrapper
gives `uio_in` to one or the other), so `swd.s`'s command loop is not used.
`swd_session.s` takes a list of transactions in data RAM, runs them alone
(line reset and JTAG-to-SWD switch first, WAITs retried), and the PS reads
the results back after the pins return. The session reads DPIDR, powers up
debug, reads AP 0's IDR and the CPUID at `0xE000ED00`, writes two patterns to
DCRDR (`0xE000EDF8`, a debug data register that is safe to write while the
core runs) and reads them back, then drops the power-up request. A pass
prints what it found, e.g.
`SWD target: DPIDR 2ba01477, AP IDR 24770011, CPUID 410fc241 = Cortex-M4 r0p1`.
A failure names the transaction and its ACK; ACK 0 or 7 means no answer
(power, wiring, or the debugger still on the lines).

`swd_session_model` runs the same firmware, script and checks against the C
model, in simulation and on the board. The hand-over to PMOD JA was checked
in simulation, where nothing is plugged in: `swd_mcu` stops at the first
transaction with ACK 0.

## Adding a protocol test

1. Write the firmware in `firmware/` and add it to `IMAGES` in
   `hil/gen_images.py`.
2. If the PS has to act as the other end of the bus, add a peer model to
   `hil/msf_proto.c`: a struct plus a `*_cycle(model, pins)` function that
   returns the `uio_in` for the next cycle (see `msf_spi_slave_cycle`).
3. Add a `test_*` function and a table entry in `hil/msf_tests.c`:
   `fw_boot()` the image with its data-RAM parameters, loop
   `p = cycle(uio); uio = model_cycle(&m, p);`, then check, using
   `dump_ram()` for results the firmware leaves in data RAM.
4. Run `python fpga/tests/run_ps_hil.py -t <name>` before trying it on the board.
