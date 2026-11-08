# SPDX-License-Identifier: Apache-2.0
"""DALI control device (firmware/dali.s) against control gear written from IEC 62386-101
as the DALI manual and NXP AN10760 describe it: 1200 bit/s bi-phase, a 1 low then high,
half bits of 416.67 us; a start bit (1), the address and data bytes MSB first, two stop
bits of idle; at least 22 Te of idle between forward frames."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, edges, level, samples

TE = 5000
FRAMES = [0xFEFE, 0x0B05, 0xFF00, 0x0B90]


@cocotb.test()
async def test_dali_forward_frames(dut):
    """Four forward frames, every half bit to the cycle, 24 Te apart."""
    await fw_boot(dut, "dali.s")
    ch = await watch_pins(dut, len(FRAMES) * (34 * TE + 120000) + 2000)
    e = edges(ch, 0)
    falls = [t for t, v in e if v == 0]
    got, starts, t = [], [], 0
    for f in falls:
        if f < t:
            continue
        # a start bit: low for the first half, high for the second
        starts.append(f)
        halves = [level(ch, f + TE // 2 + k * TE) & 1 for k in range(34)]
        bits = []
        for k in range(17):
            a, b = halves[2 * k], halves[2 * k + 1]
            assert a != b, f"frame at {f}: half bits {2 * k}, {2 * k + 1} equal"
            bits.append(b)
        assert bits[0] == 1, "start bit"
        got.append(int("".join(map(str, bits[1:])), 2))
        # the stop bits: idle (high) for 4 Te after the last bit
        assert all(level(ch, f + 34 * TE + k * TE // 2) & 1 for k in range(8)), "stop bits"
        t = f + 34 * TE + 4 * TE
    assert got == FRAMES, [hex(g) for g in got]
    for a, b in zip(starts, starts[1:]):
        assert b - (a + 34 * TE) >= 22 * TE, f"{(b - a - 34 * TE) / TE} Te between frames"
    # every edge on the half-bit grid of its frame
    for c, _ in e:
        s = max(x for x in starts if x <= c)
        assert (c - s) % TE == 0, f"an edge {(c - s) % TE} cycles off the grid"
    t1 = starts[-1] + 40 * TE
    # sigrok's reading: what each frame means (IEC 62386-102's command names)
    lines = ["Startbit: 1", "Arc Power Level", "BROADCAST", "Arc Power Level: 254",
             "Startbit: 1", "Command", "YBit: 0", "Short address: 5", "Command: 5 (Recall Maximum Level)",
             "Startbit: 1", "Command", "BROADCAST", "Command: 0 (Immediate Off)",
             "Startbit: 1", "Command", "YBit: 0", "Short address: 5", "Command: 144 (Query Status)"]
    save_trace("dali", samples(ch, 0, t1, 60), "dali:dali=0", "dali=fields", lines,
               {"pin": 0, "hold": 100}, rate=200_000)
