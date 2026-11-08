# SPDX-License-Identifier: Apache-2.0
"""WS2801 chain driver (firmware/ws2801.s) against eight chained WS2801s written from
the Worldsemi data sheet: SDI taken on CKI's rising edge, each chip keeping the first
24 bits (R, G, B, MSB first) and passing the rest on, and latching after CKI has been
low for 500 us."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, samples

CKI, SDI = 0x04, 0x08
RAINBOW = [0xFF0000, 0xFF8000, 0xFFFF00, 0x00FF00, 0x00FFFF, 0x0000FF, 0x8000FF, 0x202020]


@cocotb.test()
async def test_ws2801_rainbow(dut):
    """Eight pixels shifted down the chain, latched by 500 us of CKI low."""
    await fw_boot(dut, "ws2801.s")
    ch = await watch_pins(dut, 12000, pull=0)
    bits, prev, last_rise = [], ch[0][1], None
    for t, p in ch[1:]:
        if p & CKI and not prev & CKI:
            bits.append((p & SDI) >> 3)
            last_rise = t
        prev = p
    assert len(bits) == 24 * 8, len(bits)
    # chip k keeps bits 24k .. 24k + 23 of what passed the chips before it
    shown = [int("".join(map(str, bits[24 * k:24 * k + 24])), 2) for k in range(8)]
    assert shown == RAINBOW, [hex(x) for x in shown]
    assert int(dut.uo_out.value) == 0x55
    assert not ch[-1][1] & CKI and 12000 - last_rise >= 500 * 12, "CKI low 500 us: latched"
    t1 = last_rise + 200
    save_trace("ws2801", samples(ch, 0, t1, 1), "spi:clk=2:mosi=3,rgb_led_spi", "rgb_led_spi",
               [f"#{c:06x}" for c in RAINBOW], {"pin": 2, "hold": 3})
