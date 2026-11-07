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

from fw_tb import Bench, fw_boot, save_trace
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
    save_trace("jtag", Bench.last.pad, f"jtag:tdi={TDIP}:tdo={TDOP}:tck={TCKP}:tms={TMSP}",
               "jtag=bitstring-tdo", [f"DR TDO: {0x12345679:032b} (0x12345679), 32 bits"],
               {"pin": TDOP, "hold": 40})


@cocotb.test()
async def test_jtag_ir_bypass_and_partial_bytes(dut):
    """An IR scan reads the IR capture value (0001) and loads BYPASS; a DR scan
    then goes through the 1-bit bypass register (one cycle late, a 0 first),
    including a 13-bit scan that ends in a partial byte. Unknown commands and
    n = 0 are ignored."""
    chain = Chain(0x0BA00477)
    # the 13-bit scan's last byte is 0xFF: its 3 unsent bits stay in the shift
    # register and must not leak into the 5 bits read back
    cmds = ["R", "X", "D", 0, "I", 4, 0x0F, "D", 8, 0xA5, "D", 13, 0xFF, 0xFF]
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


# ============================================================================
# SWD (ARM ADIv5 Serial Wire Debug): firmware/swd.s
# ============================================================================
CLKP, DIOP = 2, 3


def parity(v):
    return bin(v).count("1") & 1


class SwdTarget:
    """An ADIv5 SW-DP with one MEM-AP, from the specification.

    The target samples SWDIO on SWCLK rising edges and changes it on rising
    edges. A line reset is >= 50 ones; 0xE79E (LSB first) after a line reset
    switches from JTAG to SWD, and the next line reset followed by an idle 0
    makes it ready. A request is 8 bits (start, APnDP, RnW, A2, A3, parity,
    stop, park); counting rising edges from its park bit (R8): the ACK is
    driven at R9-R11; an OK read drives the data at R12-R43 and parity at R44
    and lets go at R45; an OK write lets go at R12 and samples the data at
    R14-R45 and parity at R46; WAIT/FAULT let go at R12. AP reads are posted
    (they return the previous AP read; RDBUFF returns the last)."""
    DPIDR, IDR = 0x2BA01477, 0x24770011

    def __init__(self, mem=None, wait_drw=0):
        self.mem = dict(mem or {})
        self.wait_drw = wait_drw
        self.clk, self.ones, self.hist = 1, 0, 0
        self.mode, self.ready, self.phase = "jtag", False, "idle"
        self.bits, self.n, self.req, self.word = [], 0, None, 0
        self.drive = None
        self.select = self.csw = self.tar = self.ctrl = self.rdbuff = 0
        self.errors, self.log = [], []

    # registers ---------------------------------------------------------
    def read(self, ap, a):
        if not ap:
            # CTRL/STAT: each power-up request (bits 30, 28) acknowledged at once (31, 29)
            return {0x0: self.DPIDR, 0x4: self.ctrl | ((self.ctrl << 1) & 0xA0000000),
                    0x8: 0, 0xC: self.rdbuff}[a]
        reg = (self.select & 0xF0) | a
        val = {0x00: self.csw, 0x04: self.tar, 0xFC: self.IDR}.get(reg)
        if reg == 0x0C:
            val = self.mem.get(self.tar, 0)
        old, self.rdbuff = self.rdbuff, val or 0
        return old                            # posted

    def write(self, ap, a, v):
        self.log.append(("W", ap, a, v))
        if not ap:
            if a == 0x4:
                self.ctrl = v
            elif a == 0x8:
                self.select = v
            return
        reg = (self.select & 0xF0) | a
        if reg == 0x00:
            self.csw = v
        elif reg == 0x04:
            self.tar = v
        elif reg == 0x0C:
            self.mem[self.tar] = v

    # pins ----------------------------------------------------------------
    def step(self, k, bench):
        out, oe = bench.out[k], bench.oe[k]
        clk = (out >> CLKP) & 1
        host = (oe >> DIOP) & 1
        if host and self.drive is not None:
            self.errors.append(f"cycle {k}: host and target both drive SWDIO")
        if clk and not self.clk:
            dio = (out >> DIOP) & 1 if host else (self.drive if self.drive is not None else 1)
            self.rise(dio, host, k)
        self.clk = clk
        if self.drive is None:
            return 0, 0
        return 1 << DIOP, self.drive << DIOP

    def rise(self, dio, host, k):
        self.ones = self.ones + 1 if (host and dio) else 0
        self.hist = (self.hist >> 1) | (dio << 15)
        if self.phase == "idle" or self.ones >= 50:
            if self.ones >= 50:
                self.phase, self.ready, self.drive = "idle", False, None
            elif self.mode == "jtag" and self.hist == 0xE79E:
                self.mode = "switched"
            elif host and not dio and self.mode != "jtag":
                if self.mode == "switched":
                    self.mode = "swd"
                self.ready = self.mode == "swd"
            elif host and dio and self.ready:
                self.phase, self.bits = "req", [1]
            return
        if self.phase == "req":
            self.bits.append(dio)
            if len(self.bits) == 8:
                b = self.bits
                ap, rnw, a = b[1], b[2], (b[3] | b[4] << 1) << 2
                if b[0] != 1 or b[6] != 0 or b[7] != 1 or b[5] != b[1] ^ b[2] ^ b[3] ^ b[4]:
                    self.errors.append(f"cycle {k}: bad request {b}")
                    self.phase = "idle"
                    return
                drw = ap and ((self.select & 0xF0) | a) == 0x0C
                ack = 1
                if drw and self.wait_drw:
                    self.wait_drw -= 1
                    ack = 2
                data = self.read(ap, a) if (rnw and ack == 1) else 0
                self.req, self.n = (ap, rnw, a, ack, data), 8
                self.phase = "resp"
            return
        # response phase: self.n = the number of the rising edge just seen
        self.n += 1
        n = self.n
        ap, rnw, a, ack, data = self.req
        if 9 <= n <= 11:
            self.drive = (ack >> (n - 9)) & 1
        elif ack != 1 or not rnw:
            if n == 12:
                self.drive = None
                if ack != 1:
                    self.phase = "idle"
            elif not rnw and 14 <= n <= 45:
                self.word |= dio << (n - 14)
            elif not rnw and n == 46:
                if dio != parity(self.word):
                    self.errors.append(f"cycle {k}: write data parity error")
                self.write(ap, a, self.word)
                self.word, self.phase = 0, "idle"
        else:
            if 12 <= n <= 43:
                self.drive = (data >> (n - 12)) & 1
            elif n == 44:
                self.drive = parity(data)
            elif n == 45:
                self.drive, self.phase = None, "idle"


def hdr(ap, rnw, a):
    return ap | rnw << 1 | ((a >> 2) & 1) << 2 | ((a >> 3) & 1) << 3


def rd(ap, a):
    return ["T", hdr(ap, 1, a)]


def wr(ap, a, v):
    return ["T", hdr(ap, 0, a)] + le(v, 4)


async def swd_run(dut, target, cmds, replies):
    """cmds: one list of bytes per command. Like a real host, the test waits
    for each command's reply before sending the next (the probe has a
    one-byte UART receive buffer)."""
    items = []
    for c in cmds:
        bs = [ord(x) if isinstance(x, str) else x for x in c]
        items += [("byte", b, BIT, 1, BIT) for b in bs[:-1]]
        items.append(("byte", bs[-1], BIT, 1, 80 * BIT))
    line = Wave(1 << RXP, uart_wave(items, pin=RXP), start=400)
    await fw_boot(dut, "swd.s", uio_in=1 << RXP | 1 << DIOP)
    bench = Bench(dut, pull=1 << DIOP)
    bench.add(line)
    if target:
        bench.add(target)
    await bench.run(line.end + 12 * BIT * 7)
    back = uart_decode(bench.out, BIT, pin=TXP)
    dut._log.info(f"SWD replies: {[hex(b) for b in back]}")
    if target:
        assert not target.errors, target.errors[:5]
    assert len(back) >= replies, f"only {len(back)} reply bytes: {back}"
    return back


def ok_read(v):
    return [1] + le(v, 4) + [parity(v)]


@cocotb.test()
async def test_swd_dpidr(dut):
    """Line reset + JTAG-to-SWD switch, then a DP read of DPIDR."""
    t = SwdTarget()
    back = await swd_run(dut, t, [["L"], rd(0, 0x0)], 7)
    assert back == [ord("L")] + ok_read(SwdTarget.DPIDR), [hex(b) for b in back]
    assert t.mode == "swd"
    save_trace("swd", Bench.last.pad, f"swd:swclk={CLKP}:swdio={DIOP}",
               "swd=reset:enable:read:write:ack:data",
               ["LINERESET", "JTAG->SWD", "LINERESET", "IDCODE", "OK",
                f"0x{SwdTarget.DPIDR:08x}"], {"pin": DIOP, "hold": 40})


@cocotb.test()
async def test_swd_memap_read_write(dut):
    """SELECT bank 0xF and read the AP's IDR (posted: RDBUFF returns it);
    then CSW, TAR, a posted DRW read of the target's memory and a DRW write."""
    t = SwdTarget(mem={0x20000000: 0xDEADBEEF})
    cmds = [["L"], wr(0, 0x8, 0xF0), rd(1, 0xC), rd(0, 0xC),
            wr(0, 0x8, 0x00), wr(1, 0x0, 0x23000002), wr(1, 0x4, 0x20000000),
            rd(1, 0xC), rd(0, 0xC), wr(1, 0xC, 0xCAFEF00D)]
    back = await swd_run(dut, t, cmds, 1 + 1 + 6 + 6 + 1 + 1 + 1 + 6 + 6 + 1)
    exp = ([ord("L"), 1] + ok_read(0) + ok_read(SwdTarget.IDR) + [1, 1, 1]
           + ok_read(SwdTarget.IDR) + ok_read(0xDEADBEEF) + [1])
    assert back == exp, [hex(b) for b in back]
    assert t.csw == 0x23000002 and t.tar == 0x20000000
    assert t.mem[0x20000000] == 0xCAFEF00D


@cocotb.test()
async def test_swd_wait_and_no_target(dut):
    """A WAIT ACK ends the transaction with no data phase and the retry
    succeeds; with no target on the wire the ACK reads 7 (pulled high)."""
    t = SwdTarget(mem={0x0: 0x12345678}, wait_drw=1)
    cmds = [["L"], rd(1, 0xC), rd(1, 0xC), rd(0, 0xC)]
    back = await swd_run(dut, t, cmds, 1 + 1 + 6 + 6)
    assert back == [ord("L"), 2] + ok_read(0) + ok_read(0x12345678), [hex(b) for b in back]
    back = await swd_run(dut, None, [["L"], rd(0, 0x0)], 2)
    assert back[:2] == [ord("L"), 7], back
