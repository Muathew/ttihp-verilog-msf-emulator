# SPDX-License-Identifier: Apache-2.0
"""MAX7219 driver (firmware/max7219.s) against the device as its data sheet (Maxim
19-4452) describes it: DIN taken on CLK's rising edge into a 16-bit shift register,
the last 16 bits latched on LOAD's rising edge, D11-D8 the register and D7-D0 the
data; with decode mode on, Code B digits and D7 the decimal point. The display it
ends up showing, digit 7 at the left, must read 08.11.2026."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, samples

CODE_B = "0123456789-EHLP "


@cocotb.test()
async def test_max7219_date(dut):
    """Set-up, then 08.11.2026 on the eight digits, out of shutdown last."""
    await fw_boot(dut, "max7219.s")
    ch = await watch_pins(dut, 2000)
    sr, reg, writes, clocks = 0, {}, [], 0
    prev = ch[0][1]
    for t, p in ch[1:]:
        if (p & 0x04) and not (prev & 0x04):            # CLK rising: shift DIN in
            assert not (p & 0x20), "a clock while LOAD is high"
            sr = (sr << 1 | (p >> 3) & 1) & 0xFFFF
            clocks += 1
        if (p & 0x20) and not (prev & 0x20):            # LOAD rising: latch
            assert clocks == 16, f"{clocks} clocks in a write"
            reg[(sr >> 8) & 0x0F] = sr & 0xFF
            writes.append(sr)
            clocks = 0
        prev = p
    assert reg[0x0F] == 0 and reg[0x0B] == 7 and reg[0x09] == 0xFF and reg[0x0A] == 8
    assert reg[0x0C] == 1 and writes[-1] >> 8 & 0x0F == 0x0C, "shutdown left last"
    shown = ""
    for d in range(8, 0, -1):
        v = reg[d]
        shown += CODE_B[v & 0x0F] + ("." if v & 0x80 else "")
    assert shown == "08.11.2026", shown
    t1 = ch[-1][0] + 50
    names = {0x0F: "Display test: off", 0x0B: "Scan limit: 8", 0x09: "Decode: 0b11111111",
             0x0A: "Intensity: 8", 0x0C: "Shutdown: off"}
    lines = [names[w >> 8] if w >> 8 in names else f"Digit {w >> 8}: {w & 0xFF:02X}"
             for w in writes]
    save_trace("max7219", samples(ch, 0, t1, 1), "spi:clk=2:mosi=3:cs=5,max7219",
               "max7219=commands", lines, {"pin": 2, "hold": 3})
