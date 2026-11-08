# SPDX-License-Identifier: Apache-2.0
"""SSI encoder reader (firmware/ssi_enc.s) against an absolute encoder written from SICK-
Stegmann's and Kuebler's SSI descriptions: the clock idles high; the first falling edge
latches the position (25 bits, Gray code); each rising edge puts the next bit on DATA,
MSB first; after the last bit DATA is held low for the monoflop time (20 us), then goes
high, and the clock must stay high that long before the next reading."""
import cocotb
from cocotb.triggers import RisingEdge

from fw_tb import fw_boot

POSITIONS = [(5 << 13) | 1000, (5 << 13) | 8191, (6 << 13) | 0]
TM = 240                        # 20 us


class Encoder:
    def __init__(self, dut):
        self.dut, self.t, self.reads = dut, 0, 0
        self.shift, self.n, self.data, self.mono_until, self.last_rise = None, 0, 1, 0, None
        cocotb.start_soon(self.run())

    async def run(self):
        d, pc = self.dut, 1
        while True:
            await RisingEdge(d.clk)
            self.t += 1
            out, oe = int(d.uio_out.value), int(d.uio_oe.value)
            clk = (out & 1) if oe & 1 else 1
            if pc and not clk and self.shift is None:          # the latching edge
                if self.last_rise is not None:
                    assert self.t >= self.mono_until, "read during the monoflop time"
                    assert self.t - self.last_rise >= TM, "clock high under the monoflop time"
                p = POSITIONS[min(self.reads, len(POSITIONS) - 1)]
                self.shift, self.n = p ^ (p >> 1), 0
                self.reads += 1
            elif clk and not pc:
                self.last_rise = self.t
                if self.shift is not None:
                    if self.n < 25:
                        self.data = (self.shift >> (24 - self.n)) & 1
                        self.n += 1
                    else:                              # after the LSB: the monoflop
                        self.data, self.shift = 0, None
                        self.mono_until = self.t + TM
            if self.shift is None and self.t >= self.mono_until:
                self.data = 1
            d.uio_in.value = self.data << 1
            pc = clk


@cocotb.test()
async def test_ssi_three_positions(dut):
    """Three readings, across a turn boundary: each converted from Gray to binary."""
    await fw_boot(dut, "ssi_enc.s", uio_in=0x02)
    enc = Encoder(dut)
    for _ in range(200):
        await RisingEdge(dut.clk)
    while int(dut.uo_out.value) != 0x55:
        for _ in range(500):
            await RisingEdge(dut.clk)
        assert enc.t < 60_000, "not done"
    mem = dut.user_project.u_sram.mem
    got = [int(mem[64 + 2 * i].value) << 16 | int(mem[65 + 2 * i].value) for i in range(3)]
    assert got == POSITIONS, [(g >> 13, g & 0x1FFF) for g in got]
