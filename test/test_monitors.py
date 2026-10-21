# SPDX-License-Identifier: Apache-2.0
"""Bus monitors (firmware/mon_*.s): the chip listens to a bus without
driving it, thread 0 decodes and checks the protocol, thread 1 reports
events to the host as two UART bytes each (firmware/report.inc). The tests
drive the bus from models (some with deliberate faults) and decode the
report stream at the chip's pins.
"""
import random

import cocotb

from fw_tb import Bench, fw_boot, events

REPORT_BIT = 12          # report.inc default: 1 Mbaud at 12 MHz


class Wave:
    """Drives some uio pins from a precomputed list of per-cycle levels
    (other cycles: `idle`). levels[i] is a dict-free int of pin bits."""

    def __init__(self, mask, levels, start=60, idle=None):
        self.mask, self.levels, self.start = mask, levels, start
        self.idle = mask if idle is None else idle
        self.end = start + len(levels)

    def step(self, k, bench):
        i = k - self.start
        return self.mask, self.levels[i] if 0 <= i < len(self.levels) else self.idle


# ============================================================================
# UART
# ============================================================================
LINE = 1
BIT = 104


def uart_wave(items, pin=LINE):
    """items: ("byte", value, bit_cycles, stop_level, idle_after)
              ("glitch", low_cycles, idle_after)
    -> per-cycle levels of the line (bit `pin`)."""
    lv = []
    for it in items:
        if it[0] == "byte":
            _, v, bit, stop, idle = it
            bits = [0] + [(v >> i) & 1 for i in range(8)] + [stop]
            lv += [b for b in bits for _ in range(bit)]
            lv += [1] * idle
        else:
            _, low, idle = it
            lv += [0] * low + [1] * idle
    return [b << pin for b in lv]


async def mon_run(dut, firmware, wave, defines=None, extra=400, pull=0):
    await fw_boot(dut, firmware, defines=defines, uio_in=wave.idle)
    bench = Bench(dut, pull=pull)
    bench.add(wave)
    await bench.run(wave.end + extra)
    return events(bench.out, REPORT_BIT, pin=0), bench


def first_rise_cells(v):
    """Bits the line is low for at the start of a frame (start bit + the
    data bits that are 0 before the first 1; the stop bit ends it)."""
    n = 1
    while n < 9 and not (v >> (n - 1)) & 1:
        n += 1
    return n


@cocotb.test()
async def test_mon_uart_decodes_and_times(dut):
    """Bytes, a framing error, a glitch and two frames at the wrong baud
    rate: every byte is decoded, and the bit-time offset of a frame's first
    rising edge is reported when it exceeds TOL (BIT >> 4 = 6 cycles)."""
    tol = BIT >> 4
    items = [("byte", 0x55, BIT, 1, 200), ("byte", 0xA6, BIT, 1, 200),
             ("byte", 0x10, BIT + 5, 1, 200),      # +4.8 %, 5 low cells: 25 cycles late
             ("byte", 0x01, BIT + 5, 1, 200),      # same rate, 1 low cell: 5 cycles, under TOL
             ("byte", 0x80, BIT - 3, 1, 200),      # -2.9 %, 8 low cells: 24 cycles early
             ("byte", 0x3C, BIT, 0, 300),          # stop bit low
             ("glitch", 20, 1500),                 # receivers see a 0xFF out of it
             ("byte", 0x42, BIT, 1, 200)]
    got, _ = await mon_run(dut, "mon_uart.s", Wave(1 << LINE, uart_wave(items)))
    want = []
    for it in items:
        if it[0] == "glitch":
            want += [("G", it[1]), ("U", 0xFF)]
            continue
        _, v, bit, stop, _ = it
        off = first_rise_cells(v) * (bit - BIT)
        if abs(off) > tol:
            want.append(("B", off & 0xFF))
        want.append(("U" if stop else "F", v))
    # edges are seen in thread 0's own slots: a timed value may be one cycle off
    assert len(got) == len(want), f"{got} != {want}"
    for (gc, gd), (wc, wd) in zip(got, want):
        assert gc == wc, f"{got} != {want}"
        if wc in "BG":
            assert abs(((gd - wd + 128) & 0xFF) - 128) <= 1, f"{(gc, gd)} != {(wc, wd)}"
        else:
            assert gd == wd, f"{got} != {want}"


@cocotb.test()
async def test_mon_uart_break(dut):
    """A break (line low for two frames) is a framing error with data 0;
    the next byte after it is decoded and timed normally."""
    items = [("byte", 0x31, BIT, 1, 300), ("glitch", 20 * BIT, 600),
             ("byte", 0x10, BIT + 5, 1, 300)]
    got, _ = await mon_run(dut, "mon_uart.s", Wave(1 << LINE, uart_wave(items)))
    assert [e[0] for e in got] == ["U", "F", "B", "U"], got
    assert got[0][1] == 0x31 and got[1][1] == 0 and got[3][1] == 0x10
    assert abs(got[2][1] - 25) <= 1, got


@cocotb.test()
async def test_mon_uart_back_to_back(dut):
    """A burst with no idle between frames: the RX engine decodes every
    byte; the core cannot time each frame then, and must not invent errors."""
    rng = random.Random(3)
    data = [rng.randrange(256) for _ in range(20)]
    items = [("byte", v, BIT, 1, 0) for v in data] + [("byte", 0x00, BIT, 1, 0)]
    got, _ = await mon_run(dut, "mon_uart.s", Wave(1 << LINE, uart_wave(items)))
    assert got == [("U", v) for v in data + [0]], f"{got}"


# ============================================================================
# I2C
# ============================================================================
SDA, SCL = 5, 6
HMIN, LMIN = 48, 56
BOOT = 400          # the I2C monitor clears its queue first (~300 cycles)


class I2cWave:
    """Builds the levels of an I2C bus (both lines driven: the monitor only
    listens) with exact phase lengths, and the events the monitor must
    report for it. Phase lengths in cycles; `low` / `high` of a bit can be
    overridden to break the timing rules on purpose."""

    def __init__(self, tlow=60, thigh=60, thd=6, tss=60, tbuf=100):
        self.tlow, self.thigh, self.thd, self.tss, self.tbuf = tlow, thigh, thd, tss, tbuf
        self.lv, self.want = [], []
        self.sda, self.scl = 1, 1
        self.nbits = 0                      # bits of the current byte
        self.byte = 0

    def hold(self, n):
        self.lv += [self.sda << SDA | self.scl << SCL] * n

    def idle(self, n):
        self.hold(n)

    def start(self):
        if self.scl == 0:                   # repeated START: SDA up, SCL up
            self.hold(self.thd)
            self.sda = 1
            self.hold(self.tlow - self.thd)
            self.scl = 1
        self.hold(self.tss)
        self.sda = 0
        if self.nbits:
            self.want.append(("X", self.nbits))
        self.want.append(("S", 0))
        self.hold(self.tss)
        self.scl, self.nbits, self.byte = 0, 0, 0

    def stop(self):
        self.hold(self.thd)
        self.sda = 0
        self.hold(self.tlow - self.thd)
        self.scl = 1
        self.hold(self.tss)
        self.sda = 1
        if self.nbits:
            self.want.append(("X", self.nbits))
        self.want.append(("P", 0))
        self.nbits = 0
        self.hold(self.tbuf)

    def bit(self, b, low=None, high=None):
        low = self.tlow if low is None else low
        high = self.thigh if high is None else high
        self.hold(min(self.thd, low - 1))
        self.sda = b
        self.hold(low - min(self.thd, low - 1))
        self.scl = 1
        self.hold(high)
        self.scl = 0
        self.nbits += 1
        self.byte = self.byte << 1 | b
        if self.nbits == 9:
            self.want.append(("N" if b else "A", self.byte >> 1))
            self.nbits, self.byte = 0, 0

    def byte_(self, v, ack=0, phases=None):
        for i in range(8):
            self.bit((v >> (7 - i)) & 1, **(phases or {}).get(i, {}))
        self.bit(ack, **(phases or {}).get(8, {}))

    def wave(self):
        return Wave(1 << SDA | 1 << SCL, self.lv)


LSLACK, HFLOOR = 10, 16  # mon_i2c.s: see its header


def check_i2c_events(got, want):
    """want: (code, value), or for 'H' / 'L' the phase's real length (0: a
    short phase on a byte's 9th bit, flagged without its length), checked
    against the monitor's documented accuracy: a high to +-1 (HFLOOR for
    anything shorter); a low from -1 to +LSLACK, or for one shorter than
    thread 0's own turnaround, anything below LMIN - 1."""
    assert [g[0] for g in got] == [w[0] for w in want], f"{got} != {want}"
    for (gc, gd), (wc, wd) in zip(got, want):
        if wc in "HL" and wd == 0:
            assert gd == 0, f"{(gc, gd)}: flagged, not measured"
        elif wc == "H":
            assert abs(gd - max(wd, HFLOOR)) <= 1, f"{(gc, gd)}: real high {wd}"
        elif wc == "L":
            ok = wd - 1 <= gd <= max(wd + LSLACK, LMIN - 2) if wd < 45 else                 wd - 1 <= gd <= wd + LSLACK
            assert ok, f"{(gc, gd)}: real low {wd}"
        else:
            assert gd == wd, f"{got} != {want}"


@cocotb.test()
async def test_mon_i2c_transfers(dut):
    """A write, a random read (repeated START), a NACK and a STOP at
    100 kHz, then the same with every phase at the legal minimum: decoded
    exactly, and no timing event (a legal bus never raises one)."""
    for tlow, thigh in ((60, 60), (LMIN, HMIN)):
        w = I2cWave(tlow=tlow, thigh=thigh)
        w.idle(BOOT)
        w.start(); w.byte_(0x50 << 1); w.byte_(0x12); w.byte_(0x34); w.stop()
        w.start(); w.byte_(0x50 << 1); w.byte_(0x07)
        w.start(); w.byte_(0x50 << 1 | 1); w.byte_(0xC3); w.byte_(0x3C, ack=1); w.stop()
        got, _ = await mon_run(dut, "mon_i2c.s", w.wave(), extra=3000)
        check_i2c_events(got, w.want)


@cocotb.test()
async def test_mon_i2c_random_legal(dut):
    """Random legal phases on every bit (lows LMIN..3 LMIN, highs
    HMIN..3 HMIN, some SCL lows of a few hundred cycles), random data, ACKs
    and NACKs, repeated STARTs: decoded exactly, no timing event."""
    rng = random.Random(7)
    w = I2cWave(tlow=LMIN, thigh=HMIN)
    w.idle(BOOT)
    stopped = True
    for _ in range(4):
        w.start()
        stopped = False
        for _ in range(rng.randrange(1, 5)):
            v = rng.randrange(256)
            ph = {i: {"low": rng.choice([rng.randrange(LMIN, 3 * LMIN), rng.randrange(150, 400)])
                      if rng.random() < 0.1 else rng.randrange(LMIN, 3 * LMIN),
                      "high": rng.randrange(HMIN, 3 * HMIN)} for i in range(9)}
            w.byte_(v, ack=rng.randrange(2), phases=ph)
        if rng.random() < 0.5:
            w.stop()
            stopped = True
    if not stopped:
        w.stop()
    got, _ = await mon_run(dut, "mon_i2c.s", w.wave(), extra=5000)
    assert not [g for g in got if g[0] in "HL"], got
    check_i2c_events(got, w.want)


@cocotb.test()
async def test_mon_i2c_protocol_errors(dut):
    """The first short phase of each byte, with its length (the rest of
    that byte is not timed); a short phase on a byte's 9th bit, flagged
    after it with length 0; a STOP after 3 bits of a byte and a repeated
    START after 5."""
    w = I2cWave()
    w.idle(BOOT)
    w.start()
    w.byte_(0x50 << 1, phases={3: {"high": 30}, 6: {"low": 30}})       # 'H' 30 only
    w.byte_(0x5A, phases={4: {"low": 40}})                              # 'L' 40
    w.byte_(0x33, phases={2: {"low": 50, "high": 20}})                  # both, one word
    w.byte_(0xC4, phases={8: {"high": 30}})                             # 9th bit: flagged
    w.byte_(0x96, phases={8: {"low": 44}})
    for b in (1, 0, 1):
        w.bit(b)
    w.stop()                              # STOP after 3 bits
    w.start(); w.byte_(0x50 << 1)
    for b in (0, 1, 1, 0, 1):
        w.bit(b)
    w.start()                             # repeated START after 5 bits
    w.byte_(0x50 << 1 | 1); w.stop()
    got, _ = await mon_run(dut, "mon_i2c.s", w.wave(), extra=4000)
    check_i2c_events(got, [("S", 0), ("H", 30), ("A", 0xA0), ("L", 40), ("A", 0x5A),
                           ("L", 50), ("H", 20), ("A", 0x33), ("A", 0xC4), ("H", 0),
                           ("A", 0x96), ("L", 0), ("X", 3), ("P", 0),
                           ("S", 0), ("A", 0xA0), ("X", 5), ("S", 0), ("A", 0xA1), ("P", 0)])


@cocotb.test()
async def test_mon_i2c_single_faults(dut):
    """One short phase per byte, on each data bit from the second, at
    100 kHz and at the legal minimum: each is reported once, with its
    length, and every byte still decodes. (A byte's first bit pushes the
    byte before it into the FIFO: its high is not checked.)"""
    w = I2cWave()
    w.idle(BOOT)
    w.start()
    want = []
    rng = random.Random(11)
    for base in ((60, 60), (LMIN, HMIN)):
        w.tlow, w.thigh = base
        for bit in range(1, 8):
            for kind, val in (("L", 30), ("L", 50), ("H", 12), ("H", 40)):
                v = rng.randrange(256)
                w.byte_(v, phases={bit: {"low" if kind == "L" else "high": val}})
                want += [(kind, val), ("A", v)]
    w.stop()
    got, _ = await mon_run(dut, "mon_i2c.s", w.wave(), extra=40000)
    check_i2c_events(got, [("S", 0)] + want + [("P", 0)])


@cocotb.test()
async def test_mon_i2c_fast_master(dut):
    """A master out of spec on every bit, at the monitor's limits (lows of
    16 cycles, bits of 72): every byte decodes, and each reports its first
    short phase only."""
    for low, high in ((16, 56), (34, 38), (40, 32), (56, 16)):
        w = I2cWave(tlow=low, thigh=high)
        w.idle(BOOT)
        w.start()
        data = [0x50 << 1, 0x00, 0xFF, 0x5A, 0xA5]
        for v in data:
            w.byte_(v)
        w.stop()
        got, _ = await mon_run(dut, "mon_i2c.s", w.wave(), extra=6000)
        assert [g for g in got if g[0] in "ANSPX"] ==             [("S", 0)] + [("A", v) for v in data] + [("P", 0)], (low, high, got)
        assert len([g for g in got if g[0] in "HL"]) >= len(data), (low, high, got)


@cocotb.test()
async def test_mon_i2c_long_low(dut):
    """An SCL low of 70000 cycles inside a byte (clock stretching past the
    16-bit cycle counter's wrap) reads as fine."""
    w = I2cWave()
    w.idle(BOOT)
    w.start()
    w.byte_(0xA6, phases={4: {"low": 70000}})
    w.byte_(0x3C)
    w.stop()
    got, _ = await mon_run(dut, "mon_i2c.s", w.wave(), extra=3000)
    check_i2c_events(got, w.want)


# ============================================================================
# SPI
# ============================================================================
SCKP, MOSIP, CSP, MISOP = 2, 3, 5, 7


class SpiWave:
    """Builds the levels of an SPI bus in a given mode (the controller and
    the device both modelled: the monitor only listens) and the events the
    monitor must report. `half` = SCK half period in cycles."""

    def __init__(self, mode):
        self.cpol, self.cpha = mode >> 1, mode & 1
        self.cs, self.sck, self.mosi, self.miso = 1, self.cpol, 0, 0
        self.lv, self.want = [], []

    def level(self):
        return (self.cs << CSP | self.sck << SCKP | self.mosi << MOSIP
                | self.miso << MISOP)

    def hold(self, n):
        self.lv += [self.level()] * n

    def select(self, setup=20):
        self.cs = 0
        self.mm = self.sck if self.sck != self.cpol else None   # 'M' later
        self.hold(setup)

    def bit(self, o, i, half):
        if self.cpha == 0:
            self.sck, self.mosi, self.miso = self.cpol, o, i
            self.hold(half)
            self.sck = 1 - self.cpol     # leading edge: sampled
            self.hold(half)
        else:
            self.sck, self.mosi, self.miso = 1 - self.cpol, o, i
            self.hold(half)
            self.sck = self.cpol         # trailing edge: sampled
            self.hold(half)

    def byte(self, o, i, half=30, gap=0):
        for k in range(8):
            self.bit((o >> (7 - k)) & 1, (i >> (7 - k)) & 1, half)
        self.want += [(">", o), ("<", i)]
        if self.cpha == 0:
            self.sck = self.cpol
        self.hold(gap)

    def deselect(self, left=0, after=200):
        self.hold(10)
        self.cs = 1
        if self.mm is not None:
            self.want.append(("M", self.mm))
        self.want.append(("E", left))
        self.hold(after)

    def wave(self):
        return Wave(1 << CSP | 1 << SCKP | 1 << MOSIP | 1 << MISOP, self.lv,
                    idle=1 << CSP | self.cpol << SCKP)


async def spi_mon(dut, w, mode, extra=4000):
    got, _ = await mon_run(dut, "mon_spi.s", w.wave(), defines={"MODE": mode},
                           extra=extra)
    return got


@cocotb.test()
async def test_mon_spi_modes(dut):
    """Every mode: transfers of random bytes on MOSI and MISO, with gaps
    between bytes and without: each byte decoded on both lines."""
    rng = random.Random(5)
    for mode in range(4):
        w = SpiWave(mode)
        w.hold(BOOT)
        for half, gap in ((30, 40), (40, 0)):
            w.select()
            for _ in range(4):
                w.byte(rng.randrange(256), rng.randrange(256), half=half, gap=gap)
            w.deselect()
        got = await spi_mon(dut, w, mode)
        assert got == w.want, f"mode {mode}: {got} != {w.want}"


@cocotb.test()
async def test_mon_spi_limits(dut):
    """At the documented limits: SCK periods of 28 cycles inside a byte,
    36 from a byte's last sampling edge to the next byte's first, 16 from
    CS low to the first sampling edge and 6 to the first SCK edge."""
    for mode in range(4):
        w = SpiWave(mode)
        w.hold(BOOT)
        w.select(setup=2 if w.cpha == 0 else 6)   # (first sampling edge 16 / 20 after CS)
        data = [(0xA5, 0x3C), (0x00, 0xFF), (0xFF, 0x00), (0x5A, 0xC3)]
        for o, i in data:
            w.byte(o, i, half=14, gap=8)     # 28 per bit, 28 + 8 = 36 between
        w.deselect()
        got = await spi_mon(dut, w, mode)
        assert got == w.want, f"mode {mode}: {got} != {w.want}"


@cocotb.test()
async def test_mon_spi_errors(dut):
    """A controller in another mode (its SCK idles high), CS rising in the
    middle of a byte, and a transfer of a single clock."""
    w = SpiWave(0)
    w.hold(BOOT)
    w.sck = 1                                # a mode 2 / 3 controller
    w.hold(20)
    w.select()                               # 'M' 1 at the end
    w.mosi, w.miso = 1, 0
    for _ in range(8):                       # 8 clocks idling high
        w.sck = 0; w.hold(30)
        w.sck = 1; w.hold(30)
    w.want += [(">", 0xFF), ("<", 0x00)]
    w.deselect()
    w.sck = 0
    w.hold(100)
    w.select()                               # mode 0 again
    w.byte(0x81, 0x7E)
    for b in (1, 0, 1):
        w.bit(b, 0, 30)
    w.sck = 0
    w.deselect(left=3)            # CS high after 3 bits
    w.select()
    w.bit(1, 1, 30)
    w.sck = 0
    w.deselect(left=1)                       # a single clock
    got = await spi_mon(dut, w, 0)
    assert got == w.want, f"{got} != {w.want}"
