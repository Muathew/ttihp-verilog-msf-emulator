# What is where

The README is one screen; this is the rest of the map.

## Layout

| Path | Contents |
|---|---|
| `src/` | RTL (Verilog-2005): `project.v` top, `tt_um_msf_ctrl.v` core, `_serdes` (the engines, two sets), `_crc`, `_cap`, `_filt`, `_pio`, `_sram` (four 512-word macros), `_bootrom`, `_regfile`, `_alu`; the LibreLane config (`config.json`, `pdn_cfg.tcl`, `pnr.sdc`, `signoff.sdc`) |
| `librelane_plugin_msf_pdn.py`, `odb_sram_stripes.py` | a LibreLane step that puts the power stripes on the SRAM macros' supply pins (CMOS5L) |
| `firmware/` | 79 assembly programs, `msf.inc` constants, `report.inc` (the monitors' event reporter); `firmware/msfc/` programs in the `msfc` language |
| `test/` | the cocotb suite (77 modules), the assembler (`assembler.py`), the ISA reference model (`isa_model.py`), the boot ROM source (`bootloader.s`), the pure-Python tests (`test_msfc.py`, `test_site.py`, `test_learn_tool.py`) |
| `formal/` | the formal properties and their runner (`run_formal.py`: SymbiYosys with ABC), `parse_check.py` (elaborates every target without a solver) |
| `tools/` | `msf_boot.py` host loader, `msf_timing.py` deadline checker, `msfc.py` the timing-first language, `check_signoff.py` sign-off check, `mutation.py` mutation score, `gds_metrics.py` hardening table, `synth_check.py` area pre-check, `devsim.py` run a program against its test's device on the ISA model, `sram_pdn_check.py` SRAM rails, stranded cells and shorts over the macros on a hardened ODB |
| `fpga/` | AUP-ZU3 RTL (`fpga/rtl`, a copy of `src/` but for the board's SRAM and ROM wrappers), board constraints, Vivado block design, Zynq PS loader and C protocol tests |
| `site/` | the browser playground (the assembler, checker and model, through Pyodide) |
| `docs/` | everything below |

## Firmware by role ([`firmware/`](../firmware))

| Role | Protocols |
|---|---|
| Drive a bus | UART, SPI (modes 0-3), I2C (repeated start, clock stretching), USB low-speed TX and RX with CRC16, 10BASE-T TX with FCS and link pulses, WS2812, CAN 2.0A (arbitration, CRC-15, stuffing, ACK), 1-Wire (SEARCH ROM) |
| Play a device | SPI target (64 registers, no dummy byte), 25xx SPI flash (ID, read, program, erase with WIP), I2C target (128-byte EEPROM), PS/2 keyboard |
| Debug probes | JTAG (IEEE 1149.1 scans, chains), SWD (ADIv5 DP and MEM-AP) |
| Watch and break | UART, I2C and SPI bus monitors that report timing faults with their lengths, and flight-recorder versions that keep the waveform around the first fault; timing-fault injection; a margin finder that sweeps a device's timing on the chip alone; an in-line interposer that changes one frame; UART-to-SPI bridge; logic analyser with streaming and replay |
| Reverse-engineer | a pin finder that works out which of eight wires are UART, SWD and JTAG; learning a protocol from a recording, then sending a message the transmitter never sent |
| Drawn at random, after the chip was finished | S/PDIF at 48 kHz, an EM4100 RFID tag, Microwire (93C46), a Siemens SDA 2506 EEPROM, HDMI-CEC, the Commodore serial bus: picked by a public random round, written for the unchanged chip, judged by sigrok's decoders ([results](instruments.md#16-protocols-drawn-at-random--firmwaredraw_s-test_draw_py-docsdraw)) |

[`instruments.md`](instruments.md) gives each one's limits, measured, and compares the
approach with the RP2040's PIO: a PIO program only moves bits and leaves the decisions to the
ARM cores, while here the same core makes the bit-rate decisions (an SPI target's address
guess, an I2C ACK, a monitor's timing check) and runs the whole job on the chip.

## Running

```sh
pip install -r test/requirements.txt        # cocotb; plus Icarus Verilog
cd test && make                              # RTL suite (Windows: python run_tests.py --all)
python run_tests.py -m test_timed_pins       # one module; -t <exact test name> for one test
python -m pytest test/test_learn_tool.py test/test_site.py test/test_msfc.py   # pure Python
python tools/msf_timing.py --all             # certify every firmware deadline
python tools/msfc.py firmware/msfc/uart_tx.msf --run 1400    # compile, certify, run on the model
python tools/devsim.py sdspi.s test_sdspi Card 600000   # a program against its test's device, on the ISA model
python formal/run_formal.py --engine abc     # needs SymbiYosys with a native yosys-abc (OSS CAD Suite)
python formal/parse_check.py                 # elaborate every formal target; needs no solver
python tools/mutation.py                     # mutation score (hours: every module)
python test/assembler.py                     # regenerate the boot ROM after editing bootloader.s
python tools/msf_boot.py firmware/uart_tx.s --port COM5   # load onto silicon
```

## Documents

| | |
|---|---|
| [Architecture](architecture.md) | the whole design on one page, for someone who knows PIO and PRU |
| [ISA v4](isa.md) | encodings, timing model, timed pins, engines, threads: the contract |
| [Envelope](envelope.md) | how fast each class of protocol goes, from tests, and what it cannot do |
| [msfc](msfc.md) | the language in which a program that compiles is on time |
| [Instruments](instruments.md) | device emulation, monitors, fault injection, logic analyser, debug probes, other buses, comparison with PIO |
| [Verification](verification.md) | model, random differential tests, formal, deadlines, FPGA, hardening, mutation, claims and evidence |
| [SRAM power integration](sram-power-integration.md) | the 8x4 VPWR/VGND short (13-14 Nov run), its cause, the floorplan fix, `tools/sram_pdn_check.py` |
| [Datasheet](info.md) | pinout, boot protocol, how to test |
| [How AI was used](ai.md) | who did what, the independent check for each AI-written artefact, and where the AI was wrong |
| [Plan v4](plan-v4.md) | the plan this branch implements, with its status table |
| [v4 bugs](v4-bugs.md), [v4 open problems](v4-open-problems.md) | every bug found while building v4, with file references, and what could not be checked: written for review |
| [FPGA port](fpga.md), [FPGA PS software](../fpga/ps/README.md) | AUP-ZU3 board mapping, build, status; the Zynq loader and C protocol tests |
| [Test suite](../test/README.md) | running the cocotb tests |
| [History](history.md), [The brief and prior art](research.md), earlier plans [v2.2](plan.md) and [v3](plan-v3.md) | decisions and problems as a log; what the brief asks for and prior art |
| [GDS viewer](https://muathew.github.io/ttihp-verilog-msf-emulator/) | 3D view of the hardened layout (of v2.2) |
