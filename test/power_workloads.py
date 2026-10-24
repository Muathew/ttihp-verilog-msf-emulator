# SPDX-License-Identifier: Apache-2.0
"""Workloads for tools/power.py: each boots a program through the parallel
loader on the gate-level netlist (tb_power.v) and records a VCD window of
WINDOW cycles in steady state. Not part of the test suite."""
import cocotb
from cocotb.triggers import ClockCycles

from assembler import assemble
from test_gl_firmware import gl_boot

WINDOW = 2080                       # two UART bytes at 104 cycles a bit


async def window(dut):
    await ClockCycles(dut.clk, 3000)
    dut.dump_go.value = 1
    await ClockCycles(dut.clk, WINDOW)
    dut.dump_go.value = 0
    await ClockCycles(dut.clk, 10)


@cocotb.test()
async def uart_tx(dut):
    """uart_tx.s sending, steady state."""
    msg = bytes((0x55, 0xA3, 0x0F, 0xC6) * 10)
    await gl_boot(dut, "uart_tx.s", data=[(0, list(msg) + [0])])
    await window(dut)


@cocotb.test()
async def threads(dut):
    """thread_demo.s: T0's square wave, T1 waiting for UART bytes."""
    await gl_boot(dut, "thread_demo.s", uio_idle=0x02)
    await window(dut)


@cocotb.test()
async def halt(dut):
    """A one-instruction loop: the core fetching and jumping every cycle."""
    await gl_boot(dut, assemble("h: JMP h", base_addr=128))
    await window(dut)
