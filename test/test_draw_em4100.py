# SPDX-License-Identifier: Apache-2.0
"""Drawn protocol em4100 (docs/draw/): the chip is an EM4100 tag.

The frame is rebuilt here from the data sheets (EM Microelectronic EM4100 and
EM4102: 9 header 1s, 10 rows of 4 data bits + even parity, 4 even column parity
bits, stop bit 0; Manchester, 64 field periods a bit; a 1 is high then low, as
the EM4200 sheet says) and compared with the pin, bit by bit, over five whole
frames, with every edge on the half-bit grid. The same session wrote this and
the firmware, so it is not an independent check: sigrok's em4100 decoder is.
"""
import cocotb
from cocotb.triggers import Timer

from fw_tb import fw_boot, save_trace
from msf_tb import CLK_NS

HALF = 3072                    # cycles: RF/64 at 125 kHz is 512 us a bit at 12 MHz
VER, DATA = 0x1A, 0x2B3C4D5E
FRAMES = 5
DIV = 12                       # trace: one sample every 12 cycles, 1 MHz


def em4100_frame(ver, data):
    """The 64 bits a tag sends, first bit first."""
    nibbles = [(((ver << 32) | data) >> (36 - 4 * i)) & 0xF for i in range(10)]
    bits = [1] * 9
    for n in nibbles:
        row = [(n >> (3 - k)) & 1 for k in range(4)]
        bits += row + [sum(row) & 1]
    for k in range(4):
        bits.append(sum((n >> (3 - k)) & 1 for n in nibbles) & 1)
    bits.append(0)
    assert len(bits) == 64
    return bits


async def record(dut, cycles):
    """(cycle, level) of uio[0] at each change, for `cycles` cycles."""
    t0 = cocotb.utils.get_sim_time("ns")
    ch = []

    async def watch():
        last = None
        while True:
            v = int(dut.uio_out.value) & int(dut.uio_oe.value) & 1
            if v != last:
                ch.append((round((cocotb.utils.get_sim_time("ns") - t0) / CLK_NS), v))
                last = v
            await dut.uio_out.value_change

    task = cocotb.start_soon(watch())
    await Timer(cycles * CLK_NS, unit="ns")
    task.cancel()
    return ch


@cocotb.test()
async def test_draw_em4100(dut):
    """Five whole frames, back to back, exactly on the half-bit grid."""
    await fw_boot(dut, "draw_em4100.s")
    # a decoder finds the header after the stop bit before it, so it skips the first frame
    ch = await record(dut, (FRAMES + 2) * 64 * 2 * HALF)
    edges = [c for c, _ in ch[1:]]
    first = edges[0]
    off = [(e - first) % HALF for e in edges]
    assert not any(off), f"an edge off the {HALF}-cycle grid: {sorted(set(off))[:5]}"
    # the level in each half-bit from the first edge on, then bits (1 = high, low)
    halves, lv, i = [], ch[0][1], 1
    for t in range(first, edges[-1] + HALF, HALF):
        while i < len(ch) and ch[i][0] <= t:
            lv = ch[i][1]
            i += 1
        halves.append(lv)
    # a Manchester bit has a change in its middle: find the pairing that has one everywhere
    for ph in (0, 1):
        pairs = [halves[i:i + 2] for i in range(ph, len(halves) - 1, 2)]
        if all(a != b for a, b in pairs[:-1]):
            break
    else:
        raise AssertionError("no Manchester bit alignment fits")
    bits = [1 if a == 1 else 0 for a, _ in pairs[:-1]]
    want = em4100_frame(VER, DATA)
    stream = "".join(map(str, bits))
    frame = "".join(map(str, want))
    at = stream.find(frame)
    assert at >= 0, f"no whole frame in {stream[:200]}"
    run = stream[at:]
    whole = 0
    while run.startswith(frame):
        whole += 1
        run = run[64:]
    dut._log.info(f"{whole} whole frames back to back, first at bit {at}; "
                  f"{len(edges)} edges on the {HALF}-cycle grid")
    assert whole >= FRAMES, f"only {whole} frames back to back"
    # the pin for sigrok, one sample every DIV cycles, from just before the first edge
    t0, tend = first - 4 * HALF, edges[-1] + 4 * HALF
    samples, i, lv = [], 0, 0
    for c in range(t0, tend, DIV):
        while i < len(ch) and ch[i][0] <= c:
            lv = ch[i][1]
            i += 1
        samples.append(lv)
    tag = f"{VER:02X}{DATA:08X}"
    save_trace("draw_em4100", samples, "em4100:data=0", "em4100=tag",
               [f"Tag: {tag}"] * FRAMES, {"pin": 0, "hold": HALF // DIV},
               rate=12_000_000 // DIV)
