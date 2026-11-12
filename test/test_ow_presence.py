# SPDX-License-Identifier: Apache-2.0
"""firmware/ow_presence.s: the device side of 1-Wire's reset and presence, made with the
v4 timed pins (isa.md 6.8). The test is the master: it pulls DQ low (open drain, pull-up)
for a reset, lets go, and measures the chip's presence pulse at the pins. The pulse must
start the same number of cycles after every release (the edge is the hardware's, on its
cycle) and last exactly PLEN; a short low (a slot) gets no answer. fw_boot's
DeadlineWatch checks both timed writes and both WAIT_CMPs against the deadline checker's
ranges on the RTL, so CI's cross-check (msf_timing.py --rtl-log) sees the timed-write
kind of deadline reached."""
import cocotb

from fw_tb import Bench, fw_boot

US = 12
DQP = 3
DQ = 1 << DQP
PDLY, PLEN = 30, 120            # as in the firmware
# A pad change in cycle E is seen by the program in E + 2 (the synchroniser: TS = E + 2,
# test_timed_pins.py), and a write applied in cycle T is on the pad from T + 1.
LAND = 3


class Master:
    """Pulls DQ low over each (start, cycles) interval, else leaves it to the pull-up."""

    def __init__(self, lows):
        self.lows = lows

    def step(self, k, bench):
        return (DQ, 0) if any(s <= k < s + n for s, n in self.lows) else (0, 0)


def chip_pulls(bench):
    return [(oe >> DQP) & 1 and not (out >> DQP) & 1 for out, oe in zip(bench.out, bench.oe)]


def pulses(levels, after, before):
    """(start, length) of each run of True in levels[after:before]."""
    out, k, before = [], after, min(before, len(levels))
    while k < before:
        if levels[k]:
            s = k
            while k < before and levels[k]:
                k += 1
            out.append((s, k - s))
        k += 1
    return out


@cocotb.test()
async def test_presence_is_exact_after_each_reset(dut):
    """Four resets of different lengths (480 to 483 us plus a few cycles, so the release
    falls on different phases of the program's loop) and one 60 us slot."""
    await fw_boot(dut, "ow_presence.s", uio_in=DQ)
    resets = [(300, 480 * US), (10_000, 481 * US + 1), (20_000, 482 * US + 2),
              (30_000, 483 * US + 3)]
    slot = (40_000, 60 * US)
    bench = Bench(dut, pull=DQ, models=[Master(resets + [slot])])
    await bench.run(44_000)
    chip = chip_pulls(bench)
    delays = []
    for s, n in resets:
        release = s + n
        got = pulses(chip, release, release + 4_000)
        assert len(got) == 1, f"reset at {s}: chip pulses {got}"
        start, length = got[0]
        delays.append(start - release)
        assert length == PLEN * US, f"reset at {s}: presence {length} cycles, not {PLEN * US}"
    assert len(set(delays)) == 1, f"presence delay varies with the release's phase: {delays}"
    assert delays[0] == PDLY * US + LAND, \
        f"presence starts {delays[0]} cycles after the release, expected {PDLY * US + LAND}"
    s, n = slot
    assert not pulses(chip, s, s + n + 4_000), "the chip answered a 60 us slot"
    # the master saw the presence: the line was low while the chip pulled it
    pad = bench.bits(DQP)
    release = resets[0][0] + resets[0][1]
    assert pad[release + delays[0] + 10] == 0 and pad[release + 10] == 1
