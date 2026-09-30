<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

tt_um_msf is a **reprogrammable protocol-emulator ASIC** — a tiny RISC CPU whose ISA is
built for pin I/O and cycle-accurate timing, so communication protocols (UART, SPI, I2C)
run as **firmware** (loaded into on-chip SRAM) rather than fixed hardware. This makes the
chip **reprogrammable after fab**: flash new protocol firmware over a serial link to
change what the chip does.

### Architecture

- **Hybrid RISC core** (F1.5 footprint): 8 × 16-bit registers, 5-bit opcodes (32
  instructions), single-cycle execute with a 2-stage fetch pipeline.
- **Hardware serial shift datapath** (OSR/ISR) for fast bit-level protocol work.
- **Event-driven stall instructions** (`WAIT_EDGE`, `WAIT_CMP`) for pin-edge timing.
- **128-word boot ROM** + **384-word SRAM** (via `RM_IHPSG13_2P_512x16` IHP macro)
  for instruction memory.
- **8 bidirectional I/O pins** with open-drain capability (for I2C, JTAG, etc.).
- **16-bit free-running cycle counter** for bit-accurate timing.

### Memory map

| Address | Contents |
|---|---|
| 0 – 127 | Boot ROM (UART-RX bootloader) |
| 128 – 511 | SRAM (firmware, loaded via `MEM_W` or UART boot) |

### Clock

~12 MHz target (IHP SG13G2 130 nm, Tiny Tapeout 6 × 4 mm die).

## How to test

1. **Simulate** (cocotb + Icarus Verilog):
   ```
   . .\setup-env.ps1
   cd test; make -B
   ```
2. **FPGA**: deploy via Tiny Tapeout's FPGA test bench.
3. **Serial**: load firmware over UART (115200 8N1) — the boot ROM in words 0–127
   receives the image and writes it to SRAM via `MEM_W`.

## External hardware

- **None required** for basic operation (all I/O is on the TT pin header).
- Optional: UART bridge (USB-serial) for firmware loading, I2C/SPI peripheral for
  protocol testing.
