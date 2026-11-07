#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pin finder on every pin assignment: all 56 ordered pairs for SWD, and for JTAG
all 56 (TCK, TMS) pairs with a TDO and TDI chosen from what is left.

The RTL through cocotb runs a few thousand cycles a second, too slow for this, so it
runs on the ISA model (test/isa_model.py), written from the spec and checked against
the RTL by the random differential tests; the listen step, which needs the capture
engine the model does not have, is off. test/test_pinfind.py runs a sample of the
same on the RTL.

    python tools/pinfind_all.py                 exit status 1 on any miss
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = os.path.join(HERE, "..", "test")
sys.path.insert(0, TEST)
import assembler as asm  # noqa: E402
from isa_model import IsaModel  # noqa: E402

UBIT = 104
PULL = 0xFF
LIMIT = 3_000_000
V_N, HITS = 4, 112
M16 = 0xFFFF


class View:
    def __init__(self, model, wires):
        self.model, self.wires = model, wires
        self.out, self.oe = {}, {}

    def _virtual(self, v):
        return sum(((v >> real) & 1) << pin for pin, real in self.wires.items())

    def step(self, k, bench):
        self.out[k], self.oe[k] = self._virtual(bench.out[k]), self._virtual(bench.oe[k])
        mask, val = self.model.step(k, self)
        self.out.pop(k), self.oe.pop(k)
        rm = rv = 0
        for pin, real in self.wires.items():
            if (mask >> pin) & 1:
                rm |= 1 << real
                rv |= ((val >> pin) & 1) << real
        return rm, rv


class ModelBench:
    """The bench's rule on the ISA model: pad = chip where it drives, else the
    target's level, else the pull-up."""

    def __init__(self, words, target, defines=None):
        self.pads = {}
        self.target = target
        mem = {128 + i: w for i, w in enumerate(words)}
        self.m = IsaModel(asm.bootloader_words(), mem,
                          uio_pad=lambda c: self.pads.get(c, PULL), ui_pad=lambda c: 1)
        self.out, self.oe = {}, {}

    def run(self):
        m = self.m
        for k in range(LIMIT):
            m.step()
            m.trace.clear()
            self.out[k], self.oe[k] = m.out, m.oe
            mask, val = self.target.step(k, self)
            self.out.pop(k - 2, None), self.oe.pop(k - 2, None)
            ext = (PULL & ~mask) | (val & mask)
            self.pads[k] = ((m.out & m.oe) | (ext & ~m.oe)) & 0xFF
            self.pads.pop(k - 8, None)
            if m.uo == 0x55:
                return k
        raise AssertionError("the scan did not end")

    def ram(self, a):
        return self.m.mem[a]


def run_swd(words, clk, dio, Target):
    t = Target()
    b = ModelBench(words, View(t, {2: clk, 3: dio}))
    b.run()
    assert b.ram(V_N) == 1, (clk, dio, b.ram(V_N))
    want_id = Target.DPIDR
    got = (b.ram(HITS) & 0xFF, b.ram(HITS + 1) >> 8, b.ram(HITS + 2) | b.ram(HITS + 3) << 16)
    assert got == (clk, dio, want_id), (clk, dio, got)


def run_jtag(words, tck, tms, tdo, tdi, Chain):
    c = Chain(0x12345679)
    b = ModelBench(words, View(c, {2: tck, 5: tms, 4: tdo, 3: tdi}))
    b.run()
    assert b.ram(V_N) == 1, (tck, tms, b.ram(V_N))
    got = (b.ram(HITS) & 0xFF, b.ram(HITS + 1) >> 8, b.ram(HITS + 1) & 0xFF,
           b.ram(HITS + 2) | b.ram(HITS + 3) << 16)
    assert got == (tck, tms, tdo, 0x12345679), (tck, tms, tdo, tdi, got)


def main():
    from test_debugports import Chain, SwdTarget    # (needs cocotb importable)
    fw = os.path.join(HERE, "..", "firmware", "pinfind.s")
    swd = asm.assemble_file(fw, base_addr=128, defines={"LISTEN": 0, "JTAG_ON": 0, "FIRST": 1})
    jtag = asm.assemble_file(fw, base_addr=128, defines={"LISTEN": 0, "SWD_ON": 0, "FIRST": 1})
    rng = random.Random(1)
    n = 0
    for a in range(8):
        for b in range(8):
            if a == b:
                continue
            run_swd(swd, a, b, SwdTarget)
            rest = [x for x in range(8) if x not in (a, b)]
            rng.shuffle(rest)
            run_jtag(jtag, a, b, rest[0], rest[1], Chain)
            n += 1
            print(f"{n}/56 pair ({a}, {b}) ok", flush=True)
    print("every assignment found: 56 SWD pairs, 56 JTAG pairs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
