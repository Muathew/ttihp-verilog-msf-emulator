# SPDX-License-Identifier: Apache-2.0
"""MDIO station (firmware/mdio.s) against a PHY written from IEEE 802.3 clause 22
(22.2.4.5, 22.3.4): bits taken on MDC's rising edge; 32 ones of preamble, start 01,
op 10 (read) or 01 (write), PHY and register addresses, turnaround, 16 bits MSB first;
on a read the PHY drives the turnaround's 0 and then its data, each valid within 300 ns
after a rising edge of MDC; MDC high and low at least 160 ns, the period 400 ns or more."""
import cocotb
from cocotb.triggers import RisingEdge

from fw_tb import fw_boot, save_trace
from pins import samples

MDC, MDIO = 0x04, 0x08
PHYAD = 1


class Phy:
    def __init__(self, dut):
        self.dut = dut
        self.regs = {0: 0x3100, 2: 0x0022, 3: 0x1561}
        self.t, self.drive, self.pending = 0, 1, []
        self.ones, self.bits, self.state = 0, [], "pre"
        self.prev_mdc, self.hi_from, self.lo_from, self.last_rise = 0, None, 0, None
        self.changes = [(0, MDIO)]
        self.reads, self.writes = [], []
        cocotb.start_soon(self.run())

    async def run(self):
        dut, last = self.dut, None
        while True:
            await RisingEdge(dut.clk)
            self.t += 1
            while self.pending and self.pending[0][0] <= self.t:
                self.drive = self.pending.pop(0)[1]
            out, oe = int(dut.uio_out.value), int(dut.uio_oe.value)
            chip = (out & MDIO) >> 3 if oe & MDIO else 1
            line = chip & self.drive
            assert not (oe & MDIO and not self.drive), "the station and the PHY both drive"
            mdc = (out & MDC) >> 2 if oe & MDC else 0
            dut.uio_in.value = line << 3
            pads = mdc << 2 | line << 3
            if pads != last:
                self.changes.append((self.t, pads))
                last = pads
            if mdc and not self.prev_mdc:
                assert self.t - self.lo_from >= 2, "MDC low under 160 ns"
                assert self.last_rise is None or self.t - self.last_rise >= 5, "MDC period"
                self.last_rise, self.hi_from = self.t, self.t
                self.rise(line)
            if self.prev_mdc and not mdc:
                assert self.t - self.hi_from >= 2, "MDC high under 160 ns"
                self.lo_from = self.t
            self.prev_mdc = mdc

    def later(self, v):
        self.pending.append((self.t + 2, v))       # within 300 ns of the edge

    def rise(self, b):
        if self.state == "pre":
            if b:
                self.ones += 1
            elif self.ones >= 32:
                self.state, self.bits = "hdr", [0]  # the start's 0
            else:
                self.ones = 0
            return
        if self.state == "hdr":
            self.bits.append(b)
            if len(self.bits) == 14:
                v = int("".join(map(str, self.bits)), 2)
                assert v >> 12 == 0b01, "start"
                self.op, self.phy, self.reg = (v >> 10) & 3, (v >> 5) & 0x1F, v & 0x1F
                assert self.op in (0b10, 0b01), f"op {self.op:02b}"
                if self.op == 0b10:
                    self.state, self.n = "rd", -1   # next: the turnaround's Z edge
                else:
                    self.state, self.bits = "wr", []
            return
        if self.state == "rd":
            # after the Z edge the PHY drives the turnaround's 0; after the 0's edge
            # bit 15, ...; after bit 0's edge it lets go
            val = self.regs.get(self.reg, 0xFFFF) if self.phy == PHYAD else None
            if self.n == 0:
                assert b == 0, "the turnaround's 0 not on the line"
            if val is not None:
                if self.n == -1:
                    self.later(0)
                else:
                    self.later((val >> (15 - self.n)) & 1 if self.n < 16 else 1)
            self.n += 1
            if self.n == 17:
                self.reads.append((self.reg, val))
                self.state, self.ones = "pre", 0
            return
        if self.state == "wr":
            self.bits.append(b)
            if len(self.bits) == 18:
                assert self.bits[:2] == [1, 0], "a write's turnaround"
                v = int("".join(map(str, self.bits[2:])), 2)
                if self.phy == PHYAD:
                    self.regs[self.reg] = v
                self.writes.append((self.reg, v))
                self.state, self.ones = "pre", 0


@cocotb.test()
async def test_mdio_id_and_control(dut):
    """Read PHY 1's identifier (0x0022, 0x1561), then write 0x1200 to its control register."""
    await fw_boot(dut, "mdio.s")
    phy = Phy(dut)
    for _ in range(200):
        await RisingEdge(dut.clk)
    while int(dut.uo_out.value) != 0x55:
        for _ in range(200):
            await RisingEdge(dut.clk)
        assert phy.t < 20000, "not done"
    assert phy.reads == [(2, 0x0022), (3, 0x1561)], phy.reads
    assert phy.writes == [(0, 0x1200)] and phy.regs[0] == 0x1200
    mem = dut.user_project.u_sram.mem
    assert [int(mem[64 + i].value) for i in range(2)] == [0x0022, 0x1561]
    t1 = phy.changes[-1][0] + 50
    save_trace("mdio", samples(phy.changes, 0, t1, 1), "mdio:mdc=2:mdio=3", "mdio=decode",
               ["READ:  0022 PHYAD: 01 REGAD: 02", "READ:  1561 PHYAD: 01 REGAD: 03",
                "WRITE: 1200 PHYAD: 01 REGAD: 00"],
               {"pin": 2, "hold": 6})
