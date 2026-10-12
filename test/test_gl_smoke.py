# SPDX-License-Identifier: Apache-2.0
"""
Gate-level smoke test for the msfcore protocol emulator.

In GL mode the netlist is synthesized (flattened), so internal hierarchy
(u_sram, u_ctrl, u_rf) is NOT accessible.  These tests only use top-level
ports (clk, rst_n, ena, ui_in, uio_in, uio_out, uio_oe) to verify:

  1. The netlist elaborates (SRAM macro resolved) and runs without hanging.
  2. Top-level outputs are clean (no X/Z propagation).
  3. Reset recovers cleanly.

Functional behaviour is covered by the RTL test suite; this GL smoke test
only confirms the hardened netlist is structurally sound.
This is the only test module run in GL mode (Makefile: GATES=yes).
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge


def _is_x(val):
    """Check if a cocotb LogicArray contains X or Z bits."""
    return not val.is_resolvable


@cocotb.test()
async def test_gl_elaboration_smoke(dut):
    """Reset the GL netlist, run 100 cycles, verify outputs are clean."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    # Reset: ena=1, ui_in=1 (direct SRAM boot -> JMP 128)
    dut.ena.value = 1
    dut.ui_in.value = 1
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1

    # Run 100 cycles -- the CPU will fetch from SRAM (all zeros after reset)
    # and execute NOPs, but the key check is that the sim doesn't hang
    # and outputs are defined.
    for _ in range(100):
        await RisingEdge(dut.clk)

    # Verify top-level outputs are clean (no X/Z)
    uio_out = dut.uio_out.value
    uio_oe = dut.uio_oe.value
    uo_out = dut.uo_out.value

    assert not _is_x(uio_out), f"uio_out is X after GL reset (val={uio_out})"
    assert not _is_x(uio_oe), f"uio_oe is X after GL reset (val={uio_oe})"
    assert not _is_x(uo_out), f"uo_out is X after GL reset (val={uo_out})"

    dut._log.info(
        "GL smoke: uio_out=0x%02X uio_oe=0x%02X uo_out=0x%02X -- no X/Z",
        int(uio_out), int(uio_oe), int(uo_out),
    )


@cocotb.test()
async def test_gl_reset_recover(dut):
    """Reset the GL design twice and verify outputs stay clean."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    for attempt in (1, 2):
        dut.ena.value = 1
        dut.ui_in.value = 1
        dut.uio_in.value = 0
        dut.rst_n.value = 0
        await ClockCycles(dut.clk, 10)
        dut.rst_n.value = 1

        for _ in range(50):
            await RisingEdge(dut.clk)

        assert not _is_x(dut.uio_out.value), f"Reset {attempt}: uio_out is X"
        assert not _is_x(dut.uio_oe.value), f"Reset {attempt}: uio_oe is X"
        dut._log.info("GL reset-recover: attempt %d OK", attempt)
