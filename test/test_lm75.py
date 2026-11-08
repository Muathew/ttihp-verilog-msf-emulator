# SPDX-License-Identifier: Apache-2.0
"""LM75B thermometer (firmware/lm75.s) against a device written from NXP's LM75B data
sheet: address 0x48; the pointer selects Temp (0x00, two bytes), Conf (0x01, one),
Thyst (0x02, two) and Tos (0x03, two); a write's first byte is the pointer, the rest
the register, MSB first; a read starts at the pointer. The temperature changes between
conversions: 23.5, -10.25 and 125 degrees (11 bits, 0.125 a step, left aligned)."""
import cocotb
from cocotb.triggers import RisingEdge

from fw_tb import fw_boot
from i2c_dev import I2cDevice

TEMPS = [23.5, -10.25, 125.0]


def raw(t):
    return (int(round(t * 8)) & 0x7FF) << 5


class Lm75(I2cDevice):
    def __init__(self, dut):
        super().__init__(dut, 0x48)
        self.regs = {0: raw(TEMPS[0]), 1: 0, 2: 0x4B00, 3: 0x5000}   # Thyst 75, Tos 80
        self.ptr, self.n, self.reads = 0, 0, 0
        self.written = []

    def begin(self, read):
        self.n = 0
        if read and self.ptr == 0:
            self.regs[0] = raw(TEMPS[min(self.reads, len(TEMPS) - 1)])
            self.reads += 1

    def write(self, b):
        if self.n == 0:
            self.ptr = b & 3
        else:
            size = 1 if self.ptr == 1 else 2
            v = self.regs[self.ptr]
            if size == 1:
                v = b
            elif self.n == 1:
                v = b << 8 | (v & 0xFF)
            else:
                v = (v & 0xFF00) | b
            self.regs[self.ptr] = v
        self.n += 1

    def read(self):
        size = 1 if self.ptr == 1 else 2
        v = self.regs[self.ptr]
        b = v if size == 1 else (v >> 8 if self.n % 2 == 0 else v & 0xFF)
        self.n += 1
        return b & 0xFF


@cocotb.test()
async def test_lm75_limits_and_readings(dut):
    """Tos 60, Thyst 55, Conf 0; three readings stored raw and in eighths of a degree."""
    await fw_boot(dut, "lm75.s", defines={"GAP_P": 1}, uio_in=0x60)
    dev = Lm75(dut)
    for _ in range(1000):
        await RisingEdge(dut.clk)
    while int(dut.uo_out.value) not in (0x55, 0xEE):
        for _ in range(1000):
            await RisingEdge(dut.clk)
        assert dev.t < 400_000, "no result"
    assert int(dut.uo_out.value) == 0x55
    assert dev.regs[3] == 0x3C00 and dev.regs[2] == 0x3700 and dev.regs[1] == 0
    mem = dut.user_project.u_sram.mem
    got_raw = [int(mem[64 + i].value) for i in range(3)]
    got = [int(mem[72 + i].value) for i in range(3)]
    assert got_raw == [raw(t) for t in TEMPS], [hex(x) for x in got_raw]
    signed = [g - 0x10000 if g & 0x8000 else g for g in got]
    assert signed == [int(t * 8) for t in TEMPS], signed
    # No sigrok check: its lm75 decoder takes the pointer byte of a register write as
    # temperature data ("the temperature register is read-only") and misreads the
    # readings after it, against both the LM75B and the original LM75 data sheets.
