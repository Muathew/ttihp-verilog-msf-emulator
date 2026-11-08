# SPDX-License-Identifier: Apache-2.0
"""Wiegand 26-bit (firmware/wiegand.s) against a panel written from HID's H10301
format and the SIA Wiegand timing: pulses 20-100 us, at least 200 us apart, bit 1
even parity over bits 2-13, bit 26 odd parity over bits 14-25."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, edges, samples

US = 12
FAC, CARD = 0x5A, 0x04D2


def frames_of(ch):
    """Low pulses on D0 (a 0) and D1 (a 1) -> frames, split at gaps over 3 ms."""
    pulses = []
    for pin, bit in ((0, 0), (1, 1)):
        e = edges(ch, pin)
        for (t0, v0), (t1, v1) in zip(e, e[1:]):
            if v0 == 0 and v1 == 1:
                pulses.append((t0, t1 - t0, bit))
    pulses.sort()
    frames, cur, last = [], [], None
    for t, w, b in pulses:
        assert 20 * US <= w <= 100 * US, f"pulse of {w / US:.1f} us"
        if last is not None:
            assert t - last >= 200 * US, f"pulses {(t - last) / US:.0f} us apart"
            if t - last > 3000 * US:
                frames.append(cur)
                cur = []
        cur.append(b)
        last = t
    frames.append(cur)
    return frames


@cocotb.test()
async def test_wiegand_26(dut):
    """Two frames of facility 0x5A, card 1234, with both parities."""
    await fw_boot(dut, "wiegand.s")
    ch = await watch_pins(dut, 2 * 26 * 12000 + 80000)
    frames = frames_of(ch)
    assert len(frames) == 2 and frames[0] == frames[1], frames
    b = frames[0]
    assert len(b) == 26, len(b)
    val = lambda bits: int("".join(map(str, bits)), 2)
    assert val(b[1:9]) == FAC and val(b[9:25]) == CARD, (val(b[1:9]), val(b[9:25]))
    assert sum(b[0:13]) % 2 == 0, "even parity over bits 1-13"
    assert sum(b[13:26]) % 2 == 1, "odd parity over bits 14-26"
    t1 = ch[-1][0] + 2000 * US
    save_trace("wiegand", samples(ch, 0, t1, 12), "wiegand:d0=0:d1=1:bitwidth_ms=1",
               "wiegand=state", [f"26 bits {h10301(FAC, CARD)}"] * 2, {"pin": 1, "hold": 300},
               rate=1_000_000)


def h10301(fac, card):
    """The 26 bits as text: even parity, facility, card, odd parity."""
    data = f"{fac:08b}{card:016b}"
    ep = data[:12].count("1") % 2
    op = 1 - data[12:].count("1") % 2
    return f"{ep}{data}{op}"
