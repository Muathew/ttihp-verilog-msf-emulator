# SPDX-License-Identifier: Apache-2.0
"""RC servo driver (firmware/servo.s) against the servo interface as servo makers
describe it: a positive pulse every 20 ms, 1.0 to 2.0 ms wide (1.5 ms the centre);
two channels in turn, the second 2.5 ms after the first, as an RC receiver does."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, edges, samples

US = 12
MOVES = [(1000, 2000), (1500, 1500), (2000, 1000), (1250, 1750)]


@cocotb.test()
async def test_servo_moves(dut):
    """Four frames: each width to the cycle, frames 20 ms apart, channel 2 2.5 ms late."""
    await fw_boot(dut, "servo.s")
    ch = await watch_pins(dut, len(MOVES) * 20000 * US + 4000, pull=0)
    pulses = []
    for pin in (0, 1):
        e = edges(ch, pin)
        rises = [t for t, v in e if v == 1]
        falls = [t for t, v in e if v == 0]
        assert len(rises) == len(falls) == len(MOVES), (pin, len(rises), len(falls))
        pulses.append(list(zip(rises, falls)))
    for k, (w1, w2) in enumerate(MOVES):
        (r1, f1), (r2, f2) = pulses[0][k], pulses[1][k]
        assert f1 - r1 == w1 * US and f2 - r2 == w2 * US, (k, (f1 - r1) / US, (f2 - r2) / US)
        assert r2 - r1 == 2500 * US, (r2 - r1) / US
        if k:
            assert r1 - pulses[0][k - 1][0] == 20000 * US, (r1 - pulses[0][k - 1][0]) / US
    t1 = pulses[0][-1][0] + 20000 * US
    # sigrok's pwm decoder gives each closed period's duty cycle: the first three
    lines = [f"{w1 / 20000 * 100:.6f}%" for w1, _ in MOVES[:3]]
    save_trace("servo", samples(ch, 0, t1, US), "pwm:data=0", "pwm=duty-cycle", lines,
               {"pin": 0, "hold": 300}, rate=1_000_000)
