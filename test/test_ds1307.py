# SPDX-License-Identifier: Apache-2.0
"""DS1307 clock set-up (firmware/ds1307.s) against a device written from Maxim's DS1307
data sheet: address 0x68; 64 registers (0x00-0x07 time, date and control in BCD,
0x08-0x3F RAM); a write's first byte is the register pointer, which counts up after
each byte written or read and wraps from 0x3F to 0x00."""
import cocotb
from cocotb.triggers import RisingEdge

from fw_tb import fw_boot, save_trace
from i2c_dev import I2cDevice
from pins import samples

SET = [0x00, 0x37, 0x14, 0x07, 0x08, 0x11, 0x26, 0x10, 0xA5]


class Ds1307(I2cDevice):
    def __init__(self, dut):
        super().__init__(dut, 0x68)
        self.mem, self.ptr, self.first = [0x80] + [0] * 63, 0, False   # CH set at power-up

    def begin(self, read):
        self.first = not read

    def write(self, b):
        if self.first:
            self.ptr, self.first = b & 0x3F, False
        else:
            self.mem[self.ptr] = b
            self.ptr = (self.ptr + 1) & 0x3F

    def read(self):
        b = self.mem[self.ptr]
        self.ptr = (self.ptr + 1) & 0x3F
        return b


@cocotb.test()
async def test_ds1307_set_and_read(dut):
    """Sunday 8 Nov 2026, 14:37:00, running, SQW 1 Hz, RAM 0x08 = 0xA5; read back."""
    await fw_boot(dut, "ds1307.s", uio_in=0x60)
    dev = Ds1307(dut)
    for _ in range(1000):
        await RisingEdge(dut.clk)
    while int(dut.uo_out.value) not in (0x55, 0xEE):
        for _ in range(1000):
            await RisingEdge(dut.clk)
        assert dev.t < 300_000, "no result"
    assert int(dut.uo_out.value) == 0x55
    assert dev.mem[:9] == SET, [hex(x) for x in dev.mem[:9]]
    assert not dev.mem[0] & 0x80 and not dev.mem[2] & 0x40, "running, 24-hour"
    mem = dut.user_project.u_sram.mem
    assert [int(mem[64 + i].value) for i in range(9)] == SET
    assert dev.events == ["S", "S", "S", "P"], dev.events
    t1 = dev.changes[-1][0] + 100
    # the data sheet leaves the day of the week's meaning to the user; sigrok counts
    # Sunday as 1, so it names the program's day 7 Saturday
    save_trace("ds1307", samples(dev.changes, 0, t1, 4), "i2c:scl=6:sda=5,ds1307",
               "ds1307=date-time:warnings", ["Read date/time: Saturday, 08.11.2026 14:37:00"],
               {"pin": 5, "hold": 40}, rate=3_000_000)
