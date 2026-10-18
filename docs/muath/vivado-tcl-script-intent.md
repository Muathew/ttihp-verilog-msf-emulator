# `msf_bd.tcl`: current block-design behavior

## Scope

[`msf_bd.tcl`](../../fpga/vivado/msf_bd.tcl) is a Vivado IP Integrator
block-design creation script generated for `msf_zynq_system` with Vivado
2024.1. It instantiates the Zynq UltraScale+ PS, the `aup_zu3_io` RTL module,
and reset/concatenation/constant IP, then validates and saves the block
design.

It is **not** a complete FPGA build script. It does not add the RTL source
files or XDC, generate the HDL wrapper, set the project top, run synthesis or
implementation, generate a bitstream, or export an XSA. Those tasks remain
manual follow-up steps in Vivado.

## Project and tool behavior

- The script checks for Vivado 2024.1 and returns without creating the design
  if the running version does not match.
- If no project is open, the script creates `myproj/project_1` relative to
  Vivado's current working directory, using part
  `xczu3eg-sfvc784-2-e` and board part
  `realdigital.org:aup-zu3-8gb:part0:1.0`.
- If a project is already open, it uses that project and does not check its
  part or board preset. The PS IP configuration is set by the script.
- The module reference `aup_zu3_io` must already be present in the project
  sources, along with the RTL modules it instantiates. The required IP
  catalog entries must also be available.
- The script creates `msf_zynq_system` unless that design already exists or
  the current project/design state conflicts with it. It then runs
  `validate_bd_design` and `save_bd_design`.

The script contains generated absolute board/PS configuration values and a
fixed Vivado version. It is not yet an operating-system- and
project-directory-independent build flow.

## Block diagram cells

| Cell | IP/module | Role |
|---|---|---|
| `zynq_ultra_ps_e_0` | Zynq UltraScale+ PS, IP version 3.5 | Processing system; exposes 11 EMIO GPIO bits and the PL clock/reset |
| `aup_zu3_io_0` | Module reference `aup_zu3_io` | Emulator core, board I/O wrapper, and PS/PMOD handoff |
| `proc_sys_reset_0` | Processor System Reset, IP version 5.0 | Generates the wrapper's active-low reset |
| `xlconcat_0` | Concatenate, IP version 2.1 | Packs 8 status bits and 3 constant-low bits for PS EMIO GPIO input |
| `xlconstant_0` | Constant, IP version 1.1 | Supplies the 3 low bits of EMIO GPIO input |

There is **no AXI GPIO IP** in this block design. PS M_AXI GP0/GP1/GP2 are
disabled. The control path is PS GPIO over EMIO.

## Exact clock, reset, and GPIO connections

`->` denotes a direct net connection in the generated Tcl.

| Source | Destination | Meaning |
|---|---|---|
| `zynq_ultra_ps_e_0/pl_clk0` | `proc_sys_reset_0/slowest_sync_clk` | 12 MHz clock for reset synchronization |
| `zynq_ultra_ps_e_0/pl_clk0` | `aup_zu3_io_0/core_clk` | Emulator core clock |
| `zynq_ultra_ps_e_0/pl_clk0` | `aup_zu3_io_0/ps_clk` | Wrapper's status synchronization clock |
| `zynq_ultra_ps_e_0/pl_resetn0` | `proc_sys_reset_0/ext_reset_in` | Active-low reset input to reset controller |
| `proc_sys_reset_0/peripheral_aresetn` | `aup_zu3_io_0/core_resetn` | Active-low wrapper/core reset |
| `zynq_ultra_ps_e_0/emio_gpio_o` | `aup_zu3_io_0/ps_gpio_o` | PS EMIO GPIO output values `[10:0]` |
| `zynq_ultra_ps_e_0/emio_gpio_t` | `aup_zu3_io_0/ps_gpio_t` | PS EMIO GPIO tri-state/direction values `[10:0]` |
| `aup_zu3_io_0/ps_status_i[7:0]` | `xlconcat_0/In1[7:0]` | Core status sampled by the PS |
| `xlconstant_0/dout[2:0]` | `xlconcat_0/In0[2:0]` | Constant-low padding |
| `xlconcat_0/dout[10:0]` | `zynq_ultra_ps_e_0/emio_gpio_i[10:0]` | Status and padding returned to PS EMIO GPIO |

The concatenated input is `{ps_status_i[7:0], 3'b000}`: core status occupies
EMIO input bits `[10:3]`, and bits `[2:0]` are tied low. `pl_clk0` is
configured to 12 MHz directly in the PS configuration. There is no Clocking
Wizard, AXI clock, or AXI reset connection in this block design.

The wrapper interprets the PS EMIO output bits as follows:

| EMIO bit(s) | Wrapper meaning |
|---|---|
| `[7:0]` | Parallel boot data byte on core `ui_in[7:0]` while PS pin ownership is active |
| `[8]` | Request PS ownership of the input and bidirectional interface |
| `[9]` | PS reset-release request |
| `[10]` | Parallel boot strobe, presented to core `uio_in[1]` when the PS owns the interface |

The wrapper uses the corresponding `ps_gpio_t` bits to ensure the PS is
driving the ownership, reset-release, and strobe controls. Channel numbers
belong to AXI GPIO and do not apply to this EMIO wiring.

## External board ports

| Top-level BD port | Direction and width | Wrapper connection/behavior |
|---|---|---|
| `PL_USER_SW[7:0]` | Input, 8 | Drives core `ui_in` when the PS does not own the interface |
| `PL_USER_PB[3:0]` | Input, 4 | Bit 0 participates in the user reset gate; other bits are unused by the wrapper |
| `PL_USER_LED[7:0]` | Output, 8 | Reflects core `uo_out[7:0]` |
| `JA_tri_io[7:0]` | Inout, 8 | Bidirectional PMOD JA connection to core `uio_in/out/oe`; tri-stated during PS ownership |

The script connects each of these BD ports directly to the matching pin on
`aup_zu3_io_0`. It does not add the board XDC; the project must add
[`msf_aup_zu3.xdc`](../../fpga/board/constraints/msf_aup_zu3.xdc) separately
before implementation.

## Running and completing the Vivado flow

With the required RTL sources already added and a project open, run:

```tcl
source {D:/path/to/repository/fpga/vivado/msf_bd.tcl}
```

Use the equivalent path on Linux. If no project is open, the script creates
the default project described above. It does not consume an `.xpr` path from
`-tclargs`.

After the block design is created and validated, the remaining project steps
are to add the board XDC, create and add the BD HDL wrapper, select the wrapper
as the project top, then run synthesis, implementation, and bitstream
generation. Those steps are not automated by `msf_bd.tcl`.

## Loader software integration status

[`main.c`](../../fpga/ps/loader/src/main.c) currently uses the standalone
`XGpio` driver and expects an `AXI_GPIO_0` device with two channels: channel 1
as 11-bit control outputs and channel 2 as 8-bit status inputs. It generates
the existing parallel boot protocol in software, sends `W` and `G` frames,
checks CRC-8/0x07 frame results and alternating acknowledgement on status bit
7, then releases PS pin ownership after the core reports that firmware is
starting. The default firmware header is produced by
[`generate_image.py`](../../fpga/ps/loader/generate_image.py); the C loader
supports at most 384 words in a 512-word address space.

The current `msf_bd.tcl` does not instantiate `AXI_GPIO_0` and does not expose
its two-channel driver interface. It instead routes the PS's 11-bit EMIO GPIO
directly to the wrapper. Therefore the current C loader cannot be used with
the block design unchanged: the PS GPIO access in software and the block
design wiring must be reconciled before claiming an end-to-end PS firmware
load path.

Vivado BD validation, synthesis/implementation, bitstream generation, XSA
export, Vitis compilation, and hardware operation have not been demonstrated
by this script alone.
