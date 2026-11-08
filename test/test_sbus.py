# SPDX-License-Identifier: Apache-2.0
"""S.BUS output (firmware/sbus.s) against a receiver written from the format as PX4's
and Betaflight's drivers document it: an inverted 100 kbit/s line, 8 data bits LSB
first, even parity, two stop bits; 25-byte frames of 0x0F, sixteen 11-bit channels
packed LSB first, the flags byte and 0x00; frames 14 ms apart."""
import cocotb

from fw_tb import fw_boot
from pins import watch_pins, edges, level

BIT = 120
CHANNELS = [992, 172, 1811, 0, 2047, 1024, 500, 1500, 1, 2, 4, 8, 16, 32, 64, 1234]
FLAGS = 0b0101                         # channel 17 on, frame lost


@cocotb.test()
async def test_sbus_two_frames(dut):
    """Two frames, 14 ms apart, every channel, flag and parity bit right."""
    data = {i: v for i, v in enumerate(CHANNELS)}
    data[16] = FLAGS
    await fw_boot(dut, "sbus.s", data=data)
    ch = await watch_pins(dut, 168000 + 25 * 12 * BIT + 4000, pull=0)
    e = edges(ch, 0)
    starts, t = [], 0
    for c, v in e:                     # a start bit: the line rises (it is inverted)
        if v == 1 and c >= t:
            starts.append(c)
            t = c + 11 * BIT + BIT // 2
    assert len(starts) == 50, len(starts)
    frames = []
    for k in range(2):
        bs = []
        for c in starts[25 * k:25 * k + 25]:
            u = [1 - (level(ch, c + BIT // 2 + i * BIT) & 1) for i in range(12)]   # un-invert
            assert u[0] == 0 and u[10:] == [1, 1], f"frame bits {u}"
            byte = sum(b << i for i, b in enumerate(u[1:9]))
            assert (sum(u[1:9]) + u[9]) % 2 == 0, "even parity"
            bs.append(byte)
        if k:
            assert starts[25] - starts[0] == 168000, starts[25] - starts[0]
        assert all(b - a == 12 * BIT for a, b in zip(starts[25 * k:25 * k + 24], starts[25 * k + 1:25 * k + 25]))
        frames.append(bs)
    for bs in frames:
        assert bs[0] == 0x0F and bs[24] == 0x00, (bs[0], bs[24])
        bits = sum(b << (8 * i) for i, b in enumerate(bs[1:23]))
        chans = [(bits >> (11 * i)) & 0x7FF for i in range(16)]
        assert chans == CHANNELS, chans
        assert bs[23] == FLAGS
