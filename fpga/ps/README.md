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
   [the porting plan](../../docs/muath/fpga-porting-plan.md)).
2. `python fpga/ps/hil/gen_images.py` (writes `hil/src/msf_images.h`).
3. In Vitis, create a standalone application for `psu_cortexa53_0` and add
   `hil/src/main.c`, `hil/msf_tests.c`, `hil/msf_proto.c`,
   `common/msf_link.c` and `common/msf_emio_xgpiops.c`, with include paths
   `common`, `hil` and `hil/src`.
4. Run it. Results print on the PS UART (115200 8N1), one PASS/FAIL line per
   test, then `ALL PASSED` or `FAILED: n`.

The loader app is built the same way from `loader/src/main.c` plus the two
`common` sources, after `python fpga/ps/loader/generate_image.py <file.s>`.
A good first image is `loader/blink.s` (Muath's smoke test): LED0 toggles
every 6,000,000 cycles, so it blinks at exactly 1 Hz on the 12 MHz
`pl_clk0`, with nothing connected to PMOD JA. To re-run whatever is
already in SRAM without reloading it, call `msf_restart()`.

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

Random data comes from a fixed-seed xorshift PRNG, so the byte values differ
from the Python tests but every run is the same.

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
