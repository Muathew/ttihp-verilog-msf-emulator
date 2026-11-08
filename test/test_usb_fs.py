# SPDX-License-Identifier: Apache-2.0
"""USB full speed (12 Mbit/s) at a 48 MHz clock: 4 cycles a bit, firmware/usb_fs_tx.s
and usb_fs_rx.s against a host written from the USB 2.0 specification, chapters 7.1
and 8: J = D+ high, K = D- high, SE0 both low; NRZI (a 0 is a transition); a 0
stuffed after six 1s; SYNC 00000001, PID, data LSB first, CRC16 (sent inverted);
EOP = SE0 for two bit times, then J; the data rate within +-0.25 %. The simulation
counts cycles, so one cycle is 20.83 ns here. The host's edges are placed in
continuous time (a random phase, the rate off by up to 0.25 %, each edge moved by up
to 8 ns) and sampled on the clock, as the chip's input synchroniser does."""
import random

import cocotb
from cocotb.triggers import ClockCycles

from fw_tb import fw_boot, save_trace, Recorder
from golden_model import usb_crc16
from msf_tb import sram

DONE = 0x55
J, K, SE0 = "J", "K", "0"
NS = 1e9 / 48e6                       # one cycle at 48 MHz


def state(v):
    dp, dm = v & 1, (v >> 1) & 1
    return SE0 if (dp, dm) == (0, 0) else J if (dp, dm) == (1, 0) else K if (dp, dm) == (0, 1) else "1"


def fs_lines(pid_name, payload, crc):
    return (["SYNC: 00000001", f"PID: {pid_name}"] + [f"Databyte: {b:02X}" for b in payload]
            + [f"CRC16: 0x{crc:04X}"])


@cocotb.test()
async def test_usb_fs_tx_packet(dut):
    """A DATA0 packet with a run of ones: every edge on the 4-cycle grid, the bytes,
    the stuffing, the CRC, SE0 for 8 cycles and J for 4."""
    pid = 0xC3
    payload = bytes([0x80, 0x06, 0x00, 0x01, 0xFF, 0xFF, 0x12, 0x00, 0x7E, 0xFE])
    data = {0: pid, 1: len(payload)}
    data.update({2 + i: b for i, b in enumerate(payload)})
    await fw_boot(dut, "usb_fs_tx.s", data=data)
    rec = Recorder(dut)
    await rec.until_uo(lambda v: v == DONE, 20000)
    states = [state(v) if (oe & 3) == 3 else "z" for v, oe in zip(rec.out, rec.oe)]
    start = states.index(K)
    edges = [i for i in range(start + 1, len(states)) if states[i] != states[i - 1]]
    assert all((e - start) % 4 == 0 for e in edges), "bit timing off the 4-cycle grid"
    bits, prev, i = [], J, start + 2
    while states[i] in (J, K):
        bits.append(1 if states[i] == prev else 0)
        prev = states[i]
        i += 4
    eop = i - 2
    out, ones, skip, stuffed = [], 0, False, 0
    for b in bits:
        if skip:
            assert b == 0, "a stuffed bit must be 0"
            skip, ones, stuffed = False, 0, stuffed + 1
            continue
        out.append(b)
        ones = ones + 1 if b else 0
        skip = ones == 6
    by = [sum(out[8 * k + j] << j for j in range(8)) for k in range(len(out) // 8)]
    crc = usb_crc16(payload)
    assert by == [0x80, pid] + list(payload) + [crc & 0xFF, crc >> 8], [hex(x) for x in by]
    assert stuffed >= 2, "the run of ones was stuffed"
    se0 = 0
    while states[eop + se0] == SE0:
        se0 += 1
    assert se0 == 8, f"SE0 must last 2 bit times (8 cycles), got {se0}"
    j = 0
    while states[eop + se0 + j] == J:
        j += 1
    assert j == 4, f"J after EOP must last one bit (4 cycles), got {j}"
    assert states[eop + se0 + j] == "z", "pins released after the EOP"
    save_trace("usb_fs_tx", rec.out,
               "usb_signalling:dp=0:dm=1:signalling=full-speed,usb_packet:signalling=full-speed",
               "usb_packet=sync-ok:pid:data:crc16-ok", fs_lines("DATA0", payload, crc),
               {"pin": 0, "hold": 3}, rate=48_000_000)


def host_bits(pkt, corrupt=False):
    """The packet's line states, NRZI with stuffing, then the EOP."""
    bits = [(b >> i) & 1 for b in pkt for i in range(8)]
    st, ones = [], 0
    for b in bits:
        st.append(b)
        ones = ones + 1 if b else 0
        if ones == 6:
            st.append(0)
            ones = 0
    if corrupt:
        st[60] ^= 1
    lv, cur = [], J
    for b in st:
        cur = cur if b else (K if cur == J else J)
        lv.append(cur)
    return lv + [SE0, SE0, J]


def sampled(states, ppm, phase, jitter_ns, rng):
    """Per-cycle pad values of the line states sent at 12 Mbit/s * (1 + ppm): the
    edge of bit k at phase + k * T (+- jitter), seen from the first clock after it."""
    t_bit = 4 * (1 + ppm / 1e6)
    edges = [phase + k * t_bit + rng.uniform(-jitter_ns, jitter_ns) / NS
             for k in range(len(states) + 1)]
    n = int(edges[-1]) + 8
    code = {J: 0b01, K: 0b10, SE0: 0b00}
    pads, k = [], -1
    for c in range(n):
        while k + 1 < len(edges) and edges[k + 1] <= c:
            k += 1
        pads.append(0b01 if k < 0 or k >= len(states) else code[states[k]])
    return pads


@cocotb.test()
async def test_usb_fs_rx_packet(dut):
    """32-byte DATA1 packets at the rate's limits (+-0.25 %), random phases and 8 ns
    of edge jitter: every byte received and the CRC16 checked; a corrupted one refused."""
    rng = random.Random(12)
    payload = bytes(rng.randrange(256) for _ in range(28)) + b"\xff\xff\xff\x00"
    crc = usb_crc16(payload)
    pkt = bytes([0x80, 0x4B]) + payload + bytes([crc & 0xFF, crc >> 8])
    first = None
    for corrupt, ppm in ((False, 0), (False, 2500), (False, -2500), (False, 1200),
                         (False, -700), (True, 0)):
        await fw_boot(dut, "usb_fs_rx.s", uio_in=0b01)         # J
        rec = Recorder(dut)
        await rec.until_uo(lambda v: v == 0x40, 2000)
        await ClockCycles(dut.clk, 40)
        pads = sampled(host_bits(pkt, corrupt), ppm, rng.random(), 8.0, rng)
        for v in pads:
            dut.uio_in.value = v
            await ClockCycles(dut.clk, 1)
        dut.uio_in.value = 0b01
        await rec.until_uo(lambda v: v & 0x80, 3000)
        st = rec.uo[-1]
        if corrupt:
            assert st == 0x82, f"corrupted packet accepted ({st:#x})"
        else:
            assert st == 0x81, f"ppm={ppm}: status {st:#x}"
            got = [sram(dut, 64 + i) for i in range(sram(dut, 63))]
            assert got == list(pkt), f"ppm={ppm}: {[hex(x) for x in got]}"
            first = first or pads
    save_trace("usb_fs_rx", first,
               "usb_signalling:dp=0:dm=1:signalling=full-speed,usb_packet:signalling=full-speed",
               "usb_packet=sync-ok:pid:data:crc16-ok", fs_lines("DATA1", payload, crc),
               {"pin": 0, "hold": 3}, rate=48_000_000)
