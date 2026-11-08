# SPDX-License-Identifier: Apache-2.0
"""NEC receiver (firmware/nec_rx.s) fed an IR receiver module's output (low during a
burst) made from SB-Projects' and Vishay's description of NEC: a 9 ms burst and 4.5 ms
space, 32 bits of a 562.5 us burst and a 562.5 us (0) or 1687.5 us (1) space, address,
~address, command, ~command LSB first, a stop burst, and a repeat code (9 ms, 2.25 ms,
562.5 us) 108 ms after the frame began. Every edge is moved by up to 40 us, as a real
module's output is."""
import random

import cocotb
from cocotb.triggers import ClockCycles

from fw_tb import fw_boot

US = 12


def nec_frame(addr, cmd, bad=False):
    """[(level, cycles)] of a frame, then the rest of 108 ms, then a repeat code."""
    bits = []
    for b in (addr, addr ^ 0xFF, cmd, (cmd ^ 0xFF) ^ (1 if bad else 0)):
        bits += [(b >> i) & 1 for i in range(8)]
    seq = [(0, 9000), (1, 4500)]
    for b in bits:
        seq += [(0, 562.5), (1, 1687.5 if b else 562.5)]
    seq += [(0, 562.5)]
    used = sum(d for _, d in seq)
    seq += [(1, 108000 - used), (0, 9000), (1, 2250), (0, 562.5), (1, 20000)]
    return [(lv, int(d * US)) for lv, d in seq]


async def drive(dut, seq, seed):
    rng = random.Random(seed)
    dut.uio_in.value = 1
    await ClockCycles(dut.clk, 500)
    for lv, n in seq:
        dut.uio_in.value = lv
        await ClockCycles(dut.clk, max(1, n + rng.randint(-40 * US, 40 * US)))


@cocotb.test()
async def test_nec_rx_frame_and_repeat(dut):
    """Address 0x04, command 0x08, one repeat code: all decoded through the jitter."""
    await fw_boot(dut, "nec_rx.s", uio_in=1)
    await drive(dut, nec_frame(0x04, 0x08), seed=1)
    assert int(dut.uo_out.value) == 0x55, hex(int(dut.uo_out.value))
    mem = dut.user_project.u_sram.mem
    assert [int(mem[64 + i].value) for i in range(3)] == [0x04, 0x08, 1]


@cocotb.test()
async def test_nec_rx_bad_complement(dut):
    """The command's complement with a bit wrong: BAD."""
    await fw_boot(dut, "nec_rx.s", uio_in=1)
    await drive(dut, nec_frame(0x04, 0x08, bad=True)[:70], seed=2)
    await ClockCycles(dut.clk, 2000)
    assert int(dut.uo_out.value) == 0xEE, hex(int(dut.uo_out.value))
