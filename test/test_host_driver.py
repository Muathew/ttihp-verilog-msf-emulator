# SPDX-License-Identifier: Apache-2.0
"""The demo-board driver (host/msf_host.py) run against the RTL (plan-v4 10.2).

The driver's generators are the code that runs on the board; here a runner carries out
their pin operations on the simulated chip instead of the RP2350's GPIOs. The clock is
edged by hand, one edge per cycle the driver asks for, as the board's stepped clock
does: between edges nothing moves, so a debug-view read cannot move the core, on the
board or here. Faults are planted in the runner (a stuck pin), where a board's wiring
would put them.
"""
import os
import sys

import cocotb
from cocotb.triggers import Timer

from assembler import assemble
from msf_tb import sram
from tools_path import boot_frame

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "host"))
import msf_host  # noqa: E402
from msf_host import Msf, MsfError  # noqa: E402

HALF_NS = 5

PROG = """
        OE_SET  0xFE
        LDR     R1, #0x42
        LDR     R2, #5
        LDR     R3, #0
loop:   ADDS    R3, R3, R1
        DECR    R2
        JNZ     loop
        PIN_W   R3
done:   JMP     done
"""
DONE = 128 + len(assemble(PROG.split("done:")[0], base_addr=128))
R3 = 5 * 0x42                              # 0x14A


class SimPins:
    """The board's pins on the simulated chip. ui_stuck: {bit: level} forces ui_in bits,
    as a solder bridge or a broken trace on the board would."""

    def __init__(self, dut, ui_stuck=None):
        self.dut, self.stuck = dut, ui_stuck or {}
        dut.ena.value = 1
        dut.clk.value = 0
        dut.rst_n.value = 1
        dut.ui_in.value = 0
        dut.uio_in.value = 0
        self.edges = 0

    def _ui(self, v):
        for bit, level in self.stuck.items():
            v = (v | (1 << bit)) if level else (v & ~(1 << bit))
        return v & 0xFF

    async def _edge(self):
        self.dut.clk.value = 1
        await Timer(HALF_NS, unit="ns")
        self.dut.clk.value = 0
        await Timer(HALF_NS, unit="ns")
        self.edges += 1

    async def do(self, op):
        d, kind = self.dut, op[0]
        if kind == "ui":
            d.ui_in.value = self._ui(op[1])
        elif kind == "uio":
            d.uio_in.value = op[1] & op[2]
        elif kind == "rst":
            d.rst_n.value = op[1]
        elif kind == "clk":
            for _ in range(op[1]):
                await self._edge()
        elif kind == "uo":
            await Timer(1, unit="ns")
            return int(d.uo_out.value)
        elif kind == "uio_out":
            await Timer(1, unit="ns")
            return int(d.uio_out.value)
        elif kind == "until":
            mask, val, limit = op[1], op[2], op[3]
            for n in range(limit + 1):
                await Timer(1, unit="ns")
                if int(d.uo_out.value) & mask == val:
                    return n
                await self._edge()
            return -1
        else:
            raise AssertionError(f"unknown pin operation {op!r}")
        return None


async def run(gen, pins):
    """msf_host.drive, for a runner that awaits."""
    val = None
    try:
        while True:
            val = await pins.do(gen.send(val))
    except StopIteration as e:
        return e.value


@cocotb.test()
async def test_frames_are_msf_boot_frames(dut):
    """The board sends byte for byte what tools/msf_boot.py sends from a PC."""
    words = assemble(PROG, base_addr=128)
    ours = [b for f in msf_host.frames(words, 128, run=True) for b in f]
    assert ours == boot_frame(words, addr=128, run=True)


@cocotb.test()
async def test_parallel_load_then_step_and_inspect(dut):
    """Load over the parallel port, reset into the firmware with the debug view on,
    single-step to a PC and read registers, pins and the counter: the bring-up's
    first stages, from the pins alone."""
    pins, m = SimPins(dut), Msf()
    words = assemble(PROG, base_addr=128)
    res = await run(m.load_parallel(words, run=False), pins)
    assert res == ["ok"]
    assert [sram(dut, 128 + i) for i in range(len(words))] == words

    await run(m.start(debug=True), pins)
    steps = await run(m.run_to(DONE, limit=400), pins)
    assert steps > 0, "never reached done"
    pc, thread, word = await run(m.last(), pins)
    assert (pc, thread, word) == (DONE, 0, words[DONE - 128])
    r = await run(m.regs(0), pins)
    assert r[1:4] == [0x42, 0, R3], r
    out, oe, _uo = await run(m.pins(), pins)
    assert (out & 0xFE, oe) == (R3 & 0xFE, 0xFE)

    # reading the view does not move the core; a step does
    c0 = await run(m.counter(), pins)
    assert await run(m.counter(), pins) == c0
    await run(m.step(10), pins)
    assert await run(m.counter(), pins) == (c0 + 10) & 0xFFFF


@cocotb.test()
async def test_parallel_load_and_go(dut):
    """With the go frame the firmware starts at once; its pins answer on uio_out."""
    pins, m = SimPins(dut), Msf()
    words = assemble(PROG, base_addr=128)
    assert await run(m.load_parallel(words, run=True), pins) == ["ok", "ok"]
    await run(m.step(200), pins)
    assert await run(_read_uio(), pins) & 0xFE == R3 & 0xFE


def _read_uio():
    v = yield ("uio_out",)
    return v


@cocotb.test()
async def test_uart_autobaud_load(dut):
    """The UART loader with autobaud at 40 and 24 cycles a bit, the host clocking the
    bits itself (stepped clock): the rate does not depend on the host's speed."""
    words = assemble(PROG, base_addr=128)
    for bit in (40, 24):
        pins, m = SimPins(dut), Msf()
        assert await run(m.load_uart(words, run=True, bit=bit), pins) == ["ok", "ok"], bit
        await run(m.step(200), pins)
        assert await run(_read_uio(), pins) & 0xFE == R3 & 0xFE, bit


@cocotb.test()
async def test_stuck_pin_is_reported_not_loaded(dut):
    """ui_in[5] stuck high (a bridge on the board): the ROM sees other bytes than were
    sent, so the load must fail with an error, never leave a wrong program running."""
    pins, m = SimPins(dut, ui_stuck={5: 1}), Msf()
    words = assemble(PROG, base_addr=128)
    try:
        await run(m.load_parallel(words, run=True, timeout=500), pins)
    except MsfError as e:
        dut._log.info(f"reported: {e}")
    else:
        raise AssertionError("a load through a stuck pin was reported as good")
