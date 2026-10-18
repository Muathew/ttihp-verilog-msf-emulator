![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/formal/badge.svg) ![](../../workflows/fpga/badge.svg)

# msfcore — a reprogrammable protocol-emulator ASIC

A 16-bit CPU for IHP SG13G2 (Tiny Tapeout, 6x4 tiles) whose instruction set
is built for bit-banging: protocols are firmware, loaded over UART or a
parallel port after fabrication. Entry for the Jane Street protocol emulator
ASIC competition.

* **Deterministic hardware threads** — two barrel-interleaved threads, each
  with its own registers and stall state; one thread's timing never depends on
  the other's (e.g. an exact square wave on T0 while T1 echoes UART).
* **Exact timing** — 1 cycle per instruction, documented costs for every op,
  absolute cycle-counter waits (`WAIT_CMP`) for drift-free bit schedules,
  edge/level waits with programmable timeouts.
* **Line-code SERDES** — NRZ / NRZI / Manchester, bit stuffing, differential
  pairs, gapless double buffering; RX with de-stuffing and edge resync.
  Framing stays in firmware, so the same hardware serves UART, USB and
  10BASE-T.
* **CRC unit** — any CRC up to 32 bits (CRC-8, USB CRC5/16, Ethernet CRC-32).
* **512 x 16 SRAM** (IHP two-port macro): 384 words of firmware + 128 words of
  data RAM, 128-word boot ROM with a CRC-checked framed loader.

Firmware in [`firmware/`](firmware): UART, SPI (modes 0-3, full duplex),
I2C (ACK/NACK, repeated start, clock stretching), USB low-speed TX and RX
with CRC16, 10BASE-T transmit with FCS and link pulses, and a two-thread demo.

## Documentation

* [Datasheet](docs/info.md) — pinout, boot protocol, how to test
* [ISA v2.0](docs/sam/isa.md) — encodings, timing model, peripherals, threads
* [Implementation plan](docs/sam/IMPLEMENTATION_PLAN.md) — review findings and phases
* [FPGA porting plan](docs/muath/fpga-porting-plan.md) — AUP-ZU3 port status and current Vivado block design
* [FPGA PS software](fpga/ps/README.md) — Zynq loader and the C protocol tests (step mode over EMIO)
* [Verification](docs/golden-model.md) — reference model, random differential testing, formal

## Repository layout

| Path | Contents |
|---|---|
| `src/` | RTL (Verilog-2005): `project.v` top, `tt_um_msf_ctrl.v` core, `_serdes`, `_crc`, `_pio`, `_sram`, `_bootrom`, `_regfile`, `_alu` |
| `fpga/` | AUP-ZU3 RTL, inferred BRAM, board constraints, Vivado block-design Tcl, Zynq PS loader and C protocol tests |
| `firmware/` | assembly programs + `msf.inc` constants |
| `test/` | cocotb suite, assembler (`assembler.py`), ISA reference model (`isa_model.py`), boot ROM source (`bootloader.s`) |
| `formal/` | formal properties runner (`run_formal.py`, yosys `sat`) |
| `tools/` | `msf_boot.py` host loader, `synth_check.py` area/timing pre-check |

## Running

```sh
pip install -r test/requirements.txt        # cocotb; plus Icarus Verilog
cd test && make                              # RTL suite (Windows: python run_tests.py --all)
python formal/run_formal.py --yosys yowasp-yosys   # pip install yowasp-yosys
python test/assembler.py                     # regenerate the boot ROM after editing bootloader.s
python tools/msf_boot.py firmware/uart_tx.s --port COM5   # load onto silicon
```

## What is Tiny Tapeout?

Tiny Tapeout is an educational project that makes it easier and cheaper than
ever to get digital designs manufactured on a real chip. See
https://tinytapeout.com.
