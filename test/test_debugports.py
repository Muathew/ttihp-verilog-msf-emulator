# SPDX-License-Identifier: Apache-2.0
"""Debug-port firmware against device models written from the standards.

firmware/jtag.s is a JTAG probe driven over the UART. The devices here are
IEEE 1149.1 TAP controllers (the 16-state machine, a 4-bit IR whose capture
value ends in 01, IDCODE selected by Test-Logic-Reset, BYPASS = all ones),
alone or chained TDI -> device 0 -> device 1 -> TDO. The models also check
the probe's pin discipline: TDI and TMS change only while TCK is low, and
settle at least two cycles before TCK rises.
"""
import cocotb

from fw_tb import Bench, fw_boot
from msf_tb import uart_decode
from test_monitors import Wave, uart_wave

BIT = 104                         # jtag.s default: 115200 baud at 12 MHz
RXP, TXP = 1, 0
TCKP, TDIP, TDOP, TMSP = 2, 3, 4, 5

# IEEE 1149.1 TAP controller: state -> (next with TMS = 0, next with TMS = 1)
TAP = {
    "TLR": ("RTI", "TLR"), "RTI": ("RTI", "SDR"),
    "SDR": ("CDR", "SIR"), "CDR": ("SHDR", "E1DR"), "SHDR": ("SHDR", "E1DR"),
    "E1DR": ("PDR", "UDR"), "PDR": ("PDR", "E2DR"), "E2DR": ("SHDR", "UDR"),
    "UDR": ("RTI", "SDR"),
    "SIR": ("CIR", "TLR"), "CIR": ("SHIR", "E1IR"), "SHIR": ("SHIR", "E1IR"),
    "E1IR": ("PIR", "UIR"), "PIR": ("PIR", "E2IR"), "E2IR": ("SHIR", "UIR"),
    "UIR": ("RTI", "SDR"),
}


class Tap:
    IRLEN, IDCODE, BYPASS = 4, 0b0001, 0b1111

    def __init__(self, idcode):
        self.idcode = idcode
        self.state, self.ir, self.sh, self.n, self.tdo = "TLR", self.IDCODE, 0, 1, 0
        self.updates = []         # ("IR" | "DR", value) at each Update state

    def rise(self, tms, tdi):
        s = self.state
        if s == "CDR":
            idc = self.ir == self.IDCODE
            self.sh, self.n = (self.idcode, 32) if idc else (0, 1)
        elif s == "CIR":
            self.sh, self.n = 0b0001, self.IRLEN
        elif s in ("SHDR", "SHIR"):
            self.sh = (self.sh >> 1) | (tdi << (self.n - 1))
        self.state = TAP[s][tms]

    def fall(self):
        s = self.state
        if s in ("SHDR", "SHIR"):
            self.tdo = self.sh & 1
        elif s == "UIR":
            self.ir = self.sh
            self.updates.append(("IR", self.sh))
        elif s == "UDR":
            self.updates.append(("DR", self.sh))
        elif s == "TLR":
            self.ir = self.IDCODE


class Chain:
    """TDI -> taps[0] -> taps[1] ... -> TDO, on the bench's pins."""

    def __init__(self, *idcodes):
        self.taps = [Tap(i) for i in idcodes]
        self.tck, self.tms, self.tdi = 0, 0, 0
        self.last_change, self.errors = 0, []

    def step(self, k, bench):
        out = bench.out[k]
        tck, tms, tdi = (out >> TCKP) & 1, (out >> TMSP) & 1, (out >> TDIP) & 1
        if (tms, tdi) != (self.tms, self.tdi):
            if tck or self.tck:
                self.errors.append(f"cycle {k}: TDI/TMS changed while TCK high")
            self.last_change = k
        if tck and not self.tck:
            if k - self.last_change < 2:
                self.errors.append(f"cycle {k}: TCK rose {k - self.last_change} cycle(s) after TDI/TMS")
            ins = [tdi] + [t.tdo for t in self.taps[:-1]]
            for t, i in zip(self.taps, ins):
                t.rise(tms, i)
        elif self.tck and not tck:
            for t in self.taps:
                t.fall()
        self.tck, self.tms, self.tdi = tck, tms, tdi
        return 1 << TDOP, self.taps[-1].tdo << TDOP


async def jtag_run(dut, chain, cmds, replies):
    """Send the command bytes; run until `replies` bytes came back."""
    items = [("byte", ord(c) if isinstance(c, str) else c, BIT, 1, 4 * BIT) for c in cmds]
    line = Wave(1 << RXP, uart_wave(items, pin=RXP), start=400)
    await fw_boot(dut, "jtag.s", uio_in=1 << RXP)
    bench = Bench(dut)
    bench.add(line)
    bench.add(chain)
    await bench.run(line.end + 12 * BIT * 3)
    back = uart_decode(bench.out, BIT, pin=TXP)
    assert not chain.errors, chain.errors[:5]
    assert len(back) >= replies, f"only {len(back)} reply bytes: {back}"
    return back


def le(v, n):
    return [(v >> (8 * i)) & 0xFF for i in range(n)]


@cocotb.test()
async def test_jtag_idcode(dut):
    """Reset, then a 32-bit DR scan reads the IDCODE the reset selected."""
    chain = Chain(0x12345679)
    back = await jtag_run(dut, chain, ["R", "D", 32, 0, 0, 0, 0], 5)
    assert back == [ord("R")] + le(0x12345679, 4), [hex(b) for b in back]
    assert chain.taps[0].state == "RTI"


@cocotb.test()
async def test_jtag_ir_bypass_and_partial_bytes(dut):
    """An IR scan reads the IR capture value (0001) and loads BYPASS; a DR scan
    then goes through the 1-bit bypass register (one cycle late, a 0 first),
    including a 13-bit scan that ends in a partial byte. Unknown commands and
    n = 0 are ignored."""
    chain = Chain(0x0BA00477)
    cmds = ["R", "X", "D", 0, "I", 4, 0x0F, "D", 8, 0xA5, "D", 13, 0xFF, 0x1F]
    back = await jtag_run(dut, chain, cmds, 5)
    assert back == [ord("R"), 0x01, (0xA5 << 1) & 0xFF, 0xFE, 0x1F], [hex(b) for b in back]
    assert ("IR", 0b1111) in chain.taps[0].updates


@cocotb.test()
async def test_jtag_chain(dut):
    """Two devices: a 64-bit DR scan after reset reads both IDCODEs (the one
    nearest TDO first); an all-ones IR scan puts both in BYPASS and returns
    both IR captures; a DR scan of ones then comes back with two leading
    zeros, which is how a host counts the devices on a chain."""
    ids = (0x0BA00477, 0x4BA00477)
    chain = Chain(*ids)
    cmds = ["R", "D", 64] + [0] * 8 + ["I", 8, 0xFF, "D", 16, 0xFF, 0xFF]
    back = await jtag_run(dut, chain, cmds, 12)
    assert back[:9] == [ord("R")] + le(ids[1], 4) + le(ids[0], 4), [hex(b) for b in back]
    assert back[9] == 0x11, hex(back[9])
    assert back[10:12] == [0xFC, 0xFF], [hex(b) for b in back[10:12]]
    assert all(t.ir == Tap.BYPASS and t.state == "RTI" for t in chain.taps)
