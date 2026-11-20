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
1 at 11 ns, 11 at 12 ns; at 13.5 ns half of 40 (docs/plan-v3.md, item 3).

Fine mode (RX_OPT bit 6, eth10_rx.s FINE=1, plan-v4 6.5 f) also takes the pin on the falling
edge, so an edge is placed to 10 ns, and locks on 31 centres: it holds 13.5 ns. The tests of
it drive the pin on both clock edges from test/mode3_model.py's samples of the same line,
and compare the RTL with the model frame by frame where some frames fail."""
import random

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

import mode3_model as m3
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


# ---- fine mode: the pin driven on both clock edges, the RTL against test/mode3_model.py ----

def half_line(frame, ppm, jitter, dist, rng):
    """The frame's bits and the (falling-edge, rising-edge) pin levels of its line."""
    bits = m3.bits_of(frame)
    times, levels = m3.line_edges(bits, ppm, jitter, rng, 40 * m3.NS + rng.uniform(0, m3.NS), dist)
    return bits, m3.samples(times, levels, rng.uniform(0, m3.NS))


async def receive_half(dut, seq, fine=1, probe=None):
    """eth10_rx.s (FINE as given) on a line driven every half cycle: (status, bytes kept, length).
    With a list as probe, engine set 0's (rx_tmr, rx_ones) is appended to it every cycle."""
    await fw_boot(dut, "eth10_rx.s", uio_in=0, defines={"FINE": fine})
    for _ in range(400):
        if int(dut.uo_out.value) == 0x40:
            break
        await RisingEdge(dut.clk)
    assert int(dut.uo_out.value) == 0x40, "not armed"
    for half, pin in seq:
        dut.uio_in.value = half            # taken on the falling edge ...
        await FallingEdge(dut.clk)
        dut.uio_in.value = pin             # ... and on the rising edge
        await RisingEdge(dut.clk)
        if probe is not None:
            e = dut.user_project.u_serdes
            probe.append((int(e.rx_tmr.value), int(e.rx_ones.value)))
    for _ in range(2000):
        if int(dut.uo_out.value) & 0x80:
            break
        await RisingEdge(dut.clk)
    n = sram(dut, 100)
    return int(dut.uo_out.value), bytes(sram(dut, i) for i in range(min(n, 96))), n


def rtl_ok(frame, result):
    st, got, n = result
    return st == 0x81 and n == len(frame) and got == frame[:96]


@cocotb.test()
async def test_eth10_rx_fine_13_5ns(dut):
    """Fine mode at 802.3's 13.5 ns: minimum frames at +-100 ppm, the jitter uniform and with
    every edge at +-13.5 ns, every frame received, as the model says."""
    rng = random.Random(135)
    for dist in ("uniform", "extreme", "uniform", "extreme", "extreme", "extreme"):
        ppm = rng.choice((-100.0, 100.0))
        fr = frame_bytes(rng)
        bits, seq = half_line(fr, ppm, 13.5, dist, rng)
        assert m3.receive(seq, True) == bits, "the model loses this frame: pick another seed"
        res = await receive_half(dut, seq)
        assert rtl_ok(fr, res), f"{dist}, {ppm:+.0f} ppm: status {res[0]:#x}, {res[2]} bytes"


@cocotb.test()
async def test_eth10_rx_fine_long_frame(dut):
    """Fine mode, a maximum-size frame at -100 ppm with every edge at +-13.5 ns."""
    rng = random.Random(1500)
    fr = frame_bytes(rng, n=1500)
    bits, seq = half_line(fr, -100.0, 13.5, "extreme", rng)
    assert m3.receive(seq, True) == bits, "the model loses this frame: pick another seed"
    res = await receive_half(dut, seq)
    assert rtl_ok(fr, res), f"status {res[0]:#x}, {res[2]} bytes"


async def matches_model(dut, fine, jitter, frames, seed):
    """Each frame: the RTL receives it exactly when the model does; both outcomes occur."""
    rng = random.Random(seed)
    seen = set()
    for i in range(frames):
        ppm = rng.choice((-100.0, 100.0))
        fr = frame_bytes(rng)
        bits, seq = half_line(fr, ppm, jitter, "extreme", rng)
        want = m3.receive(seq, bool(fine)) == bits
        got = rtl_ok(fr, await receive_half(dut, seq, fine))
        assert got == want, f"frame {i}: the model {'takes' if want else 'loses'} it, the RTL does not"
        seen.add(got)
    assert seen == {True, False}, f"only {seen}: the comparison needs frames of both kinds"


@cocotb.test()
async def test_eth10_rx_fine_matches_model(dut):
    """Fine mode beyond its limit (every edge at +-16 ns): frame by frame, the RTL receives
    exactly the frames the model receives."""
    await matches_model(dut, 1, 16.0, 12, 16)


@cocotb.test()
async def test_eth10_rx_v3_matches_model(dut):
    """Fine mode off (v3) with every edge at +-10 ns, the pin driven on both edges: the RTL
    loses exactly the frames the model loses (v3 holds 10 ns of uniform jitter, not this)."""
    await matches_model(dut, 0, 10.0, 12, 10)


@cocotb.test()
async def test_eth10_rx_phase_matches_model(dut):
    """The phase itself, cycle by cycle: on a frame with every edge at +-13.5 ns, the RTL's
    rx_tmr and rx_ones equal the model's after every sample, from the first edge to the end of
    the frame, in fine mode and in v3 (a fixed lag of the synchroniser apart)."""
    for fine, seed in ((1, 51), (0, 52)):
        rng = random.Random(seed)
        fr = frame_bytes(rng)
        bits, seq = half_line(fr, rng.choice((-100.0, 100.0)), 13.5, "extreme", rng)
        want = []
        m3.receive(seq, bool(fine), trace=want)
        got = []
        await receive_half(dut, seq, fine, probe=got)
        lags = [d for d in range(6) if got[d:d + len(want)] == want]
        if not lags:
            d = max(range(6), key=lambda d: next((i for i, (a, b) in enumerate(zip(got[d:], want))
                                                    if a != b), len(want)))
            i = next(i for i, (a, b) in enumerate(zip(got[d:], want)) if a != b)
            raise AssertionError(f"fine={fine}: sample {i} of {len(want)} (lag {d}): "
                                 f"RTL {got[d + i]}, model {want[i]}")
        dut._log.info("fine=%d: %d samples equal, lag %d cycles", fine, len(want), lags[0])
