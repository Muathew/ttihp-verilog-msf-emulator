# SPDX-License-Identifier: Apache-2.0
"""SENT transmitter (firmware/sent.s) against a receiver written from the SAE J2716
descriptions in NXP's and Infineon's application notes: the tick is measured from the
56-tick synchronisation pulse; each nibble lasts 12 + value ticks between falling
edges, with a low of at least 4 ticks; status, six data nibbles, a CRC nibble; the
CRC is x^4 + x^3 + x^2 + 1, seed 0101, over the data nibbles and one more 0 nibble."""
import cocotb

from fw_tb import fw_boot
from pins import watch_pins, edges

FRAMES = [(0, 0x123, 0x800), (0, 0x456, 0x800), (0, 0x789, 0x7FF), (0, 0xABC, 0x801)]


def crc4(nibbles):
    """Polynomial division, MSB first, of the seed, the nibbles and a 0 nibble."""
    r = 0b0101
    for n in list(nibbles) + [0]:
        for i in (3, 2, 1, 0):
            top = r >> 3
            r = ((r << 1) | (n >> i) & 1) & 0xF
            if top:
                r ^= 0b1101
    return r


@cocotb.test()
async def test_sent_frames(dut):
    """Four frames, each nibble to the tick, each CRC right."""
    await fw_boot(dut, "sent.s")
    ch = await watch_pins(dut, len(FRAMES) * (56 + 8 * 27) * 36 + 2000)
    e = edges(ch, 0)
    falls = [t for t, v in e if v == 0]
    rises = [t for t, v in e if v == 1]
    periods = [b - a for a, b in zip(falls, falls[1:])] + [None]
    lows = [r - f for f, r in zip(falls, rises)]
    got, k = [], 0
    while k < len(falls) - 1:
        # a synchronisation pulse: 56 ticks; the tick from it
        tick = periods[k] / 56
        assert abs(tick - 36) < 0.5, f"tick {tick} cycles"
        nib = []
        for j in range(1, 9):
            p = periods[k + j] if k + j < len(periods) - 1 else None
            if p is None:                      # the last nibble: its end is the last rise + high
                break
            v = round(p / tick) - 12
            assert abs(p - (12 + v) * tick) < 0.5 and 0 <= v <= 15, f"a nibble of {p / tick} ticks"
            assert lows[k + j] >= 4 * tick, f"low {lows[k + j] / tick} ticks"
            nib.append(v)
        k += 9
        if len(nib) < 8:
            break
        status, data, crc = nib[0], nib[1:7], nib[7]
        assert crc == crc4(data), f"CRC {crc}, want {crc4(data)}"
        s1 = data[0] << 8 | data[1] << 4 | data[2]
        s2 = data[3] << 8 | data[4] << 4 | data[5]
        got.append((status, s1, s2))
    # every frame but the last is closed by the next sync pulse's falling edge
    assert got == FRAMES[:len(got)] and len(got) == len(FRAMES) - 1, got
    assert all(low == 5 * 36 for low in lows), set(lows)
