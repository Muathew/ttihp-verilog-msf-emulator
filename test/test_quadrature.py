# SPDX-License-Identifier: Apache-2.0
"""Encoder emulator (firmware/quadrature.s) against a x4 quadrature counter written
from encoder data sheets (Broadcom HEDS-5500, US Digital E5): every edge of A or B is
a count, up when A leads B, down when B leads; both changing at once is an error;
the gated index is high exactly where the position is a multiple of the counts a
revolution, with A and B low."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, samples

CPR = 16
MOVES = [(40, 600), (-24, 1200), (8, 300)]
NEXT = {0b00: 0b01, 0b01: 0b11, 0b11: 0b10, 0b10: 0b00}     # (B << 1 | A), A leading


@cocotb.test()
async def test_quadrature_moves(dut):
    """40 counts up, 24 down, 8 up: each count on time, index at 0 and 16."""
    await fw_boot(dut, "quadrature.s")
    ch = await watch_pins(dut, sum(abs(n) * p for n, p in MOVES) + 3000, pull=0)
    pos, last, counts = 0, None, []
    prev_ab = ch[0][1] & 3
    for t, pads in ch[1:]:              # (ch[0]: before the program drives the pins)
        ab, z = pads & 3, (pads >> 2) & 1
        if ab != prev_ab:
            if NEXT[prev_ab] == ab:
                pos += 1
            elif NEXT[ab] == prev_ab:
                pos -= 1
            else:
                raise AssertionError(f"A and B changed together at {t}")
            counts.append((t, pos))
            prev_ab = ab
        assert z == (pos % CPR == 0), f"index {z} at position {pos}"
        assert not z or ab == 0, "the index outside A = B = 0"
    want, k = [], 0
    for n, p in MOVES:
        for _ in range(abs(n)):
            k += 1 if n > 0 else -1
            want.append((p, k))
    assert [c for _, c in counts] == [k for _, k in want], [c for _, c in counts]
    for (t0, _), (t1, _), (p, _) in zip(counts, counts[1:], want[1:]):
        assert t1 - t0 == p, f"a count after {t1 - t0} cycles, not {p}"
    assert counts[-1][1] == 24
    t1 = counts[-1][0] + 2000
    # graycode prints each count as it is left: 0, then all but the last
    lines = [str(c) for c in [0] + [c for _, c in counts][:-1]]
    save_trace("quadrature", samples(ch, 0, t1, 12), "graycode:d0=0:d1=1", "graycode=count", lines,
               {"pin": 0, "hold": 120}, rate=1_000_000)
