# SPDX-License-Identifier: Apache-2.0
"""Drawn protocol iec (docs/draw/): the chip is the computer on a Commodore serial
bus, with a disk drive at device 8.

The drive is written from J. Butterfield, "How the VIC/64 Serial Bus Works"
(COMPUTE! 38, 1983) and M. Steil's "Commodore Peripheral Bus" articles
(pagetable.com): it answers ATN within 1 ms, listens and talks with the standard
handshakes, signals EOI by keeping quiet 200 us or more and acknowledges one by
holding DATA 60 us, and turns the bus round after TALK. It checks what the
computer does against the same sources: bits held >= 20 us with CLK pulled and
>= 20 us released, DATA steady while CLK is released, >= 100 us between bytes,
CLK pulled within 200 us of ready-for-data unless it means EOI, an EOI
acknowledgement held >= 60 us, frames acknowledged within 1 ms, CLK pulled under
ATN. A 1 bit is DATA released, as the C64 KERNAL's bit loop sends it. The same
session wrote this and the firmware, so it is not an independent check: sigrok's
iec decoder is.
"""
import cocotb

from fw_tb import Bench, fw_boot, save_trace
from msf_tb import uart_decode

DATA, CLK, ATN = 0, 1, 2          # uio pins; bit masks below
MD, MC, MA = 1 << DATA, 1 << CLK, 1 << ATN
US = 12                            # cycles a microsecond at 12 MHz
REPLY = b"00, OK,00,00\r"
UBIT = 104


class Atn(Exception):
    """ATN pulled while the drive was doing something else."""


class Drive:
    def __init__(self, dev=8):
        self.dev = dev
        self.pull = 0                  # lines the drive pulls low
        self.errors, self.cmds, self.rx, self.tx = [], [], [], []
        self.role = None               # None, "listen", "talk"
        self.in_atn = False
        self.k = 0
        self.gen = self.main()

    # ---- the bench calls this every cycle
    def step(self, k, b):
        self.k = k
        self.chip = b.oe[k] & ~b.out[k] & 7    # lines the chip pulls
        next(self.gen)
        return self.pull, 0

    def hi(self, m):
        """Is the line released by everyone (high)?"""
        return not ((self.chip | self.pull) & m)

    def err(self, msg):
        self.errors.append(f"cycle {self.k}: {msg}")

    def wait(self, cond, limit=None):
        t0 = self.k
        while not cond():
            if not self.in_atn and not self.hi(MA):
                raise Atn
            if limit is not None and self.k - t0 >= limit:
                return False
            yield
        return True

    def delay(self, n):
        t0 = self.k
        yield from self.wait(lambda: self.k - t0 >= n)

    # ---- behaviour
    def main(self):
        yield
        while True:
            try:
                if self.role == "listen":
                    byte, eoi = yield from self.rx_byte()
                    self.rx.append((byte, eoi))
                elif self.role == "talk":
                    yield from self.talk()
                else:
                    yield from self.wait(lambda: False)
            except Atn:
                yield from self.atn()

    def atn(self):
        self.in_atn = True
        yield from self.delay(80 * US)       # answer within 1 ms: DATA pulled
        self.pull = MD
        if self.hi(MC):
            self.err("ATN without CLK pulled by the computer")
        pending = None
        while True:
            r = yield from self.rx_byte()
            if r is None:
                break
            byte, eoi = r
            self.cmds.append(byte)
            if eoi:
                self.err(f"EOI under ATN on {byte:#04x}")
            if byte == 0x20 | self.dev:
                pending = "listen"
            elif byte == 0x40 | self.dev:
                pending = "talk"
            elif byte in (0x3F, 0x5F):
                pending, self.role = "none", None
            elif 0x60 <= byte <= 0x6F:
                if pending is None and self.role is None:
                    self.err(f"secondary {byte:#04x} with no LISTEN or TALK")
            else:
                self.err(f"unexpected command {byte:#04x}")
        self.in_atn = False
        if pending == "listen":
            self.role = "listen"             # keep DATA pulled: ready to listen
        elif pending == "talk":
            # the turnaround: the computer pulls DATA and releases CLK; we let go
            # of DATA and take CLK
            yield from self.wait(lambda: self.hi(MC), 5000 * US)
            if not self.hi(MC) or (self.chip & MD) == 0:
                self.err("no turnaround: CLK not released with DATA pulled")
            yield from self.delay(40 * US)
            self.pull = MC
            self.role = "talk"
        else:
            self.role = None
            self.pull = 0

    def rx_byte(self):
        """As listener (DATA pulled by us, CLK by the talker). None if ATN goes."""
        ok = yield from self.wait(lambda: self.hi(MC) or self.in_atn and self.hi(MA))
        if self.in_atn and self.hi(MA):
            return None
        if getattr(self, "frame_at", None) is not None and self.k - self.frame_at < 100 * US:
            self.err(f"next byte {(self.k - self.frame_at) / US:.0f} us after the last, "
                     "needs 100 us")
        yield from self.delay(40 * US)       # the listener takes its time
        self.pull &= ~MD                     # ready for data
        eoi = False
        ok = yield from self.wait(lambda: not self.hi(MC), 200 * US)
        if not ok:                           # quiet for 200 us: EOI
            eoi = True
            self.pull |= MD
            yield from self.delay(60 * US)
            self.pull &= ~MD
            yield from self.wait(lambda: not self.hi(MC))
        byte = 0
        for i in range(8):
            t_low = self.k
            yield from self.wait(lambda: self.hi(MC))
            if self.k - t_low < 20 * US:
                self.err(f"bit {i}: CLK pulled {(self.k - t_low) / US:.0f} us, needs 20")
            bit = 1 if self.hi(MD) else 0
            t_hi = self.k
            while self.hi(MC):
                if (1 if self.hi(MD) else 0) != bit:
                    self.err(f"bit {i}: DATA changed while CLK was released")
                    bit = None
                yield
            if self.k - t_hi < 20 * US:
                self.err(f"bit {i}: CLK released {(self.k - t_hi) / US:.0f} us, needs 20")
            byte |= (bit or 0) << i
        yield from self.delay(30 * US)
        self.pull |= MD                      # frame handshake
        self.frame_at = self.k
        return byte, eoi

    def talk(self):
        self.frame_at = None
        for i, byte in enumerate(REPLY):
            yield from self.tx_byte(byte, i == len(REPLY) - 1)
            self.tx.append(byte)
        yield from self.delay(60 * US)
        self.pull = 0
        self.role = None

    def tx_byte(self, byte, last):
        """As talker (CLK pulled by us, DATA by the listener)."""
        yield from self.delay(100 * US)
        self.pull &= ~MC                     # ready to send
        yield from self.wait(lambda: self.hi(MD))
        if last:                             # EOI: say nothing for 200 us and more
            t0 = self.k
            ok = yield from self.wait(lambda: not self.hi(MD), 1000 * US)
            if not ok:
                self.err("the computer never acknowledged the EOI")
            elif self.k - t0 < 200 * US:
                self.err(f"EOI acknowledged after {(self.k - t0) / US:.0f} us: too soon")
            t1 = self.k
            yield from self.wait(lambda: self.hi(MD))
            if self.k - t1 < 60 * US:
                self.err(f"EOI acknowledgement held {(self.k - t1) / US:.0f} us, needs 60")
        else:
            yield from self.delay(40 * US)   # within 200 us: not an EOI
        self.pull |= MC
        for i in range(8):
            if (byte >> i) & 1:
                self.pull &= ~MD
            else:
                self.pull |= MD
            yield from self.delay(60 * US)
            self.pull &= ~MC                 # valid
            yield from self.delay(60 * US)
            self.pull = (self.pull | MC) & ~MD
        ok = yield from self.wait(lambda: not self.hi(MD), 1000 * US)
        if not ok:
            self.err(f"no frame handshake for {byte:#04x}")


@cocotb.test()
async def test_draw_iec(dut):
    """LISTEN 8 / 15, "I0" with EOI, UNLISTEN, TALK 8 / 15, 13 bytes to EOI, UNTALK."""
    await fw_boot(dut, "draw_iec.s")
    drive = Drive()
    bench = Bench(dut, pull=0x07, models=[drive])
    await bench.until(lambda b: b.uo[-1] & 0x02, 1_500_000, "the report")
    await bench.run(200)
    report = uart_decode([u & 1 for u in bench.uo], UBIT)
    for e in drive.errors[:10]:
        dut._log.error(e)
    dut._log.info(f"commands {[hex(c) for c in drive.cmds]}, data {drive.rx}, "
                  f"sent {bytes(drive.tx)}, report {bytes(report)}")
    assert not drive.errors, f"{len(drive.errors)} protocol errors"
    assert drive.cmds == [0x28, 0x6F, 0x3F, 0x48, 0x6F, 0x5F], drive.cmds
    assert drive.rx == [(0x49, False), (0x30, True)], drive.rx
    assert bytes(drive.tx) == REPLY
    assert bytes(report) == REPLY, report
    assert bench.pad[-1] & 0x07 == 0x07, "the bus is not released at the end"
    up = next(k for k in range(len(bench.uo)) if bench.uo[k] & 1)
    end = next(k for k in range(up, len(bench.uo)) if not bench.uo[k] & 1)   # the report
    # sigrok's command/data row with its EOI row: each byte then "EOI" or nothing.
    # It names LISTEN 8 "L8" and shows the secondary address 0x6F as "R?".
    expect = ["L8", "", "R?", "", "I", "", "0", "EOI", "UNL", "", "T8", "", "R?", ""]
    for i, c in enumerate(REPLY):
        expect += [("CR" if c == 0x0D else chr(c)).strip(), "EOI" if i == len(REPLY) - 1 else ""]
    expect += ["UNT", ""]
    save_trace("draw_iec", bench.pad[:end], "iec:data=0:clk=1:atn=2", "iec=gpib:eoi", expect,
               {"pin": DATA, "hold": 60 * US})
