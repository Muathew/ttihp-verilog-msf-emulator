# SPDX-License-Identifier: Apache-2.0
"""DMX512 controller (firmware/dmx512.s) against a receiver written from ANSI E1.11-2008
(DMX512-A) §8 and Table 6: a break of at least 92 us, a mark after break of at least
12 us, slots of a start bit, 8 data bits LSB first and two stop bits at 250 kbit/s, the
NULL start code and 512 slots, break to break at least 1204 us.

sigrok's dmx512 decoder loses the framing when slots follow with no mark between them
(it takes the next start bit's first sample into the second stop bit's window and then
waits for the wrong edge), so the trace it reads is of the second run, with a 4 us mark
between slots; the first run, with none, is checked by the receiver here alone."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, edges, level, samples

US = 12
BIT = 4 * US
LEVELS = [255, 128, 0, 64, 1, 2, 4, 8, 16, 32, 0x55, 0xAA, 200, 100, 50, 25]
WANT = [0] + LEVELS + [0] * (512 - len(LEVELS))


async def run_dmx(dut, packets, mtbf):
    data = {0: len(LEVELS), **{1 + i: v for i, v in enumerate(LEVELS)}}
    await fw_boot(dut, "dmx512.s", data=data, defines={"PACKETS": packets, "MTBF": mtbf})
    slot = 11 * BIT + mtbf
    ch = await watch_pins(dut, packets * (188 * US + 513 * slot) + 4000)
    e = edges(ch, 0)
    falls = [t for t, v in e if v == 0]
    rises = [t for t, v in e if v == 1]
    breaks, i = [], 0
    for pkt in range(packets):
        while True:                                  # the break: a low of 88 us or more
            b0 = falls[i]
            b1 = next(r for r in rises if r > b0)
            if b1 - b0 >= 88 * US:
                break
            i += 1
        assert b1 - b0 >= 92 * US, f"break {(b1 - b0) / US:.1f} us"
        breaks.append(b0)
        t0 = next(f for f in falls if f > b1)
        assert t0 - b1 >= 12 * US, f"mark after break {(t0 - b1) / US:.1f} us"
        got = []
        for s in range(513):
            bits = [(level(ch, t0 + BIT // 2 + k * BIT) & 1) for k in range(11)]
            assert bits[0] == 0 and bits[9:] == [1, 1], f"packet {pkt} slot {s}: frame {bits}"
            got.append(sum(b << k for k, b in enumerate(bits[1:9])))
            nxt = next(f for f in falls if f > t0 + 10 * BIT) if s < 512 or pkt + 1 < packets else None
            if s < 512:
                assert nxt - t0 == slot, f"slot {s} lasts {(nxt - t0) / US:.2f} us"
                t0 = nxt
        assert got == WANT, f"packet {pkt}: {got[:20]}"
        if nxt is not None:
            i = falls.index(nxt)
    if packets > 1:
        assert breaks[1] - breaks[0] >= 1204 * US, (breaks[1] - breaks[0]) / US
    return ch


@cocotb.test()
async def test_dmx512_two_packets(dut):
    """Two packets, slots back to back: channels 1-16 as given, 17-512 at 0."""
    await run_dmx(dut, 2, 0)


@cocotb.test()
async def test_dmx512_mark_between_slots(dut):
    """A 4 us mark between slots (MTBF); sigrok's decoder reads this one."""
    ch = await run_dmx(dut, 1, BIT)
    t1 = ch[-1][0] + 200 * US
    lines = [f"{v} / {hex(v)}" for v in WANT]
    save_trace("dmx512", samples(ch, 0, t1, US), "dmx512:dmx=0", "dmx512=data", lines,
               {"pin": 0, "hold": 6}, rate=1_000_000)
