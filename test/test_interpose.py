# SPDX-License-Identifier: Apache-2.0
"""In-line interposer (firmware/interpose_*.s): the chip sits in the wire between two
models. With nothing asked of it, neither can tell: every frame arrives, and its edges
are late by a bounded amount. Asked to change one thing, exactly that changes."""
import cocotb
from cocotb.triggers import RisingEdge, ReadOnly, Timer

from fw_tb import Bench, fw_boot, spawn
from msf_tb import uart_decode
from test_monitors import Wave, uart_wave

BIT = 104
AIN, AOUT, BIN, BOUT = 1, 2, 3, 4
LATE_MIN, LATE_MAX = 15, 30         # cycles, the whole range (see the test below)
MSG = [0x48, 0x5A, 0xC3, 0x00, 0xFF, 0x7E, 0x31]
BACK = [0x11, 0xEE, 0x69]


def items(data, gap=40):
    return [("byte", b, BIT, 1, gap) for b in data]


async def uart_through(dut, defines=None, a=MSG, b=BACK):
    """A sends on uio[1], B on uio[3]. Returns the bench and the events reported."""
    await fw_boot(dut, "interpose_uart.s", defines=defines, uio_in=1 << AIN | 1 << BIN)
    bench = Bench(dut)
    wa = Wave(1 << AIN, uart_wave(items(a), pin=AIN), start=1000)
    wb = Wave(1 << BIN, uart_wave(items(b), pin=BIN), start=2500)
    bench.add(wa)
    bench.add(wb)
    await bench.run(max(wa.end, wb.end) + 40 * 12)
    return bench, wa, wb


def report(bench):
    raw = uart_decode(bench.out, 12)
    assert -1 not in raw and len(raw) % 2 == 0, raw
    return [(chr(raw[i]), raw[i + 1]) for i in range(0, len(raw), 2)]


def delays(inp, out):
    """How late each edge of inp came out in out, in cycles."""
    ei = [k for k in range(1, len(inp)) if inp[k] != inp[k - 1]]
    eo = [k for k in range(1, len(out)) if out[k] != out[k - 1]]
    assert len(ei) == len(eo), (len(ei), len(eo))
    return [o - i for i, o in zip(ei, eo)]


@cocotb.test()
async def test_uart_pass_through_unseen(dut):
    """Both directions arrive whole, and each edge is late by a bounded amount: the
    loop's 16 cycles and the input synchroniser, with no edge ever crossing a bit."""
    bench, _, _ = await uart_through(dut)
    assert uart_decode(bench.out, BIT, pin=AOUT, start_at=500) == MSG
    assert uart_decode(bench.out, BIT, pin=BOUT, start_at=500) == BACK
    pad_a = bench.bits(AIN)
    out_a = [(v >> AOUT) & 1 for v in bench.out]
    d = delays(pad_a[500:], out_a[500:])
    dut._log.info(f"A to B: edges {min(d)}..{max(d)} cycles late")
    assert LATE_MIN <= min(d) and max(d) <= LATE_MAX, (min(d), max(d))
    assert report(bench) == [("F", b) for b in MSG]


@cocotb.test()
async def test_uart_lateness_over_every_phase(dut):
    """At 104 cycles a bit the edges only ever meet the wire's 16-cycle loop at two
    points of it, so this sends edges 101 cycles apart to meet it at all of them: the
    lateness the header states is the whole range."""
    lv = []
    for k in range(40):
        lv += [(k & 1) ^ 1] * 101
    await fw_boot(dut, "interpose_uart.s", uio_in=1 << AIN | 1 << BIN)
    bench = Bench(dut)
    bench.add(Wave(1 << AIN, [v << AIN for v in lv], start=1000))
    await bench.run(1000 + len(lv) + 100)
    d = delays(bench.bits(AIN)[500:], [(v >> AOUT) & 1 for v in bench.out][500:])
    dut._log.info(f"edges {min(d)}..{max(d)} cycles late over {len(d)} edges")
    assert (min(d), max(d)) == (LATE_MIN, LATE_MAX), (min(d), max(d))


@cocotb.test()
async def test_uart_flip_one_bit_of_one_frame(dut):
    """Bit 3 of frame 2 changes, only going from A to B, and nothing else does."""
    bench, _, _ = await uart_through(dut, {"ACTION": 1, "N": 2, "K": 3})
    want = list(MSG)
    want[2] ^= 1 << 3
    assert uart_decode(bench.out, BIT, pin=AOUT, start_at=500) == want
    assert uart_decode(bench.out, BIT, pin=BOUT, start_at=500) == BACK
    ev = report(bench)
    assert ("A", 2) in ev and [e for e in ev if e[0] == "F"] == [("F", b) for b in MSG], ev


@cocotb.test()
async def test_uart_flip_the_first_frame_and_last_bit(dut):
    """The ends of the range: frame 0, data bit 7."""
    bench, _, _ = await uart_through(dut, {"ACTION": 1, "N": 0, "K": 7})
    want = list(MSG)
    want[0] ^= 0x80
    assert uart_decode(bench.out, BIT, pin=AOUT, start_at=500) == want


@cocotb.test()
async def test_uart_drop_one_frame(dut):
    """Frame 3 never arrives, the line stays high for it with no glitch, and the frames
    around it are untouched."""
    bench, _, _ = await uart_through(dut, {"ACTION": 2, "N": 3})
    got = uart_decode(bench.out, BIT, pin=AOUT, start_at=500)
    assert got == MSG[:3] + MSG[4:], got
    assert uart_decode(bench.out, BIT, pin=BOUT, start_at=500) == BACK


class SpiController:
    """Mode 0 controller on ui_in, one transfer. half = half period, cycles."""

    def __init__(self, dut, tx, half):
        self.dut, self.tx, self.half, self.rx, self.done = dut, tx, half, [], False
        spawn(self._run())

    async def _set(self, sck, mosi, cs):
        self.dut.ui_in.value = sck << 2 | mosi << 3 | cs << 5 | 1

    async def _cycles(self, n):
        for _ in range(n):
            await RisingEdge(self.dut.clk)

    async def _run(self):
        await self._cycles(600)
        await Timer(1, unit="ns")
        await self._set(0, 0, 0)
        await self._cycles(self.half)
        for b in self.tx:
            v = 0
            for i in range(7, -1, -1):
                await Timer(1, unit="ns")
                await self._set(0, (b >> i) & 1, 0)
                await self._cycles(self.half)
                await ReadOnly()
                v = v << 1 | (int(self.dut.uio_out.value) >> 4) & 1
                await Timer(1, unit="ns")
                await self._set(1, (b >> i) & 1, 0)
                await self._cycles(self.half)
            self.rx.append(v)
        await Timer(1, unit="ns")
        await self._set(0, 0, 0)
        await self._cycles(self.half)
        await Timer(1, unit="ns")
        await self._set(0, 0, 1)
        self.done = True


class SpiDevice:
    """Mode 0 device behind the chip: samples uo_out's MOSI on rising SCK, and answers
    the complement of the last byte on uio[7]."""

    def __init__(self):
        self.got, self.sck, self.sr, self.n, self.tx, self.miso = [], 0, 0, 0, 0xFF, 1

    def step(self, k, bench):
        uo = bench.uo[k]
        sck, mosi = (uo >> 2) & 1, (uo >> 3) & 1
        if sck and not self.sck:
            self.sr = (self.sr << 1 | mosi) & 0xFF
            self.n += 1
            if self.n == 8:
                self.got.append(self.sr)
                self.tx, self.n = self.sr ^ 0xFF, 0
        elif self.sck and not sck:
            self.miso = (self.tx >> (7 - self.n)) & 1
        self.sck = sck
        return 1 << 7, self.miso << 7


async def spi_through(dut, data, half, defines=None):
    await fw_boot(dut, "interpose_spi.s", defines=defines)
    dev = SpiDevice()
    bench = Bench(dut)
    bench.add(dev)
    ctl = SpiController(dut, data, half)
    await bench.until(lambda b: ctl.done, 200000, "the transfer")
    await bench.run(100)
    return ctl, dev


DATA = [0xA5, 0x3C, 0x81, 0x7E]
MORE = [0x55, 0xAA, 0x33, 0xCC, 0x0F, 0xF0, 0x96, 0x69]
READ_MIN, MOSI_MIN, FLIP_MIN = 35, 12, 22     # half periods, as in the firmware's header


def answers(data):
    return [b ^ 0xFF for b in data[:-1]]


@cocotb.test()
async def test_spi_pass_through(dut):
    """A transfer through the chip: the device gets every byte, and the controller reads
    its answers, down to the half period the firmware's header states."""
    for data, half in ((DATA, 48), (DATA, READ_MIN), (MORE, READ_MIN)):
        ctl, dev = await spi_through(dut, data, half)
        assert dev.got == data, (half, [hex(x) for x in dev.got])
        assert ctl.rx[1:] == answers(data), (half, [hex(x) for x in ctl.rx])


@cocotb.test()
async def test_spi_the_stated_limits_are_the_limits(dut):
    """One cycle below the read limit the answer comes back after the controller has
    read it, while MOSI still arrives, and it goes on arriving down to the wire's loop."""
    ctl, dev = await spi_through(dut, DATA, READ_MIN - 1)
    assert dev.got == DATA and ctl.rx[1:] != answers(DATA), [hex(x) for x in ctl.rx]
    ctl, dev = await spi_through(dut, DATA, MOSI_MIN)
    assert dev.got == DATA, [hex(x) for x in dev.got]


@cocotb.test()
async def test_spi_flip_one_bit(dut):
    """One bit of one byte changes on the way to the device, and the rest arrive; with
    thread 1 counting, down to the half period the header states, and not below it."""
    want = list(DATA)
    want[1] ^= 1 << (7 - 3)
    for half in (48, FLIP_MIN):
        ctl, dev = await spi_through(dut, DATA, half, {"FLIP": 1, "N": 1, "K": 3})
        assert dev.got == want, (half, [hex(x) for x in dev.got])
    ctl, dev = await spi_through(dut, DATA, FLIP_MIN - 1, {"FLIP": 1, "N": 1, "K": 3})
    assert dev.got != want, [hex(x) for x in dev.got]
