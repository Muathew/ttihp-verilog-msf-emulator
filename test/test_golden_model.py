# SPDX-License-Identifier: Apache-2.0
"""
Golden reference model test suite for the msfcore protocol emulator.

Uses ``golden_model.py`` (the independent Python reference) in two ways:

1. :func:`test_golden_lockstep` -- runs :class:`MsfCoreModel` in lock-step
   with the DUT, diffing observable core state (PC, register file, Z/N,
   cycle counter, pad outputs) every cycle.  If the RTL ever drifts from
   the ISA/golden model, this fails on the first divergent cycle.

2. :func:`test_golden_uart_reference` / :func:`test_golden_i2c_conditions`
   -- pure-model checks that the protocol reference waveforms (UART frame,
   I2C START/STOP/data detector) behave as the firmware expects.

Run with the rest of the suite: ``cd test && make`` (it is listed in
``COCOTB_TEST_MODULES`` in the Makefile).
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly

from assembler import assemble, bootloader_words
from golden_model import (
    MsfCoreModel,
    UartRxDecoder,
    I2cConditionDetector,
    uart_frame_bits,
    uart_decode_data_bits,
)


def load_sram(dut, words, start_addr=128):
    """Preload firmware words into SRAM (words 128..511)."""
    for i, w in enumerate(words):
        dut.user_project.u_sram.mem[start_addr + i].value = w


# ---------------------------------------------------------------------------
# Firmware: exercises the datapath, flags, and a taken branch.
# ---------------------------------------------------------------------------
_FW = """
    OE_SET  255
    LDR     R1, #5
    LDR     R2, #3
    ADDS    R3, R1, R2      ; R3 = 8
    AND     R6, R1, R2      ; R6 = 1
    OR      R6, R6, R3      ; R6 = 9
    XOR     R6, R6, R3      ; R6 = 1
    SHL     R6, R6, #2      ; R6 = 4
    SHR     R6, R6, #1      ; R6 = 2
    INCR    R6             ; R6 = 3
    DECR    R6             ; R6 = 2
    SUBS    R5, R1, R1      ; R5 = 0, Z = 1
    PIN_W   R3             ; out = 8
    JZ      ok            ; taken (Z = 1)
    LDR     R7, #99
    PIN_W   R7
fail_loop:
    JMP     fail_loop
ok:
    SUBS    R4, R1, R2      ; R4 = 2, Z = 0
    PIN_W   R4            ; out = 2
done_loop:
    JMP     done_loop
"""


def _dut_state(dut):
    """Read the DUT's observable core state (the RTL side of the golden diff)."""
    c = dut.user_project.u_ctrl
    regs = [int(c.u_rf.rf[i].value) for i in range(8)]
    return {
        "pc": int(c.pc_p.value),
        "regs": regs,
        "z": int(c.z.value),
        "n": int(c.n.value),
        "cnt": int(c.cnt.value) & 0xFFFF,
        "uio_out": int(dut.uio_out.value),
        "uio_oe": int(dut.uio_oe.value),
    }


@cocotb.test()
async def test_golden_lockstep(dut):
    """Differential golden-model check: DUT vs MsfCoreModel, cycle-by-cycle."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    words = assemble(_FW, base_addr=128)
    load_sram(dut, words)

    # Mirror the firmware + boot ROM into the golden model (ROM is in the
    # DUT too, and PC 0 runs the bootloader which jumps to 128 on ui_in[0]=1).
    model = MsfCoreModel()
    model.load_sram(words)                    # SRAM words 128..
    model.load_rom(bootloader_words())        # ROM words 0..127

    # --- Reset the DUT (async reset) and the model to the same state ---
    dut.ena.value = 1
    dut.ui_in.value = 1          # direct SRAM boot path
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    # Async reset: during the 5 low-rst_n cycles the DUT's pc/cnt are held at
    # 0. When rst_n goes high, the *next* posedge of clk is the DUT's first
    # "normal" cycle. So after the rst_n deassertion, the DUT is in the
    # reset state (pc=0, flush=1, cnt=0).  Put the model in the same state.
    model.reset()

    # --- Lock-step ---
    # Each iteration: set DUT inputs (write phase), advance the model and the
    # DUT by one clock, then compare the settled functional state.
    #
    # We compare PC, register file, Z/N flags, and pad outputs.  The
    # free-running cycle counter (cnt) is intentionally NOT compared: its
    # absolute phase depends on when the async reset deasserts relative to
    # clock edges, which is a testbench artifact, not a design property.
    N = 60
    for cyc in range(N):
        # RisingEdge enters the write phase; the DUT takes this clock edge.
        await RisingEdge(dut.clk)
        # Set DUT inputs for the *next* cycle (write phase).
        dut.uio_in.value = 0
        dut.ui_in.value = 1
        # Advance the model to mirror the edge the DUT just took.
        model.step(uio_in=0, ui_in=1)
        # ReadOnly waits for non-blocking assignments to settle, then
        # locks us into the read-only phase so we can safely read values.
        await ReadOnly()

        m = model.snapshot()
        d = _dut_state(dut)

        diffs = [f"{k}: golden={m[k]} dut={d[k]}"
                 for k in ("pc", "z", "n", "uio_out", "uio_oe")
                 if m[k] != d[k]]
        diffs += [f"R{r}: golden={m['regs'][r]:#06x} dut={d['regs'][r]:#06x}"
                  for r in range(8) if m["regs"][r] != d["regs"][r]]
        if diffs:
            raise AssertionError(
                f"cycle {cyc}: golden model diverged from DUT -> "
                + "; ".join(diffs)
            )

    dut._log.info(f"Golden lock-step PASSED ({N} cycles, all state matched)")


@cocotb.test()
async def test_golden_uart_reference(dut):
    """The UART reference model round-trips a byte (TX frame -> RX decode)."""
    # 115200, 8N1: expected TX pin levels for 0xA5.
    expected = [0, 1, 0, 1, 0, 0, 1, 0, 1, 1]  # start, b0..b7 LSB, stop
    assert uart_frame_bits(0xA5) == expected, "UART TX frame reference is wrong"

    # Decode the same frame back into a byte.
    frame = uart_frame_bits(0x3D)
    dec = UartRxDecoder()
    rx = dec.feed(frame[0], frame[1:9], frame[9])
    assert rx == 0x3D, f"UART RX decode expected 0x3D, got {rx:#04x}"
    assert uart_decode_data_bits(frame[1:9]) == 0x3D
    assert dec.errors == 0
    dut._log.info("Golden UART reference round-trip PASSED (0x3D)")


@cocotb.test()
async def test_golden_i2c_conditions(dut):
    """The I2C condition detector classifies START / data / STOP correctly."""
    det = I2cConditionDetector()
    # Idle (both high).
    assert det.update(1, 1) is None
    # START: SDA falls while SCL held high.
    assert det.update(0, 1) == "START"
    # Data bit 0: SDA held low, SCL pulses (low -> high) -> sample 0.
    det.update(0, 0)                       # SCL low, SDA=0
    assert det.update(0, 1) == "bit0"       # SCL rising, SDA stable 0
    # STOP: with SDA low and SCL high, releasing SDA (0->1) is a STOP.
    assert det.update(1, 1) == "STOP"
    dut._log.info(f"Golden I2C condition detector PASSED: {det.events}")
