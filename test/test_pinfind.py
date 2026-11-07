# SPDX-License-Identifier: Apache-2.0
"""Pin finder (firmware/pinfind.s): the chip works out which of eight wires are a
UART, an SWD port and a JTAG port, against the same target models the debug probes
are tested with, with their pins moved to where the firmware cannot know them.

On the RTL through the pins, a few assignments. tools/pinfind_all.py runs every
ordered pair on the ISA model, which is checked against the RTL by the random tests."""
import random

import cocotb

from fw_tb import Bench, DONE, fw_boot
from msf_tb import uart_decode
from test_debugports import CLKP, Chain, DIOP, SwdTarget, TCKP, TDIP, TDOP, TMSP

UBIT = 104
PULL = 0xFF                            # the wires idle high unless driven


class View:
    """A model's window on the bench: out and oe with the wires renamed."""

    def __init__(self, model, wires):
        self.model, self.wires = model, wires   # {the model's pin: the real pin}
        self.out, self.oe = {}, {}

    def _virtual(self, v):
        return sum(((v >> real) & 1) << pin for pin, real in self.wires.items())

    def step(self, k, bench):
        self.out[k], self.oe[k] = self._virtual(bench.out[k]), self._virtual(bench.oe[k])
        mask, val = self.model.step(k, self)
        self.out.pop(k), self.oe.pop(k)
        rmask = rval = 0
        for pin, real in self.wires.items():
            if (mask >> pin) & 1:
                rmask |= 1 << real
                rval |= ((val >> pin) & 1) << real
        return rmask, rval


class UartWire:
    """A UART transmitter on a wire, repeating some bytes."""

    def __init__(self, pin, bit, data):
        self.pin, self.lv = pin, []
        for b in data:
            self.lv += [x for x in [0] + [(b >> i) & 1 for i in range(8)] + [1] for _ in range(bit)]
            self.lv += [1] * (bit // 2)

    def step(self, k, bench):
        return 1 << self.pin, self.lv[k % len(self.lv)] << self.pin


class Noise:
    """A wire that toggles at random, seeded."""

    def __init__(self, pin, seed=1, every=(3, 60)):
        self.pin, self.rng, self.every = pin, random.Random(seed), every
        self.level, self.next = 1, 0

    def step(self, k, bench):
        if k >= self.next:
            self.level ^= 1
            self.next = k + self.rng.randrange(*self.every)
        return 1 << self.pin, self.level << self.pin


class Stuck:
    def __init__(self, mask, level):
        self.mask, self.level = mask, level

    def step(self, k, bench):
        return self.mask, self.mask if self.level else 0


def parse(raw):
    """The report -> (uarts, swds, jtags, count)."""
    uarts, swds, jtags, i = [], [], [], 0
    while i < len(raw):
        c = chr(raw[i])
        if c == "U":
            uarts.append((raw[i + 1], raw[i + 2] | raw[i + 3] << 8))
            i += 4
        elif c == "S":
            swds.append((raw[i + 1], raw[i + 2], int.from_bytes(bytes(raw[i + 3:i + 7]), "little")))
            i += 7
        elif c == "J":
            jtags.append((raw[i + 1], raw[i + 2], raw[i + 3], raw[i + 4],
                          int.from_bytes(bytes(raw[i + 5:i + 9]), "little")))
            i += 9
        elif c == "E":
            return uarts, swds, jtags, raw[i + 1]
        else:
            raise AssertionError(f"unknown report byte {raw[i]:#x} at {i}: {raw}")
    raise AssertionError(f"no end in the report: {raw}")


async def find(dut, models, defines=None, limit=2500000):
    await fw_boot(dut, "pinfind.s", defines=defines, uio_in=PULL)
    bench = Bench(dut, pull=PULL)
    for m in models:
        bench.add(m)
    await bench.until(lambda b: b.uo and b.uo[-1] == DONE, limit, "the end of the scan")
    raw = uart_decode(bench.uo, UBIT, pin=0)
    assert -1 not in raw, "framing error in the report"
    return parse(raw)


@cocotb.test()
async def test_pinfind_swd_where_it_cannot_know(dut):
    """An SW-DP on pins picked by the test, the first and last pairs the scan tries
    among others: found, with its DPIDR, whichever way round they are."""
    for clk, dio in ((0, 1), (6, 3), (7, 6)):
        t = SwdTarget()
        v = View(t, {CLKP: clk, DIOP: dio})
        uarts, swds, jtags, n = await find(dut, [v], {"LISTEN": 0, "JTAG_ON": 0, "FIRST": 1})
        assert swds == [(clk, dio, SwdTarget.DPIDR)], (clk, dio, swds)
        assert n == 1 and not uarts and not jtags


@cocotb.test()
async def test_pinfind_jtag_where_it_cannot_know(dut):
    """A TAP on pins picked by the test: TCK, TMS, TDO and TDI all found, and the
    IDCODE read."""
    for tck, tms, tdo, tdi in ((0, 1, 2, 3), (5, 2, 7, 0)):
        c = Chain(0x12345679)
        v = View(c, {TCKP: tck, TMSP: tms, TDOP: tdo, TDIP: tdi})
        uarts, swds, jtags, n = await find(dut, [v], {"LISTEN": 0, "SWD_ON": 0, "FIRST": 1})
        assert jtags == [(tck, tms, tdo, tdi, 0x12345679)], (tck, tms, tdo, tdi, jtags)


@cocotb.test()
async def test_pinfind_nothing_connected(dut):
    """No false finds: all wires floating high, all stuck low, some toggling at random.
    The scan drives everything it may, and nothing answers."""
    cases = [[], [Stuck(0xFF, 0)], [Noise(3, seed=4), Noise(5, seed=9)]]
    for models in cases:
        uarts, swds, jtags, n = await find(dut, models,
                                           {"WINDOW": 6000, "PINS": 0x7E, "SWD_ON": 1, "JTAG_ON": 1})
        assert (uarts, swds, jtags, n) == ([], [], [], 0), (uarts, swds, jtags)


@cocotb.test()
async def test_pinfind_uart_and_leaves_it_alone(dut):
    """A UART talking on a wire: its pin and bit time are found by listening, and that
    wire is never driven after that."""
    pin, bit = 4, 104
    data = [0x48, 0x65, 0x6C, 0x55, 0x21, 0x0F, 0xA2, 0x7E, 0x31, 0x90, 0x0B, 0xD6]
    u = UartWire(pin, bit, data)
    await fw_boot(dut, "pinfind.s", defines={"WINDOW": 20000, "SWD_ON": 1, "JTAG_ON": 0,
                                             "PINS": 0x1C}, uio_in=PULL)
    bench = Bench(dut, pull=PULL)
    bench.add(u)
    await bench.until(lambda b: b.uo and b.uo[-1] == DONE, 2000000, "the end of the scan")
    raw = uart_decode(bench.uo, UBIT, pin=0)
    uarts, swds, jtags, n = parse(raw)
    assert uarts == [(pin, bit)], uarts
    assert not any(oe >> pin & 1 for oe in bench.oe), "it drove a wire that was moving"
    assert any(oe & 0x08 for oe in bench.oe), "it did not try the quiet wires"


@cocotb.test()
async def test_pinfind_clock_is_no_uart(dut):
    """A square wave is a stream of 0x55 to a UART receiver, whatever the rate: it
    must not be reported, and it still counts as being driven."""
    class Clock:
        def step(self, k, bench):
            return 1 << 2, ((k // 50) & 1) << 2
    uarts, swds, jtags, n = await find(dut, [Clock()],
                                       {"WINDOW": 6000, "SWD_ON": 0, "JTAG_ON": 0})
    assert not uarts, uarts
