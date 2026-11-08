# SPDX-License-Identifier: Apache-2.0
"""ADF4351 programming (firmware/adf4351.s) against the synthesizer as ADI's data sheet
describes it: DATA taken on CLK's rising edge, 32 bits latched on LE's rising edge, the
3 LSBs the register; R5 down to R0 after power-up; and the output it would give:
f_PFD = REFin (1 + D) / (R (1 + T)), VCO = f_PFD (INT + FRAC / MOD), RF = VCO / 2^div."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, samples

CLK, DATA, LE = 0x04, 0x08, 0x20
REFIN = 25e6


@cocotb.test()
async def test_adf4351_2400mhz(dut):
    """Six registers, R5 first; together they make 2.400 GHz from 25 MHz."""
    await fw_boot(dut, "adf4351.s")
    ch = await watch_pins(dut, 2000, pull=0)
    sr, n, order, regs, prev = 0, 0, [], {}, ch[0][1]
    for t, p in ch[1:]:
        if p & CLK and not prev & CLK:
            assert not p & LE, "a clock with LE high"
            sr, n = (sr << 1 | (p & DATA) >> 3) & 0xFFFFFFFF, n + 1
        if p & LE and not prev & LE and (n or order):   # (not LE's idle level at power-up)
            assert n == 32, f"{n} bits"
            regs[sr & 7] = sr
            order.append(sr & 7)
            n = 0
        prev = p
    assert order == [5, 4, 3, 2, 1, 0], order
    r0, r1, r2, r4 = regs[0], regs[1], regs[2], regs[4]
    INT, FRAC = (r0 >> 15) & 0xFFFF, (r0 >> 3) & 0xFFF
    MOD = (r1 >> 3) & 0xFFF
    R, D, T = (r2 >> 14) & 0x3FF, (r2 >> 25) & 1, (r2 >> 24) & 1
    div = (r4 >> 20) & 7
    pfd = REFIN * (1 + D) / (R * (1 + T))
    vco = pfd * (INT + FRAC / MOD)
    assert 2.2e9 <= vco <= 4.4e9, vco
    assert vco / 2 ** div == 2.4e9, vco / 2 ** div
    assert pfd / ((r4 >> 12) & 0xFF) <= 125e3, "band-select clock over 125 kHz"
    assert (r1 >> 27) & 1 and INT >= 75, "prescaler 8/9 for INT 75 and over"
    t1 = ch[-1][0] + 50
    # sigrok decodes a register when the next one starts (CSN falling), so R0, the
    # last, is not among its lines: the asserts above read it
    save_trace("adf4351", samples(ch, 0, t1, 1), "spi:clk=2:mosi=3:cs=5,adf435x", "adf435x",
               [
                'Register: 5',
                'LD Pin Mode: Digital Lock Detect',
                'Register: 4',
                'Output Power: +5dBm',
                'Output Enable: Enabled',
                'AUX Output Power: -4dBm',
                'AUX Output Select: Divided Output',
                'AUX Output Enable: Disabled',
                'MTLD: Disabled',
                'VCO Power-Down: VCO Powered Up',
                'Band Select Clock Divider: 200',
                'RF Divider Select: ÷1',
                'Feedback Select: Fundamental',
                'Register: 3',
                'Clock Divider: 150',
                'Clock Divider Mode: Clock Divider Off',
                'CSR Enable: Disabled',
                'Charge Cancellation: Disabled',
                'ABP: 3ns (INT-N)',
                'Band Select Clock Mode: Low',
                'Register: 2',
                'Counter Reset: Disabled',
                'Charge Pump Three-State: Disabled',
                'Power-Down: Disabled',
                'PD Polarity: Positive',
                'LDP: 6ns',
                'LDF: INT-N',
                'Charge Pump Current Setting: 2.50mA @ 5.1kΩ',
                'Double Buffer: Disabled',
                'R Counter: 1',
                'RDIV2: Disabled',
                'Reference Doubler: Disabled',
                'MUXOUT: Digital Lock Detect',
                'Low Noise and Low Spur Modes: Low Noise Mode',
                'Register: 1',
                'MOD: 2',
                'Phase: 1',
                'Prescalar: 8/9',
                'Phase Adjust: Off',
               ], {"pin": 2, "hold": 3})
