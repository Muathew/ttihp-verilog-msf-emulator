# SPDX-License-Identifier: Apache-2.0
"""Philips RC-6 mode 0 remote (firmware/ir_rc6.s), carrier and all, against a receiver
written from SB-Projects' description: 36 kHz at a third duty; T = 16 carrier periods
(444 us); a 6T burst and a 2T space lead; Manchester bits of 2T, a 1 a burst then a
space; the start bit 1, mode bits 000, the trailer (toggle) bit 4T long, 8 address and
8 command bits MSB first. Two key presses, the toggle flipping between them. sigrok's
ir_rc6 reads demodulated light: the trace is the envelope an IR receiver module puts
out (low while the carrier is there), made from the chip's carrier."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, edges

CLK = 12_000_000
PER = CLK / 36000
T = 16 * 333
OFF = 222
ADDR, CMDS = 0x00, (0x0C, 0x10)


def near(x, want, tol):
    return abs(x - want) <= tol * want


@cocotb.test()
async def test_ir_rc6_two_presses(dut):
    """Address 0, standby then volume up, toggle 0 then 1."""
    await fw_boot(dut, "ir_rc6.s")
    ch = await watch_pins(dut, 2 * 52 * T + 2 * 300_000 + 20_000, pull=0)
    e = edges(ch, 0)
    rises = [t for t, v in e if v == 1]
    falls = [t for t, v in e if v == 0]
    for r, f in zip(rises, falls):
        assert near(f - r, PER / 3, 0.05), f"carrier on {f - r} cycles"
    bursts, cur = [], None
    for r, f in zip(rises, falls):
        if cur and r - cur[1] < 2 * PER:
            assert near(r - cur[2], PER, 0.01), f"carrier period {r - cur[2]}"
            cur[1], cur[2] = f, r
        else:
            if cur:
                bursts.append((cur[0], cur[1]))
            cur = [r, f, r]
    bursts.append((cur[0], cur[1]))
    leaders = [b for b in bursts if near(b[1] - b[0], 6 * T, 0.05)]
    assert len(leaders) == 2, [round((b[1] - b[0]) / T, 2) for b in bursts]
    for k, (l0, l1) in enumerate(leaders):
        u0 = l0 + 8 * T                               # the first unit after the leader
        lit = lambda t: any(s <= t < f + PER for s, f in bursts)
        assert not lit(l0 + 7 * T), "the leader's space"
        units = [lit(u0 + (j + 0.5) * T) for j in range(44)]
        def bit(j):                                   # a 2T bit from unit j
            a, b = units[j], units[j + 1]
            assert a != b, f"frame {k}: units {j}, {j + 1} not Manchester"
            return 1 if a else 0
        assert bit(0) == 1, "start bit"
        assert [bit(2), bit(4), bit(6)] == [0, 0, 0], "mode 0"
        tr = units[8:12]
        assert tr in ([True, True, False, False], [False, False, True, True]), tr
        toggle = 1 if tr[0] else 0
        data = [bit(12 + 2 * i) for i in range(16)]
        val = int("".join(map(str, data)), 2)
        assert (toggle, val >> 8, val & 0xFF) == (k, ADDR, CMDS[k]), (toggle, hex(val))
        assert not any(s > u0 + 44 * T and s < u0 + 50 * T for s, _ in bursts), "6T free after"
        for b0, b1 in bursts:                         # to the cycle, on the T grid
            if l0 <= b0 < u0 + 44 * T:
                assert (b0 - l0) % T == 0, f"a burst {(b0 - l0) % T} cycles off the grid"
                assert (b1 + OFF - b0) % T == 0, f"a burst of {(b1 + OFF - b0) / T} T"
    t1 = bursts[-1][1] + 20 * T
    env = [0 if any(s <= t < f + PER for s, f in bursts) else 1 for t in range(0, t1, 12)]
    # (sigrok gives the first frame's header twice)
    head = lambda tg: ["Synchronisation", "Startbit", "Field: 0", f"Toggle: {tg}"]
    lines = (head(0) + head(0) + [f"Address: {ADDR:02X}", f"Data: {CMDS[0]:02X}"]
             + head(1) + [f"Address: {ADDR:02X}", f"Data: {CMDS[1]:02X}"])
    save_trace("ir_rc6", env, "ir_rc6:ir=0:polarity=active-low", "ir_rc6=fields", lines,
               {"pin": 0, "hold": 450}, rate=CLK // 12)
