# SPDX-License-Identifier: Apache-2.0
"""ST7735 driver (firmware/st7735.s) against a controller written from Sitronix's ST7735S
data sheet: with CSX low, SDA taken on SCL's rising edge, MSB first, D/CX with the
byte's last bit (0 a command, 1 data); after SWRESET and after SLPOUT the next command
waits 120 ms (here W120 = 1, the waits a 24th as long, and the model asks a 24th);
CASET and RASET set the window, RAMWR fills it row by row with 16-bit pixels; COLMOD
0x05 is 16 bits a pixel."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, samples

SCL, SDA, DC, CSX = 0x04, 0x08, 0x10, 0x20
W120 = 60000                       # the test's 120 ms
RED = 0xF800


class St7735:
    def __init__(self):
        self.cmd, self.args, self.fb, self.on, self.colmod = None, [], {}, False, None
        self.ready_at, self.log = 0, []

    def byte(self, t, dc, b):
        if not dc:
            assert t >= self.ready_at, f"command {b:#04x} {(self.ready_at - t)} cycles early"
            self.cmd, self.args = b, []
            self.log.append(b)
            if b in (0x01, 0x11):
                self.ready_at = t + W120
            elif b == 0x29:
                self.on = True
            elif b == 0x2C:
                self.x, self.y, self.half = self.xs, self.ys, None
            return
        self.args.append(b)
        if self.cmd == 0x3A:
            self.colmod = b
        elif self.cmd == 0x2A and len(self.args) == 4:
            self.xs, self.xe = self.args[0] << 8 | self.args[1], self.args[2] << 8 | self.args[3]
        elif self.cmd == 0x2B and len(self.args) == 4:
            self.ys, self.ye = self.args[0] << 8 | self.args[1], self.args[2] << 8 | self.args[3]
        elif self.cmd == 0x2C:
            if self.half is None:
                self.half = b
                return
            self.fb[(self.x, self.y)] = self.half << 8 | b
            self.half = None
            self.x += 1
            if self.x > self.xe:
                self.x, self.y = self.xs, self.y + 1


@cocotb.test()
async def test_st7735_init_and_block(dut):
    """Reset, wake, 16-bit colour, display on; a red 4 x 2 block at (10, 20)."""
    await fw_boot(dut, "st7735.s", defines={"W120": 1})
    ch = await watch_pins(dut, 2 * W120 + 6000, pull=0)
    lcd, sr, n, prev = St7735(), 0, 0, ch[0][1]
    for t, p in ch[1:]:
        if p & CSX and not prev & CSX:
            assert n == 0, f"CSX rose after {n} bits"
        if not p & CSX and p & SCL and not prev & SCL:
            sr, n = (sr << 1 | (p & SDA) >> 3) & 0xFF, n + 1
            if n == 8:
                lcd.byte(t, p & DC, sr)
                n = 0
        prev = p
    assert lcd.log == [0x01, 0x11, 0x3A, 0x36, 0x29, 0x2A, 0x2B, 0x2C], [hex(c) for c in lcd.log]
    assert lcd.colmod == 0x05 and lcd.on
    want = {(x, y): RED for x in range(10, 14) for y in range(20, 22)}
    assert lcd.fb == want, lcd.fb
    t1 = ch[-1][0] + 50
    save_trace("st7735", samples(ch, 0, t1, 1), "st7735:cs=5:clk=2:mosi=3:dc=4", "st7735=description",
               # (the decoder has no description for RAMWR)
               ["SWRESET: Software reset", "SLPOUT: Sleep out & booster on",
                "COLMOD: Interface pixel format", "MADCTL: Memory data address control",
                "DISPON: Display on", "CASET: Column address set", "RASET: Row address set"],
               {"pin": 2, "hold": 3})
