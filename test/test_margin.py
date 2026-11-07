# SPDX-License-Identifier: Apache-2.0
"""Margin finder (firmware/margin_*.s): the chip alone sweeps one timing parameter
on a device and reports where the device stops answering. Each device model below
has a limit set by the test, and the firmware has to find exactly that, with no
Python in the loop except the device itself."""
import cocotb

from fw_tb import Bench, DONE, fw_boot
from msf_tb import sram, uart_decode

REPORT_BIT = 12
M_LO, M_HI, M_TABLE = 8, 9, 16


def parse_report(raw):
    """'P' p p passes ... 'R' lo lo hi hi -> ({p: passes}, lo, hi)."""
    points, i = {}, 0
    while i < len(raw) and chr(raw[i]) == "P":
        points[raw[i + 1] | raw[i + 2] << 8] = raw[i + 3]
        i += 4
    assert chr(raw[i]) == "R", (i, raw[i:i + 5])
    return points, raw[i + 1] | raw[i + 2] << 8, raw[i + 3] | raw[i + 4] << 8


async def sweep(dut, firmware, device, defines=None, limit=3000000, pull=0):
    await fw_boot(dut, firmware, defines=defines, uio_in=pull)
    bench = Bench(dut, pull=pull)
    bench.add(device)
    await bench.until(lambda b: b.uo and b.uo[-1] == DONE, limit, "the sweep's end")
    raw = uart_decode(bench.out, REPORT_BIT)
    assert -1 not in raw, "framing error in the report"
    points, lo, hi = parse_report(raw)
    assert (sram(dut, M_LO), sram(dut, M_HI)) == (lo, hi), "data RAM differs from the report"
    return points, lo, hi


class UartEcho:
    """A UART device: samples each bit at the centre by its own nominal rate from
    the start edge, and echoes the byte at that rate if the stop bit was high."""

    def __init__(self, nominal, rx=1, tx=2):
        self.n, self.rx, self.tx = nominal, rx, tx
        self.state, self.t0, self.bits, self.reply, self.prev = "idle", 0, [], [], 1

    def step(self, k, bench):
        line = (bench.out[k] >> self.rx) & 1 if (bench.oe[k] >> self.rx) & 1 else 1
        if self.state == "idle" and self.prev and not line:
            self.state, self.t0, self.bits = "rx", k, []
        elif self.state == "rx":
            cell = len(self.bits)
            if k - self.t0 == self.n // 2 + cell * self.n:
                self.bits.append(line)
                if len(self.bits) == 10:
                    self.state = "idle"
                    if self.bits[0] == 0 and self.bits[9] == 1:
                        v = self.bits[1:9]
                        self.reply = [1] * self.n + [b for x in [0] + v + [1]
                                                     for b in [x] * self.n]
        self.prev = line
        out = self.reply.pop(0) if self.reply else 1
        return 1 << self.tx, out << self.tx


def uart_limits(nominal):
    """Where a mid-bit receiver reads 0x55: each of its samples, at (k + 1/2) nominal
    bits from the start edge, has to land in cell k of a frame of p cycles a bit, but
    the last may land in the idle line after the stop bit, which is high too."""
    def sample(k):
        return nominal // 2 + k * nominal
    good = [p for p in range(nominal // 2, nominal * 2)
            if all(k * p <= sample(k) < (k + 1) * p for k in range(9)) and 9 * p <= sample(9)]
    return good[0], good[-1]


@cocotb.test()
async def test_margin_uart_finds_the_known_limit(dut):
    """The same limit the Python sweep in test_faults finds (99..109 against a
    104-cycle receiver), now found by the chip on its own."""
    assert uart_limits(104) == (99, 109)
    points, lo, hi = await sweep(dut, "margin_uart.s", UartEcho(104), {"REPEATS": 3})
    assert (lo, hi) == (99, 109), (lo, hi, points)
    assert sorted(points) == list(range(94, 115))
    assert all((v == 3) == (99 <= p <= 109) for p, v in points.items()), points


@cocotb.test()
async def test_margin_uart_other_device(dut):
    """A device at another rate: the limit moves as the arithmetic says."""
    nom = 80
    want = uart_limits(nom)
    points, lo, hi = await sweep(dut, "margin_uart.s", UartEcho(nom),
                                 {"REPEATS": 2, "NOM": nom, "SWEEP_LO": nom - 8, "SWEEP_HI": nom + 8})
    assert (lo, hi) == want, (lo, hi, want, points)


@cocotb.test()
async def test_margin_uart_silent_device(dut):
    """Nothing connected: nothing passes, and the sweep still ends."""
    class Nothing:
        def step(self, k, bench):
            return 1 << 2, 1 << 2
    points, lo, hi = await sweep(dut, "margin_uart.s", Nothing(),
                                 {"REPEATS": 1, "SWEEP_LO": 100, "SWEEP_HI": 104})
    assert (lo, hi) == (0, 0) and set(points.values()) == {0}


class LimitedI2c:
    """An I2C target that loses a clock it cannot follow: an SCL low shorter than
    min_low, or a high shorter than min_high, is not counted as a bit, so the address
    comes out wrong and it does not ACK."""
    SDA, SCL = 5, 6

    def __init__(self, addr, min_low=0, min_high=0):
        self.addr, self.min_low, self.min_high = addr, min_low, min_high
        self.scl, self.sda, self.t = 1, 1, 0
        self.bits, self.active, self.pull, self.ack_next = [], False, 0, False
        self.bad, self.sample = False, None
        self.acks = 0

    def step(self, k, bench):
        out, oe = bench.out[k], bench.oe[k]
        chip = lambda p: (oe >> p) & 1 and not (out >> p) & 1
        scl, sda = int(not chip(self.SCL)), int(not (chip(self.SDA) or self.pull))
        if scl and self.scl and sda != self.sda:
            if not sda:                    # START: the SCL fall after it is not a bit
                self.active, self.bits, self.bad, self.pull = True, [], False, 0
                self.sample = None
            else:                          # STOP
                self.active, self.pull = False, 0
        if scl != self.scl:
            length = k - self.t
            self.t = k
            if self.active and scl:        # the low just ended
                if length < self.min_low:
                    self.bad = True
                self.sample = sda
            elif self.active and not scl and self.sample is None:
                pass
            elif self.active and not scl:  # the high just ended
                if length < self.min_high:
                    self.bad = True
                if self.pull and len(self.bits) >= 9:
                    self.pull, self.active = 0, False
                elif not self.bad:
                    self.bits.append(self.sample)
                    if len(self.bits) == 8:
                        v = sum(b << (7 - i) for i, b in enumerate(self.bits))
                        if v >> 1 == self.addr:
                            self.pull = 1
                            self.acks += 1
                            self.bits.append(0)
                        else:
                            self.active = False
                else:
                    self.active = False
        self.scl, self.sda = scl, sda
        return 1 << self.SDA, 0 if self.pull else 1 << self.SDA


@cocotb.test()
async def test_margin_i2c_low(dut):
    """A device that needs SCL low for 40 cycles: the sweep starts passing exactly
    there."""
    dev = LimitedI2c(0x50, min_low=40)
    points, lo, hi = await sweep(dut, "margin_i2c.s", dev, {"REPEATS": 1, "SWEEP": 0},
                                 pull=0x60)
    assert (lo, hi) == (40, 64), (lo, hi, points)
    assert all((v == 1) == (p >= 40) for p, v in points.items()), points


@cocotb.test()
async def test_margin_i2c_high(dut):
    """The same for the SCL high, with the low left at its default."""
    dev = LimitedI2c(0x50, min_high=33)
    points, lo, hi = await sweep(dut, "margin_i2c.s", dev, {"REPEATS": 1, "SWEEP": 1},
                                 pull=0x60)
    assert (lo, hi) == (33, 64), (lo, hi, points)


@cocotb.test()
async def test_margin_i2c_wrong_address(dut):
    """A device at another address never ACKs: nothing passes."""
    dev = LimitedI2c(0x51)
    points, lo, _ = await sweep(dut, "margin_i2c.s", dev,
                                {"REPEATS": 1, "SWEEP_LO": 60, "SWEEP_HI": 64}, pull=0x60)
    assert lo == 0 and set(points.values()) == {0}


class LimitedSpi:
    """An SPI device, mode 0, that answers the complement of the last byte, and that
    cannot follow a half period shorter than min_half: a bit it missed reads as 0."""
    SCK, MOSI, MISO, CS = 2, 3, 4, 5

    def __init__(self, min_half=0):
        self.min_half = min_half
        self.sck, self.cs, self.t = 0, 1, 0
        self.sr, self.n, self.tx = 0, 0, 0xFF
        self.miso = 1

    def step(self, k, bench):
        out = bench.out[k]
        sck, cs, mosi = (out >> self.SCK) & 1, (out >> self.CS) & 1, (out >> self.MOSI) & 1
        if cs and not self.cs:
            self.n, self.sr = 0, 0
        if not cs and sck != self.sck:
            length = k - self.t
            self.t = k
            if sck:                        # rising: sample
                ok = length >= self.min_half
                self.sr = (self.sr << 1 | (mosi if ok else 0)) & 0xFF
                self.n += 1
                if self.n == 8:
                    self.n = 0
                    self.tx = self.sr ^ 0xFF
            else:                          # falling: the next bit out
                self.miso = (self.tx >> (7 - self.n)) & 1
        self.sck, self.cs = sck, cs
        return 1 << self.MISO, self.miso << self.MISO


@cocotb.test()
async def test_margin_spi(dut):
    """A device that cannot follow half periods under 28 cycles."""
    dev = LimitedSpi(min_half=28)
    points, lo, hi = await sweep(dut, "margin_spi.s", dev, {"REPEATS": 1})
    assert (lo, hi) == (28, 48), (lo, hi, points)
