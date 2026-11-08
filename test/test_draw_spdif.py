# SPDX-License-Identifier: Apache-2.0
"""Drawn protocol spdif (docs/draw/): the chip is an S/PDIF transmitter.

The clock is 30.72 MHz (the firmware's choice, inside the 33.33 MHz sign-off): 5
cycles a half-cell, 48 kHz exactly. The line is decoded here from the IEC 60958
rules as the literature states them (the AES3 and S/PDIF articles, application
notes): every edge on the half-cell grid, a transition at every cell boundary
outside the preambles, a 1 with one in the middle; preambles B, M and W; for each
subframe aux 0, the 16-bit sample in slots 12-27, V = U = 0, even parity over
slots 4-31; 192-frame blocks starting with B; channel status consumer, PCM,
48 kHz (bit 25), the same in both subframes. The same session wrote this and the
firmware, so it is not an independent check: sigrok's spdif decoder is.
"""
import cocotb
from cocotb.triggers import Timer
from cocotb.utils import get_sim_time

from fw_tb import fw_boot, save_trace
from msf_tb import CLK_NS

HALF = 5
CLOCK = 30_720_000
TAB = [0x0000, 0x5A82, 0x7FFF, 0x5A82, 0x0000, 0xA57E, 0x8001, 0xA57E]
PRE = {"B": [1, 1, 1, 0, 1, 0, 0, 0], "M": [1, 1, 1, 0, 0, 0, 1, 0], "W": [1, 1, 1, 0, 0, 1, 0, 0]}
BLOCKS = 2


async def record(dut, cycles):
    t0 = get_sim_time("ns")
    ch = []

    async def watch():
        last = None
        while True:
            v = int(dut.uio_out.value) & int(dut.uio_oe.value) & 1
            if v != last:
                ch.append((round((get_sim_time("ns") - t0) / CLK_NS), v))
                last = v
            await dut.uio_out.value_change

    task = cocotb.start_soon(watch())
    await Timer(cycles * CLK_NS, unit="ns")
    task.cancel()
    return ch


@cocotb.test()
async def test_draw_spdif(dut):
    """Two whole blocks of 48 kHz, 16-bit stereo, with channel status."""
    await fw_boot(dut, "draw_spdif.s")
    ch = await record(dut, (BLOCKS * 192 + 24) * 128 * HALF)
    edges = [c for c, _ in ch[1:]]
    first = edges[0]
    off = sorted({(e - first) % HALF for e in edges})
    assert off == [0], f"edges off the {HALF}-cycle grid: {off[:5]}"
    halves, lv, i = [], ch[0][1], 1
    for t in range(first, edges[-1], HALF):
        while i < len(ch) and ch[i][0] <= t:
            lv = ch[i][1]
            i += 1
        halves.append(lv)
    # subframes: a preamble every 64 half-cells from the first one found
    def pre_at(k):
        seg = halves[k:k + 8]
        for name, p in PRE.items():
            if seg == p or seg == [1 - x for x in p]:
                return name
        return None
    start = next(k for k in range(64) if pre_at(k))
    subs, errors = [], []
    for k in range(start, len(halves) - 64, 64):
        name = pre_at(k)
        if name is None:
            errors.append(f"half-cell {k}: no preamble")
            break
        cells = []
        for s in range(4, 32):
            a, b = halves[k + 2 * s], halves[k + 2 * s + 1]
            if a == halves[k + 2 * s - 1]:
                errors.append(f"subframe at {k}, slot {s}: no transition at the cell start")
            cells.append(1 if a != b else 0)
        if halves[k + 64] == halves[k + 63] and len(halves) > k + 64:
            errors.append(f"subframe at {k}: no transition before the next preamble")
        subs.append((name, cells))
        if len(errors) > 10:
            break
    assert not errors, errors[:5]
    # into frames and blocks, from the first B
    b0 = next(j for j, (n, _) in enumerate(subs) if n == "B")
    frames = [(subs[j], subs[j + 1]) for j in range(b0, len(subs) - 1, 2)]
    cs = [[], []]
    for f, (left, right) in enumerate(frames):
        want = ("B" if f % 192 == 0 else "M", "W")
        for side, (name, c) in enumerate((left, right)):
            if name != want[side]:
                errors.append(f"frame {f}: preamble {name}, expected {want[side]}")
            if any(c[0:8]):
                errors.append(f"frame {f}: aux or the field's low bits not 0")
            sample = sum(c[8 + n] << n for n in range(16))
            if sample != TAB[(f + 2 * side) % 8]:
                errors.append(f"frame {f} side {side}: sample {sample:04x}, "
                              f"expected {TAB[(f + 2 * side) % 8]:04x}")
            if c[24] or c[25]:
                errors.append(f"frame {f}: V or U set")
            if sum(c) % 2:
                errors.append(f"frame {f} side {side}: parity odd")
            cs[side].append(c[26])
    whole = len(frames) // 192
    for side in (0, 1):
        for blk in range(whole):
            bits = cs[side][blk * 192:(blk + 1) * 192]
            if [n for n, v in enumerate(bits) if v] != [25]:
                errors.append(f"block {blk} side {side}: channel status bits "
                              f"{[n for n, v in enumerate(bits) if v]} set, expected [25]")
    for e in errors[:10]:
        dut._log.error(e)
    dut._log.info(f"{len(subs)} subframes, {len(frames)} frames, {whole} whole blocks, "
                  f"{len(edges)} edges on the {HALF}-cycle grid at {CLOCK / 1e6} MHz: "
                  f"{CLOCK / (128 * HALF):.0f} frames a second")
    assert not errors, f"{len(errors)} errors"
    assert whole >= BLOCKS, f"only {whole} whole blocks"
    # the line for sigrok, one sample a cycle at 30.72 MHz, from the first edge: a
    # real S/PDIF line never idles, and a stretch of idle before the stream puts the
    # decoder's rate estimate far off, so that it finds nothing. It takes the first
    # subframe to lock on, then shows each preamble, sample (in a 24-bit field) and C.
    samples, i, lv = [], 0, 0
    for c in range(first, edges[-1] + 1):
        while i < len(ch) and ch[i][0] <= c:
            lv = ch[i][1]
            i += 1
        samples.append(lv)
    expect = []
    for j, (name, c) in enumerate(subs[1:], start=1):
        expect += [f"Preamble {name}", f"Audio 0x{sum(c[8 + n] << n for n in range(16)) << 8:x}",
                   f"C: {c[26]}"]
    nxt = pre_at(start + 64 * len(subs))     # the unfinished last subframe's preamble
    if nxt:
        expect.append(f"Preamble {nxt}")
    save_trace("draw_spdif", samples, "spdif:data=0", "spdif=preamble:samples:chan_stat",
               expect, {"pin": 0, "hold": HALF}, rate=CLOCK)
