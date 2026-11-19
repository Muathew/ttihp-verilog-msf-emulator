# SPDX-License-Identifier: Apache-2.0
"""run_gl_sdf.py's check that the SDF reached the cells: the clock arrives at an SRAM
macro's clock pin later than at the top (the clock tree's buffers and wires). With no
annotation it arrives in the same instant, and the gate-level tests would pass with zero
delays while claiming more. Needs the netlist run_gl_sdf.py writes (u_sram_sram0)."""
import cocotb
from cocotb.triggers import RisingEdge
from cocotb.utils import get_sim_time

from msf_tb import CLK_NS, reset, start_clock


@cocotb.test()
async def test_sdf_delays_applied(dut):
    """The clock reaches u_sram_sram0.A_CLK at least 0.2 ns after tb.clk."""
    start_clock(dut)
    await reset(dut, ui_in=1)
    sclk = dut.user_project.u_sram_sram0.A_CLK
    lags = []
    for _ in range(4):
        await RisingEdge(dut.clk)
        t = get_sim_time("ps")
        await RisingEdge(sclk)
        lags.append((get_sim_time("ps") - t) / 1000.0)
    dut._log.info("clock period %d ns; clk -> u_sram_sram0.A_CLK: %s ns", CLK_NS, lags)
    assert min(lags) > 0.2, "no clock-tree delay: the SDF was not applied"
