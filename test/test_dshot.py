# SPDX-License-Identifier: Apache-2.0
"""DShot150 output (firmware/dshot.s) against an ESC-side decoder written from
"DSHOT - the missing handbook": 16 bits MSB first, a bit 6.67 us long, high for 75 %
(a 1) or 37.5 % (a 0), read as a 1 when high for more than half the bit; 11 bits of
throttle, the telemetry request, and the checksum (v ^ v >> 4 ^ v >> 8) & 0xF."""
import cocotb

from fw_tb import fw_boot
from pins import watch_pins, edges

BIT = 80
FRAMES = [(0, 0), (0, 0), (48, 0), (1046, 1), (2047, 0)]


@cocotb.test()
async def test_dshot150_frames(dut):
    """Five frames 500 us apart, every bit and checksum right."""
    await fw_boot(dut, "dshot.s")
    ch = await watch_pins(dut, len(FRAMES) * 6000 + 2000, pull=0)
    e = edges(ch, 0)
    rises = [t for t, v in e if v == 1]
    falls = [t for t, v in e if v == 0]
    assert len(rises) == len(falls) == 16 * len(FRAMES), (len(rises), len(falls))
    got = []
    for k in range(len(FRAMES)):
        r, f = rises[16 * k:16 * k + 16], falls[16 * k:16 * k + 16]
        assert all(b - a == BIT for a, b in zip(r, r[1:])), "bit period"
        if k:
            assert r[0] - rises[16 * (k - 1)] == 6000, "frame period"
        highs = [b - a for a, b in zip(r, f)]
        assert all(h in (60, 30) for h in highs), highs
        bits = [1 if h > BIT // 2 else 0 for h in highs]
        word = int("".join(map(str, bits)), 2)
        v = word >> 4
        assert word & 0xF == (v ^ v >> 4 ^ v >> 8) & 0xF, f"checksum of {word:#06x}"
        got.append((v >> 1, v & 1))
    assert got == FRAMES, got
