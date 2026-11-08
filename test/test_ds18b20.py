# SPDX-License-Identifier: Apache-2.0
"""DS18B20 reading (firmware/ds18b20.s) against the 1-Wire device of test_buses.py
(Maxim's 1-Wire slots, ROM commands, READ SCRATCHPAD with its CRC-8) given CONVERT T
as Maxim's DS18B20 data sheet describes it: after 0x44, read slots return 0 until the
conversion is over, then 1, and the scratchpad holds the new temperature (16 bits,
1/16 degree, two's complement). The conversion here takes 20 ms."""
import cocotb
from cocotb.triggers import RisingEdge

from fw_tb import fw_boot
from test_buses import OneWireDevice, crc8_maxim

US = 12
DQ = 3
CONV = 20000 * US


def scratch(t16):
    return [t16 & 0xFF, (t16 >> 8) & 0xFF, 0x4B, 0x46, 0x7F, 0xFF, 0x0C, 0x10]


class Ds18b20(OneWireDevice):
    def __init__(self, temp16, bad_crc=False):
        super().__init__([0x28, 1, 2, 3, 4, 5, 6, 0])
        self.temp16, self.bad, self.done_at, self.k = temp16, bad_crc, None, 0

    def fall(self, k):
        self.k = k
        if self.state == "convert":
            if k < self.done_at:
                self.pull = (k, k + 30 * US)             # still converting: a 0
            else:
                s = scratch(self.temp16)
                self.scratch = s + [crc8_maxim(s) ^ (1 if self.bad else 0)]
                self.state = "idle"                      # done: 1s from now on
            return
        super().fall(k)

    def slot(self, line):
        if self.state == "convert":
            return
        if self.state == "func" and not self.out:      # the function byte
            self.bits.append(line)
            if len(self.bits) == 8:
                c = sum(x << i for i, x in enumerate(self.bits))
                self.bits = []
                if c == 0x44:
                    self.state, self.done_at = "convert", self.k + CONV
                elif c == 0xBE:
                    self.out = [(b >> i) & 1 for b in self.scratch for i in range(8)]
                else:
                    self.state = "idle"
            return
        super().slot(line)


async def run(dut, temp16, bad=False):
    await fw_boot(dut, "ds18b20.s", uio_in=1 << DQ)
    dev = Ds18b20(temp16, bad)
    k, prev_chip, fall_at = 0, False, None
    while int(dut.uo_out.value) not in (0x55, 0xEE):
        await RisingEdge(dut.clk)
        k += 1
        out, oe = int(dut.uio_out.value), int(dut.uio_oe.value)
        chip = bool((oe >> DQ) & 1 and not (out >> DQ) & 1)
        if chip and not prev_chip:
            fall_at = k
            dev.fall(k)
        if not chip and prev_chip and k - fall_at >= 480 * US:
            dev.reset(k)
        pulled = dev.pull[0] <= k < dev.pull[1]
        line = 0 if (chip or pulled) else 1
        if fall_at is not None and k == fall_at + 30 * US:
            dev.slot(line)
        dut.uio_in.value = line << DQ
        prev_chip = chip
        assert k < 600_000, "no result"
    return dev


@cocotb.test()
async def test_ds18b20_positive(dut):
    """+25.0625 degrees (0x0191): converted, read, CRC checked, kept."""
    await run(dut, 0x0191)
    assert int(dut.uo_out.value) == 0x55
    mem = dut.user_project.u_sram.mem
    s = scratch(0x0191)
    assert [int(mem[64 + i].value) for i in range(9)] == s + [crc8_maxim(s)]
    assert int(mem[73].value) == 0x0191


@cocotb.test()
async def test_ds18b20_negative_and_bad_crc(dut):
    """-10.125 degrees (0xFF5E) with its CRC one off: BAD."""
    await run(dut, 0xFF5E, bad=True)
    assert int(dut.uo_out.value) == 0xEE
