# SPDX-License-Identifier: Apache-2.0
"""TLC5620 driver (firmware/tlc5620.s) against the DAC as TI's data sheet (SLAS081)
describes it: DATA taken on CLK's falling edge, 11 bits MSB first (A1 A0 RNG D7-D0);
LOAD low latches the last 11 bits into the selected input register, and the output
follows at once if LDAC is low; LDAC falling copies all four registers to the outputs."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, samples

CLK, DATA, LOAD, LDAC = 0x01, 0x02, 0x04, 0x08


@cocotb.test()
async def test_tlc5620_quad(dut):
    """Four registers, one LDAC pulse updates them together, then A directly."""
    await fw_boot(dut, "tlc5620.s")
    ch = await watch_pins(dut, 1500, pull=0)
    sr, n, regs, outs, history = 0, 0, [None] * 4, [None] * 4, []
    prev = ch[0][1]
    low_from = {LOAD: -100, LDAC: -100}     # (they rise once as the program starts)
    for t, p in ch[1:]:
        fell = prev & ~p
        rose = p & ~prev
        if fell & CLK:
            sr, n = (sr << 1 | (p & DATA) >> 1) & 0x7FF, n + 1
        for pin in (LOAD, LDAC):
            if fell & pin:
                low_from[pin] = t
            if rose & pin:
                assert t - low_from[pin] >= 3, f"pin {pin:#x} low only {t - low_from[pin]} cycles"
        if fell & LOAD:
            assert n == 11, f"{n} bits before LOAD"
            ch_sel, rng, val = sr >> 9, (sr >> 8) & 1, sr & 0xFF
            regs[ch_sel] = (val, 1 + rng)
            if not p & LDAC:
                outs[ch_sel] = regs[ch_sel]
                history.append(list(outs))
            n = 0
        if fell & LDAC:
            outs = list(regs)
            history.append(list(outs))
        prev = p
    # the LDAC pulse; LDAC held low (falling again: the same values); A at once
    assert history == ([[(64, 1), (128, 2), (192, 1), (255, 2)]] * 2
                       + [[(16, 1), (128, 2), (192, 1), (255, 2)]]), history
    t1 = ch[-1][0] + 50
    save_trace("tlc5620", samples(ch, 0, t1, 1), "tlc5620:clk=0:data=1:load=2:ldac=3",
               "tlc5620=registers:voltage-updates",
               # the decoder's 3.3 V reference: code / 256 x 3.3 V x (1 + RNG)
               ["Setting DACA register value to 0.82V", "Setting DACB register value to 3.30V",
                "Setting DACC register value to 2.47V", "Setting DACD register value to 6.57V",
                "Updating voltages: DACA=0.82V DACB=3.30V DACC=2.47V DACD=6.57V",
                "Setting DACA voltage to 0.21V"], {"pin": 0, "hold": 2})
