# SPDX-License-Identifier: Apache-2.0
"""HD44780 driver (firmware/hd44780.s) against a model of the controller written from
Hitachi's HD44780U data sheet: RS and D7-D4 latched on E's falling edge, E high at
least 450 ns and an enable cycle of 1000 ns or more; initialising by instruction
(Figure 24): 40 ms after power-on, 0011, 4.1 ms, 0011, 100 us, 0011, 0010, then 4-bit
transfers, high nibble first; every instruction given its execution time (37 us, a
clear 1.52 ms) before the next; the display shows DDRAM 0x00.. and 0x40.."""
import cocotb

from fw_tb import fw_boot
from pins import watch_pins

US = 12
RS, E = 0x01, 0x02


class Lcd:
    def __init__(self):
        self.mode8, self.half, self.ddram = True, None, {}
        self.ac, self.inc, self.on, self.lines2, self.busy_until = 0, 1, False, False, 0
        self.inits = []

    def latch(self, t, rs, nib):
        if self.mode8:                   # before 0010: one nibble is a whole instruction
            self.inits.append((t, nib))
            if nib == 0x2 and len(self.inits) == 4:
                self.mode8 = False
            self.check(t, 37)
            return
        if self.half is None:
            self.half = (t, rs, nib)
            return
        t0, rs0, hi = self.half
        assert rs0 == rs, "RS changed inside a byte"
        self.half = None
        self.check(t0, None)
        self.do(t, rs, hi << 4 | nib)

    def check(self, t, exec_us):
        assert t >= self.busy_until, f"an instruction {(self.busy_until - t) / US:.1f} us too early"
        if exec_us is not None:
            self.busy_until = t + exec_us * US

    def do(self, t, rs, b):
        if rs:
            self.ddram[self.ac] = chr(b)
            self.ac += self.inc
            self.busy_until = t + 37 * US
            return
        if b == 0x01:
            self.ddram, self.ac, self.inc = {}, 0, 1
            self.busy_until = t + 1520 * US
            return
        if b & 0x80:
            self.ac = b & 0x7F
        elif b & 0x20:
            assert not b & 0x10, "function set: 8 bits again"
            self.lines2 = bool(b & 0x08)
        elif b & 0x08:
            self.on = bool(b & 0x04)
        elif b & 0x04:
            self.inc = 1 if b & 0x02 else -1
        self.busy_until = t + 37 * US


@cocotb.test()
async def test_hd44780_hello(dut):
    """Initialised by instruction, then "HELLO, TT" and "msfcore v3"."""
    await fw_boot(dut, "hd44780.s")
    ch = await watch_pins(dut, 9 * 60000 + 2 * 60000 + 40 * 600 + 30000 + 4000, pull=0)
    lcd, prev, rise = Lcd(), ch[0][1], None
    for t, p in ch[1:]:
        if p & E and not prev & E:
            assert rise is None or t - rise >= 12, f"enable cycle {t - rise} cycles (1000 ns is 12)"
            rise = t
        if prev & E and not p & E:
            assert t - rise >= 6, f"E high {t - rise} cycles (450 ns is 5.4)"
            assert (p & 0xF1) == (prev & 0xF1), "data changed with E falling"
            lcd.latch(t, p & RS, p >> 4)
        prev = p
    ts = [t for t, _ in lcd.inits]
    assert [n for _, n in lcd.inits] == [3, 3, 3, 2], lcd.inits
    assert ts[0] >= 40000 * US, f"first instruction {ts[0] / US:.0f} us after power-on"
    assert ts[1] - ts[0] >= 4100 * US and ts[2] - ts[1] >= 100 * US, ts
    assert lcd.lines2 and lcd.on and lcd.inc == 1
    line = lambda a: "".join(lcd.ddram.get(a + i, " ") for i in range(16)).rstrip()
    assert (line(0x00), line(0x40)) == ("HELLO, TT", "msfcore v3"), (line(0), line(0x40))
