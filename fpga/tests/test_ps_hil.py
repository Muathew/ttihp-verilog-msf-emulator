# SPDX-License-Identifier: Apache-2.0
"""The PS-side C protocol tests (fpga/ps/hil) run against the FPGA RTL.

The C code is built into a shared library with the lockstep EMIO HAL
(fpga/ps/sim/msf_emio_sim.c). Every PL clock this module hands it the
wrapper's ps_gpio_i and drives back the ps_gpio_o / ps_gpio_t it returns, so
the C that runs on the Zynq PS is what drives the RTL here. One cocotb test
per C test; run through fpga/tests/run_ps_hil.py.
"""
import ctypes
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

LIB = ctypes.CDLL(os.environ["MSF_PS_SIM_LIB"])
LIB.msf_sim_name.restype = ctypes.c_char_p
LIB.msf_sim_start.argtypes = [ctypes.c_char_p, ctypes.c_uint]
LIB.msf_sim_cycle.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                              ctypes.POINTER(ctypes.c_uint32)]
# PL clocks per PS GPIO register access (~170 ns at 12 MHz on the board)
ACCESS_CYCLES = int(os.environ.get("MSF_PS_ACCESS_CYCLES", "2"))


def _int(value):
    """X/Z read as 0 (only possible before the wrapper leaves reset)."""
    s = str(value)
    return int("".join(c if c in "01" else "0" for c in s), 2)


async def run_c_test(dut, name):
    cocotb.start_soon(Clock(dut.core_clk, 80, unit="ns").start())
    dut.core_resetn.value = 0
    dut.ps_gpio_o.value = 0
    dut.ps_gpio_t.value = 0xFFFFFFFF
    await ClockCycles(dut.core_clk, 10)
    dut.core_resetn.value = 1
    await ClockCycles(dut.core_clk, 2)

    assert LIB.msf_sim_start(name.encode(), ACCESS_CYCLES) == 0
    o, t = ctypes.c_uint32(), ctypes.c_uint32()
    driven = None
    clk, gpio_i = dut.core_clk, dut.ps_gpio_i
    while True:
        await RisingEdge(clk)
        done = LIB.msf_sim_cycle(_int(gpio_i.value), ctypes.byref(o), ctypes.byref(t))
        if (o.value, t.value) != driven:
            driven = (o.value, t.value)
            dut.ps_gpio_o.value, dut.ps_gpio_t.value = driven
        if done:
            break
    assert LIB.msf_sim_finish() == 0, f"C test {name} failed (see the msf-hil lines above)"


def _make(name):
    async def test(dut):
        await run_c_test(dut, name)
    test.__name__ = test.__qualname__ = f"test_{name}"
    return cocotb.test()(test)


for _i in range(LIB.msf_sim_count()):
    _name = LIB.msf_sim_name(_i).decode()
    globals()[f"test_{_name}"] = _make(_name)
