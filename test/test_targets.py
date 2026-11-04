# SPDX-License-Identifier: Apache-2.0
"""Device emulation: the chip as an SPI or I2C target (firmware/spi_target.s,
firmware/i2c_target.s) against controller models written from the protocol
specs. Besides the data, the tests measure the response latencies at the
pins and assert the exact values derived from the ISA's cycle costs
(docs/instruments.md), then find the fastest bus each target keeps up with.
"""
import random

import cocotb

from fw_tb import Bench, fw_boot
from msf_tb import sram, uart_decode

SCK, MOSI, MISO, CS = 2, 3, 4, 5
STATUS = 64


# ============================================================================
# SPI
# ============================================================================
class SpiController:
    """SPI controller (modes 0-3) clocking `transfers` (lists of bytes) into
    the chip, one CS-low frame each. Timing in core cycles: `half` = SCK half
    period, `lead` = CS low -> first SCK edge, `lag` = extra cycles after the
    last bit before CS high, `gap` = CS high time, `ibg` = extra SCK-idle
    cycles between bytes. `abort` = (transfer, bit) raises CS early.

    MISO is sampled at each sampling edge from the chip's output that cycle
    (the controller changes SCK in cycle k, so out[k] is what it sees).
    timing() then reports per sampled bit how long MISO had been valid."""

    def __init__(self, mode, transfers, half=8, lead=8, lag=0, gap=40, ibg=0,
                 start=30, abort=None, sck_pin=SCK, mosi_pin=MOSI,
                 miso_pin=MISO, cs_pin=CS):
        self.cpol, self.cpha = (mode >> 1) & 1, mode & 1
        self.sck_pin, self.mosi_pin = sck_pin, mosi_pin
        self.miso_pin, self.cs_pin = miso_pin, cs_pin
        self.start = start
        self.wave = []                    # (sck, mosi, cs) per cycle from `start`
        self.sample_at = {}               # wave index -> (transfer, byte, bit)
        w = self.wave
        idle = self.cpol
        for ti, tr in enumerate(transfers):
            bits = [(b >> (7 - k)) & 1 for b in tr for k in range(8)]
            if abort is not None and abort[0] == ti:
                bits = bits[:abort[1]]
            mosi = bits[0] if bits and not self.cpha else 0
            w += [(idle, mosi, 0)] * lead
            for n, bit in enumerate(bits):
                if self.cpha:                   # shift on the leading edge, sample on the trailing
                    mosi = bit
                    w += [(1 - idle, mosi, 0)] * half
                    self.sample_at[len(w)] = (ti, n)
                    w += [(idle, mosi, 0)] * half
                else:                           # sample on the leading edge, shift on the trailing
                    self.sample_at[len(w)] = (ti, n)
                    w += [(1 - idle, mosi, 0)] * half
                    mosi = bits[n + 1] if n + 1 < len(bits) else 0
                    w += [(idle, mosi, 0)] * half
                if n % 8 == 7:
                    w += [(idle, mosi, 0)] * ibg
            w += [(idle, mosi, 0)] * lag
            w += [(idle, 0, 1)] * gap
        self.end = start + len(w)
        self.miso = [[] for _ in transfers]   # sampled bits per transfer
        self.samples = []                     # (cycle, transfer, bit number)
        self.selects = [start + i for i in range(1, len(w)) if w[i][2] < w[i - 1][2]]
        if w and w[0][2] == 0:
            self.selects.insert(0, start)

    def step(self, k, bench):
        i = k - self.start
        if i < 0 or i >= len(self.wave):
            sck, mosi, cs = self.cpol, 0, 1
        else:
            sck, mosi, cs = self.wave[i]
            if i in self.sample_at:
                ti, n = self.sample_at[i]
                self.miso[ti].append((bench.out[k] >> self.miso_pin) & 1)
                self.samples.append((k, ti, n))
        mask = (1 << self.sck_pin) | (1 << self.mosi_pin) | (1 << self.cs_pin)
        value = (sck << self.sck_pin) | (mosi << self.mosi_pin) | (cs << self.cs_pin)
        return mask, value

    def timing(self, bench):
        """Per sampled bit after the first of its transfer:
        (transfer, bit, turn, setup) where turn = cycles from the previous
        sampling edge to MISO's last change before this edge (None if MISO
        kept its level) and setup = cycles MISO had been stable at the edge."""
        lv = bench.bits(self.miso_pin, "out")
        changes = [c for c in range(1, len(lv)) if lv[c] != lv[c - 1]]
        out, prev = [], None
        for k, ti, n in self.samples:
            last = max((c for c in changes if c <= k), default=0)
            turn = last - prev[0] if prev and prev[1] == ti and last > prev[0] else None
            if prev and prev[1] == ti:
                out.append((ti, n, turn, k - last))
            prev = (k, ti)
        return out

    def bytes_read(self):
        out = []
        for bits in self.miso:
            out.append([sum(b << (7 - j) for j, b in enumerate(bits[i:i + 8]))
                        for i in range(0, len(bits) - len(bits) % 8, 8)])
        return out


def spi_ref(regs, status, transfers):
    """The 64-register device of spi_target.s, from its header comment."""
    regs, out = list(regs), []
    for tr in transfers:
        addr, read, miso = tr[0] & 0x3F, tr[0] >> 7, [status]
        for b in tr[1:]:
            miso.append(regs[addr])
            if not read:
                regs[addr] = b
            addr = (addr + 1) & 0x3F
        out.append(miso)
    return out, regs


async def spi_run(dut, mode, transfers, regs, status=0xA5, **timing):
    data = {a: regs[a] for a in range(64)}
    data[STATUS] = status
    await fw_boot(dut, "spi_target.s", data=data, defines={"MODE": mode})
    bench = Bench(dut)
    ctl = bench.add(SpiController(mode, transfers, **timing))
    ctl.bench = bench
    await bench.run(ctl.end + 20)
    return ctl, [sram(dut, a) for a in range(64)]


def random_spi_case(rng, n=6):
    regs = [rng.randrange(256) for _ in range(64)]
    transfers = [
        [0x00 | 0x3D] + [rng.randrange(256) for _ in range(5)],     # write across the wrap
        [0x80 | 0x3D] + [0] * 5,                                    # read it back
        [0x80 | rng.randrange(64)],                                 # command only
    ]
    for _ in range(n):
        rw = rng.choice((0, 0x80))
        transfers.append([rw | rng.randrange(64) | rng.choice((0, 0x40))] +
                         [rng.randrange(256) for _ in range(rng.randrange(1, 5))])
    return regs, transfers


@cocotb.test()
async def test_spi_target_all_modes(dut):
    """Register reads and writes in every mode at SCK = clk/16 (the fastest)
    and clk/80 (every SCK wait times out and re-checks CS on the way)."""
    rng = random.Random(7)
    for mode, half in [(m, 8) for m in range(4)] + [(0, 40), (3, 40)]:
        regs, transfers = random_spi_case(rng)
        ctl, after = await spi_run(dut, mode, transfers, regs, half=half)
        want, want_regs = spi_ref(regs, 0xA5, transfers)
        assert ctl.bytes_read() == want, f"mode {mode}: MISO {ctl.bytes_read()} != {want}"
        assert after == want_regs, f"mode {mode}: registers after the writes differ"
        assert min(s for *_, s in ctl.timing(ctl.bench)) >= 1, \
            f"mode {mode}: MISO changed at a sampling edge"


def classify(tr, n):
    """Which code path produced the MISO bit sampled as bit n of a transfer."""
    if n == 8:
        return "command, odd address" if tr[0] & 1 else "command, even address"
    return "between data bytes" if n % 8 == 0 else "inside a byte"


@cocotb.test()
async def test_spi_target_latency(dut):
    """The MISO turnarounds derived from the ISA cycle costs, at the pins:
    5 cycles inside a byte, 8 between data bytes, 5 / 13 after the command
    byte (even / odd address); MISO driven 4 cycles after CS falls. At the
    fastest SCK (period 16) the last command bit is seen 2 cycles late, so
    the odd-address bit lands 15 cycles after the edge: one cycle to spare."""
    rng = random.Random(11)
    regs = [rng.randrange(256) for _ in range(64)]
    transfers = [[0x80 | a] + [0] * 3 for a in (0x10, 0x11, 0x2A, 0x2B, 0x3E, 0x3F)]
    transfers += [[a, 0x5A, 0xA5, 0x3C] for a in (0x06, 0x07)]
    # SCK half periods up to POLL + 1 = 13 cycles never time out a wait, so
    # these are the bare code paths; slower clocks may add up to ~8 cycles
    # (a wait times out, checks CS and waits again) with far more slack.
    # odd address: the even register's bit 7 goes out at 5 cycles and the
    # odd one's replaces it at 13 when they differ (the worst case)
    for half, expect in ((12, {"inside a byte": {5}, "between data bytes": {8},
                               "command, even address": {5}, "command, odd address": {5, 13}}),
                         (8, {"command, odd address": {7, 15}})):
        ctl, _ = await spi_run(dut, 0, transfers, regs, half=half)
        assert ctl.bytes_read() == spi_ref(regs, 0xA5, transfers)[0]
        seen = {}
        for ti, n, turn, setup in ctl.timing(ctl.bench):
            assert setup >= 1, f"half {half}: MISO not valid at the edge (transfer {ti}, bit {n})"
            if turn is not None:
                seen.setdefault(classify(transfers[ti], n), set()).add(turn)
        for path, turns in expect.items():
            assert seen.get(path) == turns, f"half {half}, {path}: turnaround {seen.get(path)} != {turns}"
        oe = ctl.bench.bits(MISO, "oe")
        for k in ctl.selects:
            on = next(c for c in range(k, len(oe)) if oe[c])
            assert on - k == 4, f"MISO driven {on - k} cycles after CS low (expected 4)"


@cocotb.test()
async def test_spi_target_speed_limit(dut):
    """SCK period 16 cycles is the limit the analysis gives (two register
    loads and the guess fit between the last two command bits): 16 works in
    every mode, 14 must fail."""
    for mode in range(4):
        for half, ok in ((8, True), (7, False)):
            good = True
            for seed in range(2):
                regs, transfers = random_spi_case(random.Random(seed * 10 + mode))
                ctl, after = await spi_run(dut, mode, transfers, regs, half=half)
                want, want_regs = spi_ref(regs, 0xA5, transfers)
                good &= ctl.bytes_read() == want and after == want_regs
            assert good == ok, f"mode {mode}, SCK period {2 * half}: works={good}, expected {ok}"


@cocotb.test()
async def test_spi_target_cs_rules(dut):
    """CS high ends a transfer anywhere, also in the middle of a byte; the
    next transfer starts clean after the minimum CS high time (32 cycles);
    the controller may pause SCK for any time inside a transfer."""
    rng = random.Random(5)
    regs = [rng.randrange(256) for _ in range(64)]
    transfers = [[0x00 | 0x20, 0x11, 0x22, 0x33],      # aborted in the 2nd data byte
                 [0x80 | 0x20, 0, 0, 0],
                 [0x80 | 0x31, 0, 0]]
    ctl, after = await spi_run(dut, 0, transfers, regs, half=8, gap=32,
                               abort=(0, 8 * 2 + 3))
    # the aborted write stored its first data byte only
    regs2 = list(regs)
    regs2[0x20] = 0x11
    want, _ = spi_ref(regs2, 0xA5, transfers[1:])
    assert ctl.bytes_read()[1:] == want, f"{ctl.bytes_read()[1:]} != {want}"
    assert after[0x20] == 0x11 and after[0x21] == regs[0x21], "aborted byte must not be stored"
    # long SCK pauses between bytes (well over the CS poll interval)
    regs, transfers = random_spi_case(random.Random(9))
    ctl, after = await spi_run(dut, 3, transfers, regs, half=8, ibg=100)
    want, want_regs = spi_ref(regs, 0xA5, transfers)
    assert ctl.bytes_read() == want and after == want_regs


# ============================================================================
# SPI flash: firmware/spi_flash.s
# ============================================================================
FLASH, FSIZE = 512, 512
FLASH_ID = (0xEF, 0x40, 0x09)


def flash_ref(mem, transfers, status=0):
    """The 25xx flash of spi_flash.s, from its header comment: the MISO
    bytes of each transfer (None where they are undefined), the store
    after, and the status. Erases finish before the next transfer."""
    mem, out = list(mem), []
    for tr in transfers:
        cmd, n = tr[0], len(tr)
        miso = [0x00] + [None] * (n - 1)          # zeros during the command byte
        a = ((tr[2] & 1) << 8 | tr[3]) if n >= 4 else None
        if cmd in (0x9F, 0x9E):
            miso[1:] = [FLASH_ID[i % 3] for i in range(n - 1)]
        elif cmd == 0x05:
            miso[1:] = [status] * (n - 1)
        elif cmd in (0x03, 0x0B) and a is not None:
            first = 4 if cmd == 0x03 else 5
            for i in range(first, n):
                miso[i] = mem[(a + i - first) % FSIZE]
        elif cmd == 0x06:
            status |= 0x02
        elif cmd == 0x04:
            status &= ~0x02
        elif cmd == 0x02 and a is not None and status & 0x02:
            for d in tr[4:]:
                mem[a] &= d
                a = (a & 0x100) | ((a + 1) & 0xFF)
            status &= ~0x02
        elif cmd in (0x20, 0x52, 0xD8, 0x60, 0xC7) and status & 0x02:
            mem = [0xFF] * FSIZE
            status &= ~0x02
        out.append(miso)
    return out, mem, status


async def flash_run(dut, mode, transfers, mem, **timing):
    timing.setdefault("gap", 64)          # CS high: room for the CS-high work
    await fw_boot(dut, "spi_flash.s", data={FLASH + i: b for i, b in enumerate(mem)},
                  defines={"MODE": mode})
    bench = Bench(dut)
    ctl = bench.add(SpiController(mode, transfers, **timing))
    ctl.bench = bench
    await bench.run(ctl.end + 20)
    return ctl, [sram(dut, FLASH + i) for i in range(FSIZE)]


def flash_check(ctl, want):
    got = ctl.bytes_read()
    bad = [(t, i, g[i], w[i]) for t, (g, w) in enumerate(zip(got, want))
           for i in range(len(w)) if w[i] is not None and g[i] != w[i]]
    assert not bad, f"MISO differs (transfer, byte, got, want): {bad[:6]}"
    assert min(s for *_, s in ctl.timing(ctl.bench)) >= 1, "MISO changed at a sampling edge"


def flash_reads(rng):
    return [
        [0x9F] + [0] * 6,                                  # ID, then again
        [0x9E] + [0] * 3,
        [0x05, 0, 0],
        [0x03, 0x12, 0x01, 0xFC] + [0] * 7,                # across the 512 wrap
        [0x03, 0xFF, 0x00, rng.randrange(256)] + [0] * 3,  # high bits ignored
        [0x0B, 0x00, 0x00, 0x7F, 0xA5] + [0] * 4,          # FAST_READ: a dummy byte
        [0x03, 0x00, 0x01],                                # cut short in the address
        [0x77, 0x11, 0x22],                                # not a command
        [0x03, 0x00, 0x01, 0x00, 0],
    ]


@cocotb.test()
async def test_flash_id_status_read(dut):
    """JEDEC ID (9F and 9E, repeating), status, READ across the 512-byte
    wrap and with address bits 23..9 set, FAST_READ, a transfer cut short
    and an unknown command, in every mode at SCK = clk/28 (the fastest) and
    clk/80 (every SCK wait times out and re-checks CS)."""
    rng = random.Random(3)
    for mode, half in [(m, 14) for m in range(4)] + [(0, 40), (3, 40)]:
        mem = [rng.randrange(256) for _ in range(FSIZE)]
        transfers = flash_reads(rng)
        ctl, after = await flash_run(dut, mode, transfers, mem, half=half, lead=16)
        want, want_mem, _ = flash_ref(mem, transfers)
        flash_check(ctl, want)
        assert after == want_mem, f"mode {mode}: a read changed the store"


@cocotb.test()
async def test_flash_program(dut):
    """Page program: ignored without WREN; WREN sets WEL (seen in the
    status), program ANDs into the store and wraps inside its 256-byte
    page, and CS high clears WEL; WRDI clears WEL; the result reads back."""
    rng = random.Random(4)
    mem = [rng.randrange(256) for _ in range(FSIZE)]
    data = [rng.randrange(256) for _ in range(6)]
    transfers = [
        [0x02, 0, 0x00, 0x10, 0x00, 0x00],                 # no WEL: ignored
        [0x05, 0],
        [0x06], [0x05, 0, 0],                              # WEL on
        [0x02, 0, 0x01, 0xFD] + data,                      # 1FD..1FF, then 100..102
        [0x05, 0],                                         # WEL off again
        [0x06], [0x04], [0x05, 0],                         # WRDI
        [0x02, 0, 0x00, 0x20, 0x00],                       # no WEL: ignored
        [0x06], [0x02, 0, 0x00, 0x40, 0xF0], [0x06],
        [0x02, 0, 0x00, 0x40, 0x3C],                       # 40 = old & F0 & 3C
        [0x03, 0, 0x01, 0xFD] + [0] * 3,
        [0x03, 0, 0x01, 0x00] + [0] * 3,
        [0x03, 0, 0x00, 0x3F] + [0] * 3,
    ]
    for mode in (0, 3):
        ctl, after = await flash_run(dut, mode, transfers, mem, half=16, lead=16)
        want, want_mem, _ = flash_ref(mem, transfers)
        flash_check(ctl, want)
        assert after == want_mem, f"mode {mode}: the store differs at " \
            f"{[i for i in range(FSIZE) if after[i] != want_mem[i]][:8]}"


@cocotb.test()
async def test_flash_erase(dut):
    """Each erase command (20, 52, D8 with an address; 60, C7) is ignored
    without WREN; with it, status polled at once reads WIP | WEL until the
    erase ends, then 00 (the erase pauses for each poll), and every byte
    reads back FF."""
    rng = random.Random(5)
    for cmd in (0x20, 0x52, 0xD8, 0x60, 0xC7):
        mem = [rng.randrange(256) for _ in range(FSIZE)]
        er = [cmd, 0, 0, 0] if cmd in (0x20, 0x52, 0xD8) else [cmd]
        polls = [[0x05, 0, 0]] * 16
        transfers = [er, [0x03, 0, 0, 0, 0], [0x06], er] + polls + \
            [[0x03, 0, 0x01, 0xF0] + [0] * 4, [0x03, 0, 0, 0] + [0] * 4]
        ctl, after = await flash_run(dut, 0, transfers, mem, half=16, lead=32, gap=800)
        got = ctl.bytes_read()
        assert got[1][4] == mem[0], f"{cmd:#x} without WREN erased"
        st = [p[1] for p in got[4:4 + len(polls)]]
        assert all(p[1] == p[2] for p in got[4:4 + len(polls)]), st
        busy = [s == 0x03 for s in st]
        assert busy[0] and not busy[-1] and busy == sorted(busy, reverse=True) \
            and set(st) == {0x03, 0x00}, f"{cmd:#x}: status polls {[hex(s) for s in st]}"
        assert got[-2][4:] == [0xFF] * 4 and got[-1][4:] == [0xFF] * 4
        assert after == [0xFF] * FSIZE, f"{cmd:#x}: not all erased"


@cocotb.test()
async def test_flash_speed_limit(dut):
    """SCK period 28 cycles works in every mode and 26 fails: the longest
    path, from a READ's last address bit to the wait for the first data
    bit, must fit in one SCK period."""
    rng = random.Random(6)
    for mode in range(4):
        for half, ok in ((14, True), (13, False)):
            mem = [rng.randrange(256) for _ in range(FSIZE)]
            transfers = flash_reads(rng)
            ctl, after = await flash_run(dut, mode, transfers, mem, half=half, lead=16)
            want, _, _ = flash_ref(mem, transfers)
            got = ctl.bytes_read()
            good = all(w[i] is None or g[i] == w[i]
                       for g, w in zip(got, want) for i in range(len(w)))
            assert good == ok, f"mode {mode}, SCK period {2 * half}: works={good}, expected {ok}"


# ============================================================================
# I2C
# ============================================================================
SDA, SCL = 5, 6


class I2cController:
    """Open-drain I2C controller driving `ops` against the chip, cycle by
    cycle (a generator advanced once per Bench step). Timing in core
    cycles: SCL low `tlow` (SDA changes `thd` after SCL falls), SCL high
    `thigh`, START/STOP setup and hold `tss`, bus free `tbuf`. Clock
    stretching is honoured: tHIGH starts when SCL is actually high.

    ops: ("start",) ("stop",) ("byte", value) ("read", ack)
         ("bits", value, n): the first n bits of a byte, then nothing
    results: per "byte" op the ACK bit seen (0 = ACK), per "read" the byte.
    drive: (role, scl_fall_cycle, scl_rise_cycle) for every bit the target
    drives, role = ack after the "address W" / "address R" / "pointer" /
    "data" byte, or "read bit 7" / "read bit" (first bit of a read byte or
    another one), for latency measurement. violations: SDA moving while SCL
    was high (a START or STOP the controller did not make)."""

    def __init__(self, ops, tlow=60, thigh=60, thd=6, tss=60, tbuf=80, start=40,
                 sda_pin=SDA, scl_pin=SCL):
        self.ops, self.tlow, self.thigh, self.thd = ops, tlow, thigh, thd
        self.sda_pin, self.scl_pin = sda_pin, scl_pin
        self.tss, self.tbuf, self.t0 = tss, tbuf, start
        self.sda_low = self.scl_low = False
        self.results, self.drive, self.violations = [], [], []
        self.stretched = self.fall = self.rise = 0
        self.done = False
        self.gen = self._run()

    # -- bus helpers --------------------------------------------------------
    def chip_low(self, pin):
        return bool((self.bench.oe[self.k] >> pin) & 1)

    def sda(self):
        return 0 if (self.sda_low or self.chip_low(self.sda_pin)) else 1

    def wait(self, n):
        for _ in range(n):
            yield

    def release_scl(self):
        """Release SCL and wait until it is high (the target may hold it
        low: clock stretching); self.rise = first cycle it is high."""
        self.scl_low = False
        while self.chip_low(self.scl_pin):
            self.stretched += 1
            yield
        self.rise = self.k
        yield

    def pull_scl(self):
        self.scl_low = True
        self.fall = self.k                 # SCL low from this cycle
        yield

    def clock(self, value=None):
        """One bit, SCL low on entry and on exit: SDA = value (None:
        released for the target); returns (SDA while SCL high, fall, rise)."""
        fall = self.fall
        yield from self.wait(self.thd - 1)
        self.sda_low = value == 0
        yield from self.wait(self.tlow - self.thd)
        yield from self.release_scl()
        rise = self.rise
        first = self.sda()
        for _ in range(self.thigh - 1):
            if self.sda() != first:
                self.violations.append(self.k)
            yield
        yield from self.pull_scl()
        return first, fall, rise

    def _run(self):
        yield from self.wait(self.t0)
        state = None
        for op in self.ops:
            if op[0] == "start":
                if self.scl_low:                 # repeated START: SDA up, SCL up
                    yield from self.wait(self.thd)
                    self.sda_low = False
                    yield from self.wait(self.tlow - self.thd)
                    yield from self.release_scl()
                yield from self.wait(self.tss)
                self.sda_low = True
                yield from self.wait(self.tss)
                state = "address"
                yield from self.pull_scl()
            elif op[0] == "stop":
                yield from self.wait(self.thd)
                self.sda_low = True
                yield from self.wait(self.tlow - self.thd)
                yield from self.release_scl()
                yield from self.wait(self.tss)
                self.sda_low = False
                yield from self.wait(self.tbuf)
            elif op[0] == "bits":
                for i in range(op[2]):
                    yield from self.clock((op[1] >> (7 - i)) & 1)
            elif op[0] == "byte":
                for i in range(8):
                    yield from self.clock((op[1] >> (7 - i)) & 1)
                ack, fall, rise = yield from self.clock(None)
                role = {"address": "address R" if op[1] & 1 else "address W"}.get(state, state)
                self.drive.append((role, fall, rise))
                self.results.append(ack)
                state = {"address W": "pointer", "pointer": "data"}.get(role, state)
            elif op[0] == "read":
                v = 0
                for i in range(8):
                    b, fall, rise = yield from self.clock(None)
                    self.drive.append(("read bit 7" if i == 0 else "read bit", fall, rise))
                    v = v << 1 | b
                yield from self.clock(0 if op[1] else 1)
                self.results.append(v)
        self.end = self.k

    def step(self, k, bench):
        self.k, self.bench = k, bench
        if not self.done:
            try:
                next(self.gen)
            except StopIteration:
                self.done = True
        mask = ((1 << self.sda_pin if self.sda_low else 0) |
                (1 << self.scl_pin if self.scl_low else 0))
        return mask, 0

    def turnarounds(self):
        """{role: set of cycles from SCL falling to the target's last SDA
        change before SCL rises} for target-driven bits (changes the
        controller made itself, releasing SDA after its ACK, are not the
        target's), and the smallest setup (cycles from that change to the
        SCL rise) seen."""
        lv = [(p >> self.sda_pin) & 1 for p in self.bench.pad]
        chip = [(o >> self.sda_pin) & 1 for o in self.bench.oe]
        out, setup = {}, None
        for role, fall, rise in self.drive:
            last = max((c for c in range(fall + 1, rise + 1)
                        if lv[c] != lv[c - 1] and chip[c] != chip[c - 1]), default=None)
            if last is not None:
                out.setdefault(role, set()).add(last - fall)
                setup = rise - last if setup is None else min(setup, rise - last)
        return out, setup


def i2c_write(addr, ptr, data):
    return [("start",), ("byte", addr << 1), ("byte", ptr)] + \
        [("byte", b) for b in data] + [("stop",)]


def i2c_read(addr, n, ptr=None):
    ops = [("start",), ("byte", addr << 1), ("byte", ptr)] if ptr is not None else []
    return ops + [("start",), ("byte", addr << 1 | 1)] + \
        [("read", i < n - 1) for i in range(n)] + [("stop",)]


def i2c_ref(mem, addr, ops):
    """The 24C01-style device of i2c_target.s, from its header comment:
    expected ACK bits / read bytes per op, and the memory afterwards."""
    mem, out, ptr = list(mem), [], 0
    state = "idle"
    for op in ops:
        if op[0] == "start":
            state = "addr"
        elif op[0] in ("stop", "bits"):          # a partial byte is dropped
            state = "idle" if op[0] == "stop" else state
        elif op[0] == "byte":
            if state == "addr":
                us = op[1] >> 1 == addr
                out.append(0 if us else 1)
                state = ("read" if op[1] & 1 else "ptr") if us else "idle"
            elif state == "ptr":
                ptr, state = op[1] & 0x7F, "data"
                out.append(0)
            elif state == "data":
                mem[ptr], ptr = op[1], (ptr + 1) & 0x7F
                out.append(0)
            else:
                out.append(1)
        elif op[0] == "read":
            if state == "read":
                out.append(mem[ptr])
                ptr = (ptr + 1) & 0x7F
            else:
                out.append(0xFF)
    return out, mem


async def i2c_run(dut, ops, mem, addr=0x50, stretch=0, **timing):
    await fw_boot(dut, "i2c_target.s", data={a: mem[a] for a in range(128)},
                  defines={"ADDR": addr, "STRETCH": stretch})
    bench = Bench(dut, pull=1 << SDA | 1 << SCL)
    ctl = bench.add(I2cController(ops, **timing))
    ctl.bench = bench
    await bench.until(lambda b: ctl.done, 400000, "I2C controller")
    await bench.run(20)
    return ctl, [sram(dut, a) for a in range(128)]


def random_i2c_ops(rng, addr):
    ops = []
    for _ in range(6):
        ptr = rng.randrange(128)
        kind = rng.choice(("write", "read", "random", "other"))
        if kind == "write":
            ops += i2c_write(addr, ptr, [rng.randrange(256) for _ in range(rng.randrange(1, 4))])
        elif kind == "read":
            ops += i2c_read(addr, rng.randrange(1, 4))
        elif kind == "random":
            ops += i2c_read(addr, rng.randrange(1, 4), ptr)
        else:
            ops += i2c_write(addr ^ 0x01, ptr, [1, 2])
    return ops


@cocotb.test()
async def test_i2c_target_eeprom(dut):
    """Writes, current-address reads, random reads (repeated START), wrap
    around and a transfer for another address, at 100 kHz (120 cycles per
    bit at 12 MHz) without clock stretching."""
    rng = random.Random(21)
    mem = [rng.randrange(256) for _ in range(128)]
    ops = i2c_write(0x50, 0x7E, [0x11, 0x22, 0x33]) + i2c_read(0x50, 4, 0x7E) + \
        random_i2c_ops(rng, 0x50)
    ctl, after = await i2c_run(dut, ops, mem)
    want, want_mem = i2c_ref(mem, 0x50, ops)
    assert ctl.results == want, f"{ctl.results} != {want}"
    assert after == want_mem, "memory after the writes differs"
    assert not ctl.violations


FAST = dict(tlow=16, thigh=14, thd=2, tss=8, tbuf=16)   # 400 kHz at 12 MHz


def i2c_session(rng, n=8):
    ops = []
    for _ in range(n):
        ops += i2c_write(0x50, rng.randrange(128), [rng.randrange(256) for _ in range(3)])
        ops += i2c_read(0x50, 3, rng.randrange(128))
        ops += i2c_read(0x50, 2)
    return ops


@cocotb.test()
async def test_i2c_target_latency(dut):
    """Cycles from SCL falling to the target's SDA change, derived from the
    ISA costs (docs/instruments.md): a read bit 10 (0) / 11 (1); the first bit
    of a read byte 9 after the address ACK, 12 after the controller's ACK;
    the ACK after the pointer or a data byte 11..15, after the address byte
    17..21 (write) / 19..23 (read) -- a byte ends in a 5-cycle loop that
    watches both lines for START and STOP, so its exit is 0..4 cycles late."""
    rng = random.Random(4)
    mem = [rng.randrange(256) for _ in range(128)]
    ops = i2c_session(rng)
    ctl, after = await i2c_run(dut, ops, mem)
    assert ctl.results == i2c_ref(mem, 0x50, ops)[0] and not ctl.violations
    turns, setup = ctl.turnarounds()
    assert turns["read bit"] == {10, 11}, turns
    assert turns["read bit 7"] <= {9, 12}, turns
    for role, lo in (("pointer", 11), ("data", 11), ("address W", 17), ("address R", 19)):
        assert turns[role] and min(turns[role]) >= lo and max(turns[role]) <= lo + 4, (role, turns)
    assert setup >= 3, f"SDA setup {setup} cycles < 250 ns"


@cocotb.test()
async def test_i2c_target_fast_mode(dut):
    """400 kHz at a 12 MHz core: SCL low is 16 cycles, shorter than the
    address ACK (17..23), so the target must stretch the clock. With
    STRETCH=1 it does and every transfer works; with STRETCH=0 the ACK lands
    while SCL is high. At 100 kHz STRETCH=1 never actually holds SCL."""
    rng = random.Random(8)
    mem = [rng.randrange(256) for _ in range(128)]
    ops = i2c_session(rng, 4)
    want = i2c_ref(mem, 0x50, ops)
    ctl, after = await i2c_run(dut, ops, mem, stretch=1, **FAST)
    assert (ctl.results, after) == want and not ctl.violations
    assert ctl.stretched > 0
    ctl, after = await i2c_run(dut, ops, mem, stretch=0, **FAST)
    assert ctl.violations, "without stretching the 400 kHz address ACK must be late"
    ctl, after = await i2c_run(dut, ops, mem, stretch=1)
    assert (ctl.results, after) == want and ctl.stretched == 0


@cocotb.test()
async def test_i2c_target_start_stop_anywhere(dut):
    """A repeated START or a STOP in the middle of a byte drops that byte
    and restarts / ends the transfer, as the I2C spec requires."""
    rng = random.Random(12)
    mem = [rng.randrange(256) for _ in range(128)]
    ops = [("start",), ("byte", 0x50 << 1), ("byte", 0x20), ("byte", 0x77),
           ("bits", 0xAB, 3), ("start",), ("byte", 0x50 << 1), ("byte", 0x30), ("byte", 0x99),
           ("bits", 0xCD, 5), ("stop",),
           ("start",), ("byte", 0x50 << 1), ("bits", 0x40, 6), ("stop",)]
    ops += i2c_read(0x50, 3, 0x20) + i2c_read(0x50, 2, 0x30)
    ctl, after = await i2c_run(dut, ops, mem)
    want, want_mem = i2c_ref(mem, 0x50, ops)
    assert ctl.results == want, f"{ctl.results} != {want}"
    assert after == want_mem and after[0x20] == 0x77 and after[0x21] == mem[0x21]
    assert after[0x30] == 0x99 and after[0x31] == mem[0x31]


class McuUartController:
    def __init__(self, data, start, bit_cycles=104):
        self.data, self.start, self.bit_cycles = data, start, bit_cycles
        self.bits = []
        for byte in data:
            self.bits.extend([0] + [(byte >> bit) & 1 for bit in range(8)] + [1])

    def step(self, k, bench):
        i = k - self.start
        bit = self.bits[i // self.bit_cycles] if 0 <= i < len(self.bits) * self.bit_cycles else 1
        return 1, bit


@cocotb.test()
async def test_mcu_protocol_target(dut):
    """Run the combined Vitis image against the MSPM0 UART, SPI and I2C
    test vectors, including repeated START and 1 MHz mode-0 SPI."""
    data = [0x01, 0x02, 0x03]
    await fw_boot(dut, "mcu_protocol_target.s")
    bench = Bench(dut, pull=(1 << 2) | (1 << 3))

    uart = McuUartController(data, start=100)
    bench.add(uart)
    await bench.run(100 + len(data) * 10 * 104 + 1200)
    assert uart_decode(bench.out, 104, pin=1)[:len(data)] == data

    i2c_ops = i2c_read(0x50, 1, 0x00)
    i2c = I2cController(i2c_ops, start=len(bench.out) + 100,
                        sda_pin=3, scl_pin=2)
    bench.add(i2c)
    await bench.until(lambda b: i2c.done, 100000, "MSPM0 I2C register read")
    assert i2c.results == [0, 0, 0, 0xA5], i2c.results
    assert not i2c.violations

    spi_data = [0x55, 0xAA, 0x12, 0x34, 0xDE, 0xAD, 0xBE, 0xEF]
    spi = SpiController(
        0, [[byte] for byte in spi_data], half=6, lead=20, gap=100,
        start=len(bench.out) + 100, sck_pin=4, mosi_pin=6, miso_pin=7,
        cs_pin=5,
    )
    bench.add(spi)
    await bench.run(spi.end - len(bench.out) + 20)
    assert spi.bytes_read() == [[byte] for byte in spi_data], spi.bytes_read()
    assert (bench.uo[-1] & 0x01) == 0, "SPI MOSI did not match the test vector"
