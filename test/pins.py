# SPDX-License-Identifier: Apache-2.0
"""Pins recorded on events, for protocols that run for milliseconds: the pads at
each change of the chip's uio outputs, with cycle numbers, instead of a sample a
cycle (fw_tb.Recorder), which is slow over a million cycles."""
import cocotb
from cocotb.triggers import First, Timer
from cocotb.utils import get_sim_time

from msf_tb import CLK_NS


def now():
    return round(get_sim_time("ns") / CLK_NS)


async def watch_pins(dut, cycles, pull=0xFF, uo=False):
    """[(cycle, pads)] for `cycles` cycles from now: pads = OUT where OE is set,
    `pull` elsewhere (push-pull lines idle as driven; open drain idles high with
    pull set). uo=True records uo_out instead."""
    t0 = now()
    ch = []

    def pads():
        if uo:
            return int(dut.uo_out.value)
        out, oe = int(dut.uio_out.value), int(dut.uio_oe.value)
        return ((out & oe) | (pull & ~oe)) & 0xFF

    async def watch():
        last = None
        while True:
            v = pads()
            if v != last:
                ch.append((now() - t0, v))
                last = v
            if uo:
                await dut.uo_out.value_change
            else:
                await First(dut.uio_out.value_change, dut.uio_oe.value_change)

    task = cocotb.start_soon(watch())
    await Timer(cycles * CLK_NS, unit="ns")
    task.cancel()
    return ch


def level(ch, t):
    """The pads at cycle t."""
    v = ch[0][1]
    for c, p in ch:
        if c > t:
            break
        v = p
    return v


def edges(ch, pin):
    """[(cycle, new level)] of one pin."""
    out, last = [], (ch[0][1] >> pin) & 1
    for c, p in ch[1:]:
        b = (p >> pin) & 1
        if b != last:
            out.append((c, b))
            last = b
    return out


def samples(ch, t0, t1, div=1):
    """One pad byte every `div` cycles from t0 to t1, for save_trace."""
    out, i, v = [], 0, ch[0][1]
    for t in range(t0, t1, div):
        while i < len(ch) and ch[i][0] <= t:
            v = ch[i][1]
            i += 1
        out.append(v)
    return out
