# SPDX-License-Identifier: Apache-2.0
"""Philips RC-5 remote (firmware/ir_rc5.s), carrier and all, against a receiver
written from SB-Projects' and Vishay's descriptions: 36 kHz at a third duty,
half-bits of 889 us (32 carrier periods), a 1 is space then burst, a 0 burst then
space, S1 = S2 = 1, toggle, 5 address bits, 6 command bits, MSB first, a held key
repeating every 113.778 ms. sigrok's ir_rc5 reads demodulated light, so the trace
given to it is the envelope an IR receiver module would put out (low while the
carrier is there), made here from the chip's carrier."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, edges

CLK = 12_000_000
ADDR, CMD, TOG = 0x05, 0x35, 0


def near(x, want, tol):
    return abs(x - want) <= tol * want


@cocotb.test()
async def test_ir_rc5_held_key(dut):
    """Two frames of address 5, command 0x35, 113.778 ms apart."""
    await fw_boot(dut, "ir_rc5.s")
    ch = await watch_pins(dut, 1_700_000, pull=0)
    e = edges(ch, 0)
    rises = [t for t, v in e if v == 1]
    falls = [t for t, v in e if v == 0]
    for r, f in zip(rises, falls):
        assert near(f - r, CLK / 36000 / 3, 0.05), f"carrier on {f - r} cycles"
    for a, b in zip(rises, rises[1:]):
        if b - a < 2 * CLK / 36000:
            assert near(CLK / (b - a), 36000, 0.01), f"carrier at {CLK / (b - a):.0f} Hz"
    # the envelope: carrier there or not, then half-bits from each frame's first burst
    bursts, cur = [], None
    for r, f in zip(rises, falls):
        if cur and r - cur[1] < 2 * CLK / 36000:
            cur[1] = f
        else:
            if cur:
                bursts.append(tuple(cur))
            cur = [r, f]
    bursts.append(tuple(cur))
    half = 32 * CLK / 36000
    frames, start = [], None
    for s, f in bursts:
        if start is None or s - start > 40 * half:
            frames.append([])
            start = s - half            # the first bit is a 1: its first half is space
        frames[-1].append((s, f))
    assert len(frames) == 2, len(frames)
    decoded = []
    for fr in frames:
        t0 = fr[0][0] - 32 * 333
        lit = lambda t: any(s <= t < f for s, f in fr)
        halves = [lit(t0 + (k + 0.5) * half) for k in range(28)]
        bits = []
        for k in range(14):
            a, b = halves[2 * k], halves[2 * k + 1]
            assert a != b, f"half-bits {2 * k}, {2 * k + 1} not bi-phase"
            bits.append(1 if b else 0)
        decoded.append((t0, bits))
    for t0, bits in decoded:
        val = lambda bb: int("".join(map(str, bb)), 2)
        assert bits[:2] == [1, 1] and bits[2] == TOG, bits[:3]
        assert val(bits[3:8]) == ADDR and val(bits[8:14]) == CMD, bits
    # to the cycle: every burst starts on its frame's half-bit grid and lasts whole
    # half-bits (its last period's off time included); frames 64 bit times apart
    H, OFF = 32 * 333, 222
    for fr, (t0, _) in zip(frames, decoded):
        for b0, b1 in fr:
            assert (b0 - t0) % H == 0, f"a burst {(b0 - t0) % H} cycles off the grid"
            assert (b1 + OFF - b0) % H == 0, f"a burst of {(b1 + OFF - b0) / H} half bits"
    assert decoded[1][0] - decoded[0][0] == 128 * H, (decoded[1][0] - decoded[0][0]) / CLK
    # the receiver module's output, a sample every 12 cycles: low while lit
    t1 = frames[1][-1][1] + 4 * int(half)
    env = []
    for t in range(0, t1, 12):
        env.append(0 if any(s <= t < f + CLK / 36000 for s, f in bursts) else 1)
    # (5 and 53 are a video recorder's Play in the RC-5 tables)
    lines = [f"Address: {ADDR} (Video cassette recorder 1)", f"Command: {CMD} (Play)"] * 2
    save_trace("ir_rc5", env, "ir_rc5:ir=0:polarity=active-low", "ir_rc5=address:command", lines,
               {"pin": 0, "hold": 900}, rate=CLK // 12)
