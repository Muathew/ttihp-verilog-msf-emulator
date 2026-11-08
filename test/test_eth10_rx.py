# SPDX-License-Identifier: Apache-2.0
"""10BASE-T receive at a 50 MHz clock (firmware/eth10_rx.s, the RX engine's mode 3)
against a peer written from IEEE 802.3: Manchester, the second half of a bit cell is
the bit (low->high = 1), 10 Mbit/s +-0.01 %, preamble 7 x 0x55, SFD 0xD5, bytes LSB
first, FCS = CRC-32; after the frame TP_IDL holds the line high for 300 ns, then idle.
The receiver must take 13.5 ns of edge jitter (clause 14). The simulation counts
cycles, so a cycle is 20 ns here and a half-bit 2.5 cycles: the peer places every
edge in continuous time (a random phase, the rate off by up to 100 ppm, each edge
moved at random by up to 10 ns) and the pin sees it from the first clock after it,
as the chip's input synchroniser does. Why 10 ns and not 802.3's 13.5: sampling at
20 ns spreads the edges a further 20 ns, so the centres' band (27 + 20 ns) and the
boundaries' come within 1.5 ns of each other half a bit (50 ns) apart, which no
receiver sampling at 50 MHz on one clock edge can tell apart reliably. Mode 3's
logic in Python, 150 minimum-size frames a point at +-100 ppm: none wrong at 10 ns,
1 at 11 ns, 11 at 12 ns; at 13.5 ns half of 40 (docs/plan-v3.md, item 3)."""
import random

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge

from fw_tb import fw_boot
from golden_model import eth_fcs
from msf_tb import sram

NS = 20.0                                  # one cycle at 50 MHz


def frame_bytes(rng, n=46, corrupt=False):
    body = bytes([0x02, 0, 0, 0, 0, 0x01, 0x02, 0x4D, 0x53, 0x46, 0x33, 0x00, 0x88, 0xB5])
    body += bytes(rng.randrange(256) for _ in range(n))
    fcs = eth_fcs(body)
    fr = bytearray(body + fcs.to_bytes(4, "little"))
    if corrupt:
        fr[20] ^= 0x10
    return bytes(fr)


def line(frame, ppm, phase, jitter, rng):
    """Per-cycle pin levels: idle, preamble, SFD, the frame, TP_IDL, idle."""
    bits = []
    for b in bytes([0x55] * 7 + [0xD5]) + frame:
        bits += [(b >> i) & 1 for i in range(8)]
    halves = []
    for b in bits:
        halves += [1 - b, b]
    h = 50.0 * (1 + ppm / 1e6) / NS         # a half-bit in cycles
    t0 = 40 + phase
    edges, lv, k = [], 0, 0
    for i, v in enumerate(halves):
        if v != lv:
            edges.append((t0 + i * h + rng.uniform(-jitter, jitter) / NS, v))
            lv, k = v, k + 1
    end = t0 + len(halves) * h
    if lv == 0:                               # TP_IDL: high for 300 ns, then idle
        edges.append((end, 1))
    edges.append((end + 300 / NS, 0))
    n = int(edges[-1][0]) + 400
    levels, e, cur = [], 0, 0
    for c in range(n):
        while e < len(edges) and edges[e][0] <= c:
            cur = edges[e][1]
            e += 1
        levels.append(cur)
    return levels


async def receive(dut, levels):
    await fw_boot(dut, "eth10_rx.s", uio_in=0)
    for _ in range(400):
        if int(dut.uo_out.value) == 0x40:
            break
        await RisingEdge(dut.clk)
    assert int(dut.uo_out.value) == 0x40, "not armed"
    for v in levels:
        dut.uio_in.value = v
        await ClockCycles(dut.clk, 1)
    for _ in range(2000):
        if int(dut.uo_out.value) & 0x80:
            break
        await RisingEdge(dut.clk)
    return int(dut.uo_out.value)


@cocotb.test()
async def test_eth10_rx_frames(dut):
    """Minimum-size frames at 0, +-100 ppm and between, 10 ns of edge jitter: every
    byte kept, the FCS right; a frame with a flipped bit refused."""
    rng = random.Random(10)
    cases = [(0, 10.0), (100, 10.0), (-100, 10.0), (100, 10.0), (-100, 10.0), (60, 10.0)]
    for ppm, jitter in cases:
        fr = frame_bytes(rng)
        st = await receive(dut, line(fr, ppm, rng.random() * 5, jitter, rng))
        n = sram(dut, 100)
        got = bytes(sram(dut, i) for i in range(min(n, 96)))
        assert st == 0x81 and n == len(fr) and got == fr[:96], \
            f"ppm {ppm}, jitter {jitter}: status {st:#x}, {n} bytes\n{got.hex()}\n{fr[:96].hex()}"
    fr = frame_bytes(rng, corrupt=True)
    st = await receive(dut, line(fr, 0, rng.random() * 5, 10.0, rng))
    assert st == 0x82, f"a corrupted frame accepted ({st:#x})"


@cocotb.test()
async def test_eth10_rx_long_frame(dut):
    """A maximum-size frame (1500-byte payload, 12,000 bits) at -100 ppm with 10 ns of
    jitter: the phase holds to the end, the first 96 bytes kept, the FCS right."""
    rng = random.Random(11)
    fr = frame_bytes(rng, n=1500)
    st = await receive(dut, line(fr, -100, 2.3, 10.0, rng))
    assert st == 0x81, f"status {st:#x}"
    assert sram(dut, 100) == len(fr) and bytes(sram(dut, i) for i in range(96)) == fr[:96]
