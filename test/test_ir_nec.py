# SPDX-License-Identifier: Apache-2.0
"""NEC infrared remote (firmware/ir_nec.s), carrier and all, against a receiver
written from SB-Projects' and Vishay's descriptions: 38 kHz at a third duty, a 9 ms
burst and 4.5 ms space, bits of a 562.5 us burst and 562.5 us (0) or 1687.5 us (1)
of space, address, ~address, command, ~command LSB first, a stop burst, and the
repeat code (9 ms, 2.25 ms, 562.5 us) 108 ms after the frame began. Times within 5 %."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, edges, samples

CLK = 12_000_000
ADDR, CMD = 0x59, 0x16


def near(x, want, tol=0.05):
    return abs(x - want) <= tol * want


def bursts(ch):
    """Carrier pulses -> [(start, end, periods)], checking frequency and duty."""
    e = edges(ch, 0)
    rises = [t for t, v in e if v == 1]
    falls = [t for t, v in e if v == 0]
    out, cur = [], None
    for r, f in zip(rises, falls):
        assert near((f - r) / CLK, 1 / 38000 / 3, 0.1), f"carrier on {f - r} cycles"
        if cur and r - cur[2] < 2 * CLK / 38000:
            assert near(CLK / (r - cur[1]), 38000, 0.01), f"carrier at {CLK / (r - cur[1]):.0f} Hz"
            cur = [cur[0], r, f, cur[3] + 1]
        else:
            if cur:
                out.append((cur[0], cur[2], cur[3]))
            cur = [r, r, f, 1]
    out.append((cur[0], cur[2], cur[3]))
    return out


@cocotb.test()
async def test_ir_nec_frame_and_repeat(dut):
    """Address 0x59, command 0x16, then a repeat code at 108 ms."""
    await fw_boot(dut, "ir_nec.s")
    ch = await watch_pins(dut, 1_480_000, pull=0)
    b = bursts(ch)
    us = lambda c: c * 1e6 / CLK
    lead = b[0]
    assert near(us(lead[1] - lead[0]), 9000), us(lead[1] - lead[0])
    assert near(us(b[1][0] - lead[1]), 4500), us(b[1][0] - lead[1])
    bits = []
    for (s0, e0, _), (s1, _, _) in zip(b[1:33], b[2:34]):
        assert near(us(e0 - s0), 562.5, 0.06), us(e0 - s0)
        space = us(s1 - e0)
        assert near(space, 562.5, 0.06) or near(space, 1687.5), space
        bits.append(1 if space > 1000 else 0)
    stop = b[33]
    assert near(us(stop[1] - stop[0]), 562.5, 0.06)
    byte = lambda k: sum(bits[8 * k + i] << i for i in range(8))
    assert [byte(i) for i in range(4)] == [ADDR, ADDR ^ 0xFF, CMD, CMD ^ 0xFF], \
        [hex(byte(i)) for i in range(4)]
    rep = b[34:]
    assert len(rep) == 2, len(rep)
    assert near(us(rep[0][0] - lead[0]), 108000, 0.01), us(rep[0][0] - lead[0])
    assert near(us(rep[0][1] - rep[0][0]), 9000) and near(us(rep[1][0] - rep[0][1]), 2250)
    assert near(us(rep[1][1] - rep[1][0]), 562.5, 0.06)
    save_trace("ir_nec", samples(ch, 0, ch[-1][0] + 24000, 3), "ir_nec:ir=0:polarity=active-high:cd_freq=38000",
               "ir_nec=addr:addr-inv:cmd:cmd-inv:repeat-code",
               [f"Address: 0x{ADDR:02X}", f"Address#: 0x{ADDR ^ 0xFF:02X}", f"Command: 0x{CMD:02X}",
                f"Command#: 0x{CMD ^ 0xFF:02X}", "Repeat code"], {"pin": 0, "hold": 3000},
               rate=CLK // 3)
