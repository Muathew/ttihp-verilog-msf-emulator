# SPDX-License-Identifier: Apache-2.0
"""AM2302 host (firmware/am2302.s) against a sensor written from Aosong's AM2302 data
sheet: it answers a host low of 0.8 ms or more, 30 us after release, with low 80 us
and high 80 us, then 40 bits, each low 50 us and high 26 us (0) or 70 us (1), MSB
first: humidity x 10, temperature x 10 (bit 15 the sign), and the low byte of the sum
of the four bytes; then it releases the bus after 50 us of low."""
import cocotb
from cocotb.triggers import RisingEdge

from fw_tb import fw_boot, save_trace
from pins import samples

US = 12
BUS = 0x01
HUM, TEMP = 652, 0x8000 | 101          # 65.2 %RH, -10.1 degrees C


def frame(hum, temp, bad=False):
    b = [hum >> 8, hum & 0xFF, temp >> 8, temp & 0xFF]
    return b + [(sum(b) + (1 if bad else 0)) & 0xFF]


class Sensor:
    def __init__(self, dut, data):
        self.dut, self.t, self.drive, self.sched = dut, 0, 1, []
        self.low_from, self.prev, self.started = None, 1, False
        self.data, self.changes, self.host_low = data, [(0, 1)], None
        cocotb.start_soon(self.run())

    def answer(self, t):
        seq = [(30, 0), (80, 1), (80, 0)]       # (after us, then the level)
        for byte in self.data:
            for i in range(7, -1, -1):
                seq += [(50, 1), (70 if byte >> i & 1 else 26, 0)]
        seq += [(50, 1)]                        # the last low, then released
        for dt, lv in seq:
            t += dt * US
            self.sched.append((t, lv))

    async def run(self):
        dut = self.dut
        while True:
            await RisingEdge(dut.clk)
            self.t += 1
            while self.sched and self.sched[0][0] <= self.t:
                self.drive = self.sched.pop(0)[1]
            oe, out = int(dut.uio_oe.value) & BUS, int(dut.uio_out.value) & BUS
            host = 0 if oe and not out else 1
            line = host & self.drive
            dut.uio_in.value = line
            if line != self.prev:
                self.changes.append((self.t, line))
            if host == 0 and self.low_from is None:
                self.low_from = self.t
            if host == 1 and self.low_from is not None and not self.started:
                self.host_low = self.t - self.low_from
                if self.host_low >= 800 * US:
                    self.started = True
                    self.answer(self.t)
                self.low_from = None
            self.prev = line


async def read_sensor(dut, data):
    await fw_boot(dut, "am2302.s")
    s = Sensor(dut, data)
    for _ in range(200):
        await RisingEdge(dut.clk)
    while int(dut.uo_out.value) not in (0x55, 0xEE):
        for _ in range(1000):
            await RisingEdge(dut.clk)
        assert s.t < 120_000, "no result"
    return s


@cocotb.test()
async def test_am2302_reading(dut):
    """65.2 %RH and -10.1 degrees C, the checksum right: DONE and the values stored."""
    s = await read_sensor(dut, frame(HUM, TEMP))
    assert 800 * US <= s.host_low <= 20000 * US, s.host_low / US
    assert int(dut.uo_out.value) == 0x55
    mem = dut.user_project.u_sram.mem
    assert [int(mem[64 + i].value) for i in range(3)] == [HUM, TEMP, frame(HUM, TEMP)[4]]
    t1 = s.changes[-1][0] + 200 * US
    save_trace("am2302", samples(s.changes, 0, t1, 12), "am230x:sda=0", "am230x=results",
               [f"Humidity: {HUM / 10:.1f} %", f"Temperature: -{(TEMP & 0x7FFF) / 10:.1f} °C",
                "Checksum: OK"],
               {"pin": 0, "hold": 30}, rate=1_000_000)


@cocotb.test()
async def test_am2302_bad_checksum(dut):
    """The same reading with its checksum one off: BAD."""
    await read_sensor(dut, frame(HUM, TEMP, bad=True))
    assert int(dut.uo_out.value) == 0xEE
