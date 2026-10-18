# AUP-ZU3 FPGA port

## Goal and target

Port the msfcore design to the AUP-ZU3 V3I1 board, using the XCZU3EG
programmable logic (PL) for the emulator and the Zynq UltraScale+ processing
system (PS) to load its firmware. Preserve the ISA, cycle-level behavior,
parallel/UART boot-ROM protocol, and peripheral pin semantics. Keep the
existing IHP ASIC implementation and tapeout flow separate and unchanged.

The project-specific pin constraints are in
`fpga/board/constraints/msf_aup_zu3.xdc`; compare these assignments with the
schematic/manual for the exact physical board revision before use. The FPGA
RTL copy is in `fpga/rtl/`. Its `tt_um_msf_sram` is the FPGA-specific
inferred-memory backend; the ASIC implementation remains in
`src/tt_um_msf_sram.v`.

## Board interface and clock

The board wrapper, `fpga/rtl/aup_zu3_io.v`, uses this mapping:

| AUP-ZU3 signal | Emulator signal | Use |
|---|---|---|
| `PL_USER_SW[7:0]` | `ui_in[7:0]` | Normal core inputs |
| `PL_USER_LED[7:0]` | `uo_out[7:0]` | Core status/output |
| `PL_USER_PB[0]` | reset gate | Manual reset; wrapper defaults to active-low |
| `JA_tri_io[7:0]` | `uio_in/out/oe` | Runtime bidirectional pins |

During firmware loading, the PS claims the PMOD pins (which are then
high-impedance), selects its GPIO data for `ui_in`, and drives the parallel
loader strobe. After the ROM reports that firmware is starting, the wrapper
returns `uio` to PMOD JA. `BUTTON_ACTIVE_LOW` in the wrapper can be changed if
the board revision's button polarity differs from the default; verify polarity
against the board schematic before programming hardware.

The current generated block design in `fpga/vivado/msf_bd.tcl` configures
`pl_clk0` directly to 12 MHz and connects it to both the emulator's `core_clk`
and the wrapper's `ps_clk`. It does not use a Clocking Wizard. The 12 MHz rate
matches the boot ROM's UART timing for 115200 baud. Verify the actual clock
and all timing constraints in Vivado before relying on this configuration.

`fpga/vivado/msf_bd.tcl` is a Vivado 2024.1 block-design creation script, not
a complete build flow. If no project is open, it creates a default project
with hard-coded part and board-part values; if a project is already open, it
uses that project without checking its part or board preset. The required
`aup_zu3_io` RTL module must already be in the project. The script does not
add RTL or constraints, create the HDL wrapper, run synthesis/implementation,
or export a bitstream/XSA. See the
[current Tcl behavior and connections](./vivado-tcl-script-intent.md) before
running it. Obtain the matching AUP-ZU3 schematic/manual from the board vendor
to check pin assignments and PS/DDR presets for the physical revision.

## Zynq PS firmware loading and protocol tests

The PS reaches the core through 32 EMIO GPIO bits (bank 3, PS GPIO 78-109),
driven with the standalone `XGpioPs` driver in `fpga/ps/common`. Bits
`[10:0]` keep their original meaning (boot byte, PS ownership, reset release,
parallel strobe) and `uo_out` is still returned on `[10:3]`. The wrapper also
returns `uio_out`/`uio_oe`, lets the PS drive `uio_in`, and has a step mode in
which the core clock (through a `BUFGCE`) advances one cycle per PS request.
The full bit map is in [`fpga/ps/README.md`](../../fpga/ps/README.md).

The loader (`fpga/ps/loader/src/main.c`) selects parallel mode
(`ui_in[1] = 1`, with direct boot disabled), holds reset while it configures
GPIO, then sends the existing protocol: `A5`, a `W` frame containing
little-endian firmware words, CRC-8/0x07, and a `G` frame. It waits for every
alternating strobe acknowledgement and for the ROM's starting-firmware status
before releasing PMOD ownership. Frames are streamed, so there is no frame
buffer. No change to `test/bootloader.s` or the shared core is needed.
Generate its `firmware_image.h` from assembly with:

```sh
python3 fpga/ps/loader/generate_image.py firmware/thread_demo.s
```

The `--address` and `--output` options select another load address/header
location. The image must fit the 512-word address space, with code at or above
word 128 unless the application is intentionally loading data RAM.

The protocol-test application (`fpga/ps/hil`) runs every firmware test of
`test/test_firmware.py` on the FPGA core in step mode, with C ports of the
cocotb protocol models (UART, SPI slave, I2C slave, USB low speed, 10BASE-T)
acting as the other end of each bus. The same C runs against the RTL in
simulation with `python fpga/tests/run_ps_hil.py`. Build steps and the test
list are in [`fpga/ps/README.md`](../../fpga/ps/README.md).

## Porting and implementation procedure

### 1. Preserve and characterize the ASIC behavior

1. Keep the ASIC source tree and ASIC flow unchanged; `fpga/rtl` is the FPGA
   build's source set, listed in `fpga/rtl/sources.f`.
2. Record source lists, tool versions, board-part revision, clock, reset
   polarity, and test commands for each release.
3. Run the existing RTL regression baseline and preserve cycle counts,
   registered memory reads, input synchronization, reset behavior, and
   peripheral status semantics.

### 2. Use a matching FPGA memory implementation

1. Use the FPGA `tt_um_msf_sram` implementation in `fpga/rtl`, not the IHP
   macro instantiation in `src/tt_um_msf_sram.v`.
2. Preserve the SRAM interface: 512 x 16 words; continuously registered
   port-A fetch reads; enabled port-B reads/writes; one-cycle read latency;
   read-data hold while port B is inactive; full-word writes.
3. Keep memory initialization synthesizable for FPGA BRAM configuration. Do
   not add a reset loop to inferred BRAM. Same-address cross-port
   read/write collisions are device-defined and must not be relied on by the
   core.
4. Retain the generated 128-word boot ROM and verify its contents survive
   synthesis.

### 3. Create and build the AUP-ZU3 design

1. Use Vivado 2024.1 for the current generated Tcl. Add the RTL sources in
   `fpga/rtl/sources.f` to the project before sourcing the script; the Tcl
   specifically requires `aup_zu3_io` to resolve as a module reference. Add
   `fpga/board/constraints/msf_aup_zu3.xdc` separately.
2. Source the block-design script in the Vivado Tcl console:

   ```tcl
   source {D:/path/to/repository/fpga/vivado/msf_bd.tcl}
   ```

   Use the equivalent repository path on Linux. The generated script creates
   and validates the `msf_zynq_system` BD. If no project is open, it creates
   its own default project using hard-coded part/board-part values. It does
   not accept an `.xpr` path via `-tclargs`.
3. Review the generated BD against
   [the documented current connections](./vivado-tcl-script-intent.md).
   The PS uses 32 EMIO GPIO bits; there is no AXI GPIO IP or HPM0 AXI master
   path. `pl_clk0` is set to 12 MHz and is connected directly to
   `aup_zu3_io_0/core_clk`.
4. Manually create/add the BD HDL wrapper, set it as the project top, and run
   synthesis, implementation, and bitstream generation. The current Tcl does
   not automate these steps or export an XSA. Confirm the actual PL clock,
   timing, absence of unresolved IHP macro modules, and 512 x 16 SRAM BRAM
   mapping in Vivado reports.
5. Confirm `PL_USER_PB[0]` polarity and PS/PL handoff behavior against the
   board revision, schematic, and physical hardware.
6. Export the XSA and build the PS applications in Vitis as described in
   [`fpga/ps/README.md`](../../fpga/ps/README.md). Run the protocol-test
   application first: it exercises the loader path, step mode and every
   firmware demo before anything is connected to PMOD JA.

### 4. Verify behavior and hardware

1. Run `fpga/tests/run_sram_test.sh` to check the FPGA RAM's registered
   latency, writes, initialized contents, and disabled-port hold behavior.
   Run `fpga/tests/run_aup_zu3_io_test.sh` to check PS-vs-PMOD ownership,
   status, reset handoff and step mode (one core edge per request).
   Run `fpga/tests/run_ps_loader_compile_test.sh` to compile-check both
   bare-metal applications against the generated headers and stubs of the
   `XGpioPs` driver (classic and SDT flows).
   Run `python fpga/tests/run_ps_hil.py` to run the C protocol tests against
   the RTL through the same EMIO interface the board uses.
2. Run the existing RTL protocol/core tests against the FPGA source list.
   Require the same results as the ASIC baseline for boot, ISA, peripherals,
   threads, and firmware.
   From the repository root, for example:

   ```sh
   FPGA_RTL="$PWD/fpga/rtl"
   (cd test && make SRC_DIR="$FPGA_RTL")
   ```

   This replaces only the source directory; the ASIC tests and sources remain
   unchanged.
3. On the board, run the protocol-test application (reset, parallel ACK
   toggling, valid/invalid frames, step mode and every firmware demo), then
   the loader for PMOD ownership handoff.
4. Test representative runtime peripherals through PMOD JA. Include
   electrical checks for the PMOD's 3.3 V logic and connected peripheral before
   driving pins.
5. Record utilization, timing, and any board-only restrictions from the
   actual Vivado reports. Do not claim a successful board build based only on
   RTL simulation.

### 5. Keep the build reproducible

1. Keep the RTL, board wrapper, board-specific XDC, Vivado Tcl, PS loader, and
   firmware-image generator in version control.
2. Vivado/Vitis CI requires a licensed tool installation and an automated
   setup that adds the required sources before creating the block design.
   The current generated Tcl alone is not a complete CI build.
3. Keep ASIC tests and hardening/sign-off independent so FPGA changes cannot
   silently regress the tapeout target.

## Completion criteria

The port is complete when Vivado produces a bitstream from the board's
verified PS/DDR configuration, confirms BRAM mapping and timing at the
intended core clock, and reports no IHP macro dependencies; the chosen PS
software interface reliably programs the core through the existing parallel
boot protocol; runtime PMOD handoff is safe; and RTL/hardware tests pass
without weakening the shared behavioral contract.

## Risks and open validation

* The supplied PDFs and XDC pin maps need to be checked against the physical
  board revision and the installed Vivado board-part files.
* The direct 12 MHz `pl_clk0` configuration must be confirmed in Vivado timing
  reports, including the paths through the core clock's `BUFGCE`.
* Step mode is verified in simulation with a behavioural `BUFGCE`
  (`fpga/tests/sim_models/BUFGCE.v`); confirm on the board that each step
  request gives exactly one core cycle (every protocol test depends on it).
* The pushbutton's electrical polarity and reset deassertion behavior must be
  verified on the board; the RTL exposes `BUTTON_ACTIVE_LOW` for revision
  differences.
* Vivado/Vitis are not available in every environment. Synthesis, bitstream,
  XSA export, and PS application integration remain unverified until run with
  the licensed AMD tools and matching board preset.
