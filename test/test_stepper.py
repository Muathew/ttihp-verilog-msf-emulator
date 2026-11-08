# SPDX-License-Identifier: Apache-2.0
"""Stepper moves (firmware/stepper.s) against a STEP/DIR driver written from Allegro's
A4988 data sheet (a step on STEP's rising edge, in the direction DIR shows; STEP high
and low at least 1 us; DIR set up 200 ns before the edge and held 200 ns after), and
the ramp against D. Austin's recurrence, c(n) = c(n-1) - 2 c(n-1) / (4n + 1) from
c(0) = 2 ms down to 250 us, then the same intervals backwards."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, edges, level, samples

STEP, DIR = 0x01, 0x02
STEPS, C0, C_MIN = 100, 24000, 3000


def ramp():
    c, n, r = C0, 1, []
    while True:
        r.append(c)
        if c <= C_MIN or len(r) == 48:
            return r
        c -= (2 * c) // (4 * n + 1)
        n += 1


def intervals(n):
    r = ramp()
    k = min(len(r), n // 2)
    return r[:k] + [r[k - 1]] * (n - 2 * k) + r[:k][::-1]


@cocotb.test()
async def test_stepper_moves(dut):
    """100 steps forward on the ramp, 50 back: every interval to the cycle."""
    await fw_boot(dut, "stepper.s")
    total = sum(intervals(STEPS)) + sum(intervals(STEPS // 2)) + 2 * 60000 + 5000
    ch = await watch_pins(dut, total, pull=0)
    e = edges(ch, 0)
    rises = [t for t, v in e if v == 1]
    falls = [t for t, v in e if v == 0]
    assert len(rises) == len(falls) == STEPS + STEPS // 2, (len(rises), len(falls))
    pos, dirs = 0, edges(ch, 1)
    for r, f in zip(rises, falls):
        assert f - r == 24, f"STEP high {f - r} cycles"
        assert all(abs(t - r) >= 3 for t, _ in dirs), "DIR changed within 200 ns of a step"
        pos += 1 if level(ch, r) & DIR else -1
    assert pos == STEPS - STEPS // 2, pos
    gaps = [b - a for a, b in zip(rises, rises[1:])]
    m1, m2 = intervals(STEPS), intervals(STEPS // 2)
    assert gaps[:STEPS - 1] == m1[:-1], "the first move's intervals"
    assert gaps[STEPS:] == m2[:-1], "the second move's intervals"
    assert gaps[STEPS - 1] == m1[-1] + 2 * 60000, "the pause"
    t1 = rises[-1] + 30000
    save_trace("stepper", samples(ch, 0, t1, 12), "stepper_motor:step=0:dir=1", "stepper_motor=position",
               # (sigrok gives a position at the step after it: not the last, 50)
               [f"{p} steps" for p in list(range(1, STEPS + 1)) + list(range(STEPS - 1, STEPS // 2, -1))],
               {"pin": 0, "hold": 300}, rate=1_000_000)
