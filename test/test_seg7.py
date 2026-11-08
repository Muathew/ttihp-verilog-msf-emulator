# SPDX-License-Identifier: Apache-2.0
"""Seven-segment counter (firmware/seg7.s) read by a viewer written from the Code B
font of Maxim's MAX7219 data sheet: which digit each set of lit segments is,
and how long it stays: 0 to 9, 10 ms each."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, samples

SEGS = {"0": "abcdef", "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg", "5": "acdfg",
        "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcdfg"}


def lit(v):
    return "".join(c for i, c in enumerate("abcdefg") if v >> i & 1)


@cocotb.test()
async def test_seg7_count(dut):
    """0 to 9, each for exactly 10 ms, no decimal point."""
    await fw_boot(dut, "seg7.s")
    ch = await watch_pins(dut, 10 * 120000 + 2000, pull=0)
    shown = [(t, lit(p)) for t, p in ch if p]
    names = {v: k for k, v in SEGS.items()}
    assert [names.get(s, s) for _, s in shown] == list("0123456789"), shown
    assert all(p >> 7 == 0 for _, p in ch), "the decimal point"
    assert all(b[0] - a[0] == 120000 for a, b in zip(shown, shown[1:]))
    t1 = shown[-1][0] + 20000
    save_trace("seg7", samples(ch, 0, t1, 120), "seven_segment:a=0:b=1:c=2:d=3:e=4:f=5:g=6",
               # sigrok names a digit as it ends: the dark display before the 0 first,
               # and not the 9, still lit when the trace stops
               "seven_segment", [""] + list("012345678"), {"pin": 0, "hold": 300}, rate=100_000)
