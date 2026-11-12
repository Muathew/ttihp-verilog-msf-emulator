# SPDX-License-Identifier: Apache-2.0
"""NES controller reader (firmware/nes_pad.s) against a pad written from the NESdev
wiki's "Standard controller" and the CD4021B data sheet: while LATCH (P/S) is high the
eight buttons load in parallel and A is on DATA; with LATCH low each rising CLK edge
shifts the next button out (B, Select, Start, Up, Down, Left, Right), then 1s from the
serial input; a pressed button is a 0."""
import cocotb
from cocotb.triggers import RisingEdge

from fw_tb import fw_boot, save_trace
from pins import samples

ORDER = ["A", "B", "Select", "Start", "Up", "Down", "Left", "Right"]
PRESSES = [["A", "Start", "Up"], [], ["B", "Left", "Right"]]


def pad_line(pressed):
    """sigrok's reading of one poll: it names the pad's directions by compass point."""
    compass = {"Up": "North", "Down": "South", "Left": "West", "Right": "East"}
    names = [compass.get(b, b) for b in ORDER if b in pressed]
    return " + ".join(names) if names else "No button is pressed"


def byte(pressed):
    return sum((0 if ORDER[i] in pressed else 1) << (7 - i) for i in range(8))


class Pad:
    def __init__(self, dut):
        self.dut, self.t, self.poll, self.sr = dut, 0, -1, 0xFF
        self.changes = [(0, 0x06)]
        cocotb.start_soon(self.run())

    async def run(self):
        d, pl, pc, last = self.dut, 0, 1, None
        while True:
            await RisingEdge(d.clk)
            self.t += 1
            out, oe = int(d.uio_out.value), int(d.uio_oe.value)
            lat = (out & 1) if oe & 1 else 0
            clk = ((out >> 1) & 1) if oe & 2 else 1
            if lat and not pl:
                self.poll += 1
            if lat:
                self.sr = byte(PRESSES[min(self.poll, len(PRESSES) - 1)])
            elif clk and not pc:
                self.sr = (self.sr << 1 | 1) & 0xFF
            q8 = self.sr >> 7
            d.uio_in.value = q8 << 2
            pads = lat | clk << 1 | q8 << 2
            if pads != last:
                self.changes.append((self.t, pads))
                last = pads
            pl, pc = lat, clk


@cocotb.test()
async def test_nes_pad_three_polls(dut):
    """Three polls, a frame (16.7 ms) apart: A+Start+Up, nothing, B+Left+Right. The program
    as shipped: with a shortened frame the wait between frames (its fifth deadline) is
    never issued, and the RTL cross-check of the deadlines then misses it."""
    await fw_boot(dut, "nes_pad.s")
    pad = Pad(dut)
    for _ in range(200):
        await RisingEdge(dut.clk)
    while int(dut.uo_out.value) != 0x55:
        for _ in range(500):
            await RisingEdge(dut.clk)
        assert pad.t < 450_000, "not done"
    mem = dut.user_project.u_sram.mem
    got = [int(mem[64 + i].value) for i in range(3)]
    assert got == [byte(p) for p in PRESSES], [hex(g) for g in got]
    t1 = pad.changes[-1][0] + 400
    save_trace("nes_pad", samples(pad.changes, 0, t1, 4), "spi:clk=1:miso=2:cpol=1:cpha=0,nes_gamepad",
               "nes_gamepad", [pad_line(p) for p in PRESSES], {"pin": 2, "hold": 30},
               rate=3_000_000)
