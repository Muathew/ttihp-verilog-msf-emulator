# SPDX-License-Identifier: Apache-2.0
"""Drawn protocol sda2506 (docs/draw/): the chip drives a Siemens SDA 2506-5.

The peer follows the SDA 2506-5 data sheet's text (Siemens, 06.94): with CE = 1
bits go into a 16-bit shift register, LSB first, taken at the clock's falling edge;
CE 1 -> 0 starts a read (CB = 0) or a reprogramming step (CB = 1, D = 1 erase, D = 0
write); a read loads the word with the first pulse and puts out D0 after its
trailing edge, then a bit after each further pulse, open drain; a reprogramming
step begins at the start pulse's trailing edge and ends at CE 0 -> 1, and must last
10-20 ms; an erase sets the word to 0xFF, a write clears the bits that are 0. It
checks the sheet's timing: clock high 2.5-60 us, low >= 5 us, CE edge to a clock
edge >= 5 us, CE edge to a change of D >= 2.5 us, D held 2.5 us before and after a
falling edge while bits go in, D steady from chip enable to the start pulse's
falling edge. The sheet's timing figures are pictures, so the edge a bit is taken
on is read from "data hold before/after falling edge". The same session wrote this
and the firmware, so it is not an independent check: sigrok's sda2506 decoder is.
"""
import cocotb

from fw_tb import Bench, fw_boot, save_trace
from msf_tb import uart_decode

CLK, D, CE = 0, 1, 2
US = 12
UBIT = 104


class Sda2506:
    def __init__(self, preload):
        self.mem = [0xFF] * 128
        for a, v in preload.items():
            self.mem[a] = v
        self.sr = []                       # bits as they went in, the last at the end
        self.errors, self.log = [], []
        self.mode = None                   # None, "read", "erase", "write"
        self.pulses = 0
        self.out, self.out_bits = None, []  # D driven low (0) or released (None)
        self.prev = None
        self.t = {"rise": None, "fall": None, "ce": None, "d": None}
        self.d_at_ce = None
        self.prog_start = None

    def err(self, k, msg):
        self.errors.append(f"cycle {k}: {msg}")

    def step(self, k, b):
        oe, outp = b.oe[k], b.out[k]
        clk = (outp >> CLK) & 1 if oe >> CLK & 1 else 0
        ce = (outp >> CE) & 1 if oe >> CE & 1 else 1
        chip_d = 0 if oe >> D & 1 and not (outp >> D) & 1 else 1   # open drain
        d = chip_d & (0 if self.out == 0 else 1)
        if self.prev is None:
            self.prev = (clk, ce, chip_d)
            return 1 << D, 1 << D
        pclk, pce, pd = self.prev
        self.prev = (clk, ce, chip_d)
        t = self.t
        if chip_d != pd:                       # the chip moved D
            if t["ce"] is not None and k - t["ce"] < int(2.5 * US):
                self.err(k, "D changed within 2.5 us of a CE edge")
            if ce == 1 and t["fall"] is not None and k - t["fall"] < int(2.5 * US):
                self.err(k, "D changed within 2.5 us after a falling edge")
            if ce == 0 and self.mode in ("erase", "write") and self.pulses == 0:
                self.err(k, "D changed between chip enable and the start pulse")
            t["d"] = k
        if ce != pce:
            for e in ("rise", "fall"):
                if t[e] is not None and k - t[e] < 5 * US:
                    self.err(k, f"CE moved {(k - t[e]) / US:.1f} us after a clock edge")
            t["ce"] = k
            if ce == 0:
                self.chip_enable(k, chip_d)
            else:
                self.chip_disable(k)
        if clk != pclk:
            if t["ce"] is not None and k - t["ce"] < 5 * US:
                self.err(k, f"clock edge {(k - t['ce']) / US:.1f} us after CE moved")
            if clk == 1:
                if t["fall"] is not None and k - t["fall"] < 5 * US:
                    self.err(k, f"clock low {(k - t['fall']) / US:.1f} us, needs 5")
                t["rise"] = k
                self.rise(k)
            else:
                hi = k - t["rise"]
                if not int(2.5 * US) <= hi <= 60 * US:
                    self.err(k, f"clock high {hi / US:.1f} us, needs 2.5-60")
                t["fall"] = k
                if ce == 1:
                    if t["d"] is not None and k - t["d"] < int(2.5 * US):
                        self.err(k, "D changed within 2.5 us before a falling edge")
                    self.sr = (self.sr + [chip_d])[-16:]
                else:
                    self.fall(k)
        if self.out_bits and self.out_bits[0][0] <= k:
            _, self.out = self.out_bits.pop(0)
        return 1 << D, (0 if self.out == 0 else 1) << D

    def word(self, n):
        bits = self.sr[-n:]
        return sum(v << i for i, v in enumerate(bits))   # first in is the LSB

    def chip_enable(self, k, dlevel):
        ctl = self.word(8) if len(self.sr) >= 8 else None
        self.pulses = 0
        if ctl is None:
            self.err(k, "chip enable with no control word")
            return
        self.addr, cb = ctl & 0x7F, ctl >> 7
        if cb == 0:
            self.mode = "read"
        else:
            self.mode = "erase" if dlevel else "write"
            self.data = self.word(16) & 0xFF if len(self.sr) >= 16 else None
            self.d_at_ce = dlevel

    def rise(self, k):
        if self.mode in ("erase", "write") and self.pulses == 1:
            self.err(k, "a second clock pulse while programming")

    def fall(self, k):
        self.pulses += 1
        if self.mode == "read":
            if self.pulses == 1:
                self.shift = self.mem[self.addr]
            if self.pulses <= 8:
                bit = (self.shift >> (self.pulses - 1)) & 1
                self.out_bits.append((k + 2 * US, 0 if bit == 0 else None))
            else:
                self.err(k, "more than eight pulses in a read")
        elif self.mode in ("erase", "write") and self.pulses == 1:
            self.prog_start = k

    def chip_disable(self, k):
        self.out_bits, self.out = [], None
        if self.mode == "read":
            self.log.append(("read", self.addr, self.shift if self.pulses else None, self.pulses))
        elif self.mode in ("erase", "write"):
            if self.prog_start is None:
                self.err(k, f"{self.mode} with no start pulse")
            else:
                ms = (k - self.prog_start) / (1000 * US)
                if not 10 <= ms <= 20:
                    self.err(k, f"{self.mode} lasted {ms:.2f} ms, needs 10-20")
                elif self.mode == "erase":
                    self.mem[self.addr] = 0xFF
                elif self.data is None:
                    self.err(k, "write with no data entered")
                else:
                    self.mem[self.addr] &= self.data
                self.log.append((self.mode, self.addr, self.data, round(ms, 2)))
        self.mode, self.prog_start = None, None


@cocotb.test()
async def test_draw_sda2506(dut):
    """Read 0x12, erase and write 0xA7 to 0x34, read 0x34."""
    await fw_boot(dut, "draw_sda2506.s")
    dev = Sda2506({0x12: 0x5A})
    bench = Bench(dut, pull=1 << D, models=[dev])
    await bench.until(lambda b: b.uo[-1] & 0x02, 600_000, "the report")
    await bench.run(200)
    report = uart_decode([u & 1 for u in bench.uo], UBIT)
    for e in dev.errors[:10]:
        dut._log.error(e)
    dut._log.info(f"device saw {dev.log}; report {[hex(x) for x in report]}")
    assert not dev.errors, f"{len(dev.errors)} protocol errors"
    assert [e[:2] for e in dev.log] == [("read", 0x12), ("erase", 0x34), ("write", 0x34),
                                         ("read", 0x34)], dev.log
    assert dev.log[0][2:] == (0x5A, 8) and dev.log[3][2:] == (0xA7, 8)
    assert dev.log[2][2] == 0xA7 and dev.mem[0x34] == 0xA7
    assert report == [0x5A, 0xA7], report
    up = next(k for k in range(len(bench.uo)) if bench.uo[k] & 1)
    end = next(k for k in range(up, len(bench.uo)) if not bench.uo[k] & 1)
    # sigrok's decoder disagrees on the bytes read. The sheet's Read figure (UED01268)
    # has D0 come out tDD = 2.5 us after the first pulse's trailing edge, D1 after the
    # second's, as the firmware and the peer have it; the decoder shows the line as it
    # was before that, then D0-D6, so it reads a byte b as (b << 1 | 1) & 0xFF. That is
    # written here as such, so that the check still fails if anything else changes; the
    # disagreement is in docs/draw/README.md and docs/instruments.md. It agrees on every
    # address, control bit and the data written.
    early = lambda b: ((b << 1) | 1) & 0xFF
    expect = ["addr: 12", "CB: 00", "read: 12", f"{early(0x5A):02X}",
              "addr: 34", "CB: 01", "addr: 34", "CB: 01", "data: A7",
              "addr: 34", "CB: 00", "read: 34", f"{early(0xA7):02X}"]
    save_trace("draw_sda2506", bench.pad[:end], "sda2506:clk=0:d=1:ce=2",
               "sda2506=data", expect, {"pin": D, "hold": 10 * US})
