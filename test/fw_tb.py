# SPDX-License-Identifier: Apache-2.0
"""Helpers for the firmware-level tests (firmware/*.s checked at the pins).

fw_boot   assemble a firmware file, preload it, reset the chip
Recorder  per-cycle samples of uio_out / uio_oe / uo_out
Bench     steps the chip one clock at a time against Python device models
          (the same lock-step idea as the FPGA C harness): each cycle it
          records the outputs, then every model sets the levels it drives,
          and the pads resolve as push-pull where the chip drives
          (uio_oe = 1) and as the models' levels elsewhere
events    decode the bus monitors' report stream into (code, data) pairs
"""
import os

import cocotb
from cocotb.triggers import RisingEdge, ReadOnly, Timer

from assembler import assemble_file
from msf_tb import start_clock, reset, load_words, uart_decode

FW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "firmware")
DONE = 0x55

_bg = []


def spawn(coro):
    """Background model task; cancelled by the next fw_boot."""
    t = cocotb.start_soon(coro)
    _bg.append(t)
    return t


async def fw_boot(dut, name, data=None, defines=None, uio_in=0):
    while _bg:
        _bg.pop().cancel()
    start_clock(dut)
    words = assemble_file(os.path.join(FW, name), base_addr=128, defines=defines)
    for a in range(1024):
        dut.user_project.u_sram.mem[a].value = 0
    load_words(dut, words, 128)
    for a, w in (data or {}).items():
        load_words(dut, [w], a)
    await reset(dut, ui_in=1, uio_in=uio_in)
    return words


class Recorder:
    """Per-cycle samples of (uio_out, uio_oe, uo_out)."""

    def __init__(self, dut):
        self.dut, self.out, self.oe, self.uo = dut, [], [], []
        self.task = spawn(self._run())

    async def _run(self):
        while True:
            await RisingEdge(self.dut.clk)
            await ReadOnly()
            self.out.append(int(self.dut.uio_out.value))
            self.oe.append(int(self.dut.uio_oe.value))
            self.uo.append(int(self.dut.uo_out.value))

    async def until_uo(self, pred, limit):
        for _ in range(limit):
            await RisingEdge(self.dut.clk)
            if self.uo and pred(self.uo[-1]):
                return
        raise AssertionError("timeout waiting for uo_out")


class Bench:
    """Lock-step pin bench. Cycle k: record the chip's outputs (out[k], oe[k],
    uo[k]), then each model's step(k, bench) returns the levels it drives as
    (mask, value); the pads become
        pad = chip value where uio_oe = 1, else the models' value, else `pull`
    and are written to uio_in (seen by the chip's synchronisers from the next
    edge). Open-drain lines (I2C): the chip only ever drives them low, and a
    model drives one (mask bit set, value 0) only while pulling it low, so
    with `pull` set the pad is low when either side pulls (wired AND)."""

    def __init__(self, dut, pull=0x00, models=()):
        self.dut, self.pull = dut, pull
        self.models = list(models)
        self.out, self.oe, self.uo, self.pad = [], [], [], []
        self.task = spawn(self._run())

    def add(self, model):
        self.models.append(model)
        return model

    @property
    def cycle(self):
        return len(self.out) - 1

    async def _run(self):
        d = self.dut
        while True:
            await RisingEdge(d.clk)
            await ReadOnly()
            out, oe = int(d.uio_out.value), int(d.uio_oe.value)
            self.out.append(out)
            self.oe.append(oe)
            self.uo.append(int(d.uo_out.value))
            await Timer(1, unit="ns")
            k = len(self.out) - 1
            ext = self.pull
            for m in self.models:
                mask, val = m.step(k, self)
                ext = (ext & ~mask) | (val & mask)
            pad = ((out & oe) | (ext & ~oe)) & 0xFF
            self.pad.append(pad)
            d.uio_in.value = pad

    async def run(self, cycles):
        target = len(self.out) + cycles
        while len(self.out) < target:
            await RisingEdge(self.dut.clk)

    async def until(self, pred, limit, what="condition"):
        for _ in range(limit):
            await RisingEdge(self.dut.clk)
            if self.out and pred(self):
                return
        raise AssertionError(f"timeout waiting for {what}")

    def bits(self, pin, src="pad"):
        """Per-cycle levels of one uio pin (pads, or the chip's uio_out)."""
        return [(v >> pin) & 1 for v in getattr(self, src)]


def edges(levels, start=0):
    """Indices k where levels[k] != levels[k-1]."""
    return [k for k in range(max(1, start), len(levels)) if levels[k] != levels[k - 1]]


def events(samples, bit_cycles, pin=0):
    """Monitor report stream (two bytes per event: code, data) -> list of
    (chr(code), data) pairs, from per-cycle samples of the report pin."""
    by = uart_decode(samples, bit_cycles, pin=pin)
    assert -1 not in by, f"report stream framing error: {by}"
    assert len(by) % 2 == 0, f"odd number of report bytes: {by}"
    return [(chr(by[i]), by[i + 1]) for i in range(0, len(by), 2)]
