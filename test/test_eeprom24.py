# SPDX-License-Identifier: Apache-2.0
"""24C02 page write and read (firmware/eeprom24.s) against an EEPROM written from
Microchip's 24LC02B data sheet: control byte 0x50; a write's first byte is the word
address, the bytes after it fill the page, the address wrapping within its 8 bytes;
the write cycle starts at the STOP and lasts 5 ms, during which the control byte is
not acknowledged; a read continues from the address counter."""
import cocotb
from cocotb.triggers import RisingEdge

from fw_tb import fw_boot, save_trace
from i2c_dev import I2cDevice
from pins import samples

PAGE = list(b"MSF v3.0")
TWC = 60000                       # 5 ms


class Eeprom(I2cDevice):
    def __init__(self, dut):
        super().__init__(dut, 0x50)
        self.mem, self.ptr, self.busy_until = [0xFF] * 256, 0, 0
        self.first, self.buf, self.nacks = False, [], 0

    def ack_address(self, read):
        if self.t < self.busy_until:
            self.nacks += 1
            return False
        return True

    def begin(self, read):
        self.first, self.buf = not read, []

    def write(self, b):
        if self.first:
            self.ptr, self.first = b, False
        else:
            self.buf.append((self.ptr, b))
            self.ptr = (self.ptr & 0xF8) | ((self.ptr + 1) & 7)

    def read(self):
        b = self.mem[self.ptr]
        self.ptr = (self.ptr + 1) & 0xFF
        return b

    def end(self):
        if self.buf:                    # a page write: the cycle starts now
            for a, b in self.buf:
                self.mem[a] = b
            self.busy_until = self.t + TWC
            self.buf = []


@cocotb.test()
async def test_eeprom24_page_write_poll_read(dut):
    """8 bytes at 0x10, acknowledge polling through the 5 ms cycle, read back equal."""
    await fw_boot(dut, "eeprom24.s", uio_in=0x60)
    dev = Eeprom(dut)
    for _ in range(1000):
        await RisingEdge(dut.clk)
    while int(dut.uo_out.value) not in (0x55, 0xEE):
        for _ in range(1000):
            await RisingEdge(dut.clk)
        assert dev.t < 300_000, "no result"
    assert int(dut.uo_out.value) == 0x55
    assert dev.mem[0x10:0x18] == PAGE
    mem = dut.user_project.u_sram.mem
    assert [int(mem[64 + i].value) for i in range(8)] == PAGE
    polls = int(mem[80].value)
    assert polls == dev.nacks and polls > 10, (polls, dev.nacks)
    t1 = dev.changes[-1][0] + 100
    hx = " ".join(f"{b:02X}" for b in PAGE)
    lines = ([f"Page write (addr=10, 8 bytes): {hx}"] + ["Warning: No reply from slave!"] * dev.nacks
             + [f"Sequential random read (addr=10, 8 bytes): {hx}"])
    save_trace("eeprom24", samples(dev.changes, 0, t1, 4), "i2c:scl=6:sda=5,eeprom24xx",
               "eeprom24xx=ops:warnings", lines, {"pin": 5, "hold": 40}, rate=3_000_000)
