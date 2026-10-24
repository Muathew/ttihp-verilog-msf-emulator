![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/formal/badge.svg) ![](../../workflows/fpga/badge.svg)

# msfcore — a reprogrammable protocol-emulator ASIC

msfcore is a 16-bit CPU for IHP SG13CMOS5L (Tiny Tapeout, 6x4 tiles) whose
instruction set is built for driving pins on exact cycles. Protocols are
firmware, loaded over UART or a parallel port after fabrication. Entry for
the Jane Street protocol emulator ASIC competition.

One chip plays the device, drives the bus, watches it and breaks it on
purpose: it can stand in for an SPI flash or an I2C EEPROM, probe a JTAG or
SWD target, report every timing fault on an I2C bus with its length, or
shift a device's edges by an exact number of cycles to find where it fails.

## The core

* **Exact timing.** One cycle per instruction, with documented costs for
  every op; `WAIT_CMP` waits on the absolute cycle counter, so a bit
  schedule never drifts; edge and level waits with timeouts.
* **A missed deadline is never silent.** A `WAIT_CMP` whose target has
  passed releases at once with Z=1 and sets the thread's LATE flag.
* **Four hardware threads** in a fixed rotation, each with its own
  registers: a thread's timing depends only on which threads run, never on
  what they do. A hardware FIFO passes words between them.
* **Line-code SERDES** (NRZ, NRZI, Manchester, bit stuffing, differential
  pairs) with receive, de-stuffing, resync and Manchester decoding; framing
  stays in firmware, so one engine serves UART, USB and 10BASE-T.
* **CRC unit** for any CRC up to 32 bits; **capture engine** recording 8
  pins with a trigger (a logic analyser); **glitch filter** per input pin;
  **debug view** of internal state on the outputs.
* **1024 x 16 SRAM** (two IHP macros): 896 words of firmware and 128 of data,
  with a 128-word boot ROM and a CRC-checked framed loader.

## The firmware ([`firmware/`](firmware))

| Role | Protocols |
|---|---|
| Drive a bus | UART, SPI (modes 0-3), I2C (repeated start, clock stretching), USB low-speed TX and RX with CRC16, 10BASE-T TX with FCS and link pulses, WS2812, CAN 2.0A (arbitration, CRC-15, stuffing, ACK), 1-Wire (SEARCH ROM) |
| Play a device | SPI target (64 registers, no dummy byte), 25xx SPI flash (ID, read, program, erase with WIP), I2C target (128-byte EEPROM), PS/2 keyboard |
| Debug probes | JTAG (IEEE 1149.1 scans, chains), SWD (ADIv5 DP and MEM-AP) |
| Watch and break | UART, I2C and SPI bus monitors that report timing faults with their lengths; timing-fault injection; UART-to-SPI bridge; logic analyser |

[`docs/instruments.md`](docs/instruments.md) gives each one's limits,
measured, and compares the approach with the RP2040's PIO: a PIO program
only moves bits and leaves the decisions to the ARM cores, while here the
same core makes the bit-rate decisions (an SPI target's address guess, an
I2C ACK, a monitor's timing check) and runs the whole job on the chip.

## How it is checked

Each kind of work is checked against something written independently of it
([`docs/verification.md`](docs/verification.md), with a table mapping every
claim to its evidence):

1. **An ISA model** written from [`isa.md`](docs/isa.md), not from the RTL,
   compared with the RTL on every issue of random programs.
2. **181 cocotb tests** in 21 modules, with peer models written from the
   protocol standards (USB, Ethernet, I2C, SPI, IEEE 1149.1, ADIv5, CAN,
   1-Wire, PS/2), not from the firmware.
3. **Formal properties** (SymbiYosys and ABC, in CI) with free memory and
   pins; known bugs put back must each give a counterexample, and every
   cover point must be reached.
4. **Mutation testing**: 37 of 40 seeded RTL faults are caught, and the
   other three cannot change behaviour.
5. **A static deadline checker** ([`tools/msf_timing.py`](tools/msf_timing.py))
   certifies that no `WAIT_CMP` in the firmware is ever late, on any path,
   and is itself cross-checked against the ISA model.
6. **The FPGA board** (AUP-ZU3): 14 of 14 C protocol tests pass on the same
   core, stepped by the Zynq.
7. **Sign-off in CI**: a job that fails on any setup or hold violation, DRC,
   antenna or LVS error, because the GDS badge alone does not.
8. **The routed netlist equals the RTL**, proved formally
   ([`formal/netlist_equiv.py`](formal/netlist_equiv.py)): from reset, on
   every cycle and for any inputs, so the checks above apply to the layout.

## Limits, honestly

* The clock is signed off at 33 MHz (30 ns) in every corner. At the slow
  corner (1.08 V, 125 °C) the worst path, SRAM read to register file, is
  about 28 ns; 50 MHz is met only at the typical corner
  ([verification](docs/verification.md) §7). Every firmware we ship needs
  20 MHz or less.
* The board tests step the core one clock at a time: they check logic and
  protocols on real hardware, not timing at speed.
* Firmware is written in assembly.
* 10BASE-T is transmit only: receiving needs about 80 MHz.
* Most formal properties are proved for all time, but four of the
  controller's (F3's timeout bounds, part of F10) only to depth 16
  ([verification](docs/verification.md) §3).

## Documentation

* [Datasheet](docs/info.md) — pinout, boot protocol, how to test
* [ISA v2.2](docs/isa.md) — encodings, timing model, peripherals, threads
* [Instruments](docs/instruments.md) — device emulation, bus monitors, fault injection, logic analyser, debug probes, other buses, comparison with PIO
* [Verification](docs/verification.md) — model, random differential tests, formal, deadlines, FPGA, hardening, mutation, claims and evidence
* [How AI was used](docs/ai.md) — who did what, the independent check for each AI-written artefact, and where the AI was wrong
* [Plan](docs/plan.md) — what is left before tape-out, with status
* [FPGA port](docs/fpga.md) — AUP-ZU3 board mapping, block design, build, status
* [FPGA PS software](fpga/ps/README.md) — Zynq loader and the C protocol tests
* [Test suite](test/README.md) — running the cocotb tests
* [History](docs/history.md) — decisions, plans and problems, as one log
* [Competitor research](docs/research.md) — other entries and prior art, dated surveys
* [GDS viewer](https://muathew.github.io/ttihp-verilog-msf-emulator/) — 3D view of the hardened layout

## Repository layout

| Path | Contents |
|---|---|
| `src/` | RTL (Verilog-2005): `project.v` top, `tt_um_msf_ctrl.v` core, `_serdes`, `_crc`, `_cap`, `_filt`, `_pio`, `_sram`, `_bootrom`, `_regfile`, `_alu`; the LibreLane config (`config.json`, `pdn_cfg.tcl`) |
| `librelane_plugin_msf_pdn.py`, `odb_sram_stripes.py` | a LibreLane step that puts the power stripes on the SRAM macros' supply pins (CMOS5L) |
| `firmware/` | assembly programs, `msf.inc` constants, `report.inc` (the monitors' event reporter) |
| `test/` | cocotb suite, assembler (`assembler.py`), ISA reference model (`isa_model.py`), boot ROM source (`bootloader.s`) |
| `formal/` | formal properties and their runner (`run_formal.py`: SymbiYosys with ABC) |
| `tools/` | `msf_boot.py` host loader, `msf_timing.py` deadline checker, `check_signoff.py` sign-off check, `mutation.py` mutation score, `gds_metrics.py` hardening table, `synth_check.py` area pre-check |
| `fpga/` | AUP-ZU3 RTL, board constraints, Vivado block design, Zynq PS loader and C protocol tests |

## Running

```sh
pip install -r test/requirements.txt        # cocotb; plus Icarus Verilog
cd test && make                              # RTL suite (Windows: python run_tests.py --all)
python tools/msf_timing.py --all             # certify every firmware deadline
python formal/run_formal.py --engine abc     # needs SymbiYosys (OSS CAD Suite)
python tools/mutation.py                     # mutation score (hours: every module)
python test/assembler.py                     # regenerate the boot ROM after editing bootloader.s
python tools/msf_boot.py firmware/uart_tx.s --port COM5   # load onto silicon
```

## What is Tiny Tapeout?

Tiny Tapeout is an educational project that makes it easier and cheaper than
ever to get digital designs manufactured on a real chip. See
https://tinytapeout.com.
