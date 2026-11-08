# SPDX-License-Identifier: Apache-2.0
"""I2S transmitter (firmware/i2s_tx.s) against a receiver written from Philips' I2S bus
specification: SD and WS taken on SCK's rising edge, MSB first, two's complement;
WS = 0 left, 1 right, changing one clock before the MSB of the word it announces."""
import math

import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, edges, level, samples

HALF, FRAMES = 30, 32
SINE = [round(16000 * math.sin(2 * math.pi * k / 16)) for k in range(16)]


@cocotb.test()
async def test_i2s_sine_cosine(dut):
    """32 stereo frames: left sin, right cos, every bit and WS edge in its slot."""
    await fw_boot(dut, "i2s_tx.s")
    ch = await watch_pins(dut, (FRAMES * 32 + 2) * 2 * HALF + 3000, pull=0)
    rises = [t for t, v in edges(ch, 0) if v == 1]
    assert all(b - a == 2 * HALF for a, b in zip(rises, rises[1:])), "SCK period"
    sd = [(level(ch, t) >> 2) & 1 for t in rises]
    ws = [(level(ch, t) >> 1) & 1 for t in rises]
    # the first rising edge is the lead-in slot (WS 0); words follow, 16 slots each
    words, cur, k = [], [], 1
    while k + 16 <= len(rises):
        w = [sd[k + i] for i in range(16)]
        # this word's WS on its first 15 slots, the next word's on its last
        chan = ws[k]
        assert all(ws[k + i] == chan for i in range(15)), f"WS inside a word at slot {k}"
        assert ws[k - 1] == chan, "WS did not lead the MSB by a slot"
        v = int("".join(map(str, w)), 2)
        words.append((chan, v - 0x10000 if v & 0x8000 else v))
        k += 16
    assert len(words) == 2 * FRAMES, len(words)
    want = []
    for i in range(FRAMES):
        want += [(0, SINE[i % 16]), (1, SINE[(i + 4) % 16])]
    assert words == want, words[:6]
    t1 = rises[-1] + 4 * HALF
    lines = []
    for i in range(FRAMES):
        lines += [f"Left channel: {SINE[i % 16] & 0xFFFF:08x}",
                  f"Right channel: {SINE[(i + 4) % 16] & 0xFFFF:08x}"]
    save_trace("i2s_tx", samples(ch, 0, t1, 2), "i2s:sck=0:ws=1:sd=2", "i2s", lines,
               {"pin": 2, "hold": 30}, rate=6_000_000)
