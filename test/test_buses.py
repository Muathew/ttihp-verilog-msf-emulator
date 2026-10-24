# SPDX-License-Identifier: Apache-2.0
"""Firmware for the other buses the brief names, against models written from
the specifications.

PS/2 (firmware/ps2_device.s): the chip plays a keyboard. The host model
follows the PS/2 protocol as a host does: it reads device frames on CLK
falling edges (start 0, 8 bits LSB first, odd parity, stop 1), and sends a
command by holding CLK low for 100 us, pulling DATA low and releasing CLK,
then setting each bit while the device holds CLK low, and checking the
device's ACK in the 11th clock. It also checks the device's timing: every
CLK phase is within the standard's 30-50 us, and DATA changes only while
CLK is high when the device is sending.
"""
import cocotb

from fw_tb import Bench, fw_boot
from msf_tb import uart_decode
from test_monitors import Wave, uart_wave

BIT = 104                          # UART: 115200 baud at 12 MHz
RXP, TXP = 1, 0
CLKP, DATP = 2, 3
US = 12                            # cycles per microsecond at 12 MHz


def odd_parity(b):
    return 1 - (bin(b).count("1") & 1)


class Ps2Host:
    def __init__(self, sends=(), inhibit_at=None):
        self.sends = list(sends)       # (cycle, byte, good_parity)
        self.inhibit_at = inhibit_at   # (n-th device frame, falling edge, cycles)
        self.pull_clk = self.pull_dat = 0
        self.clk = self.dat = 1
        self.bits, self.got, self.errors, self.acks = [], [], [], []
        self.tx = None                 # host-to-device state
        self.last_edge, self.phases, self.last_fall_bits = 0, [], 0
        self.inhibit_until, self.frames_started = None, 0

    def lines(self, k, bench):
        out, oe = bench.out[k], bench.oe[k]
        chip_clk = (oe >> CLKP) & 1 and not (out >> CLKP) & 1
        chip_dat = (oe >> DATP) & 1 and not (out >> DATP) & 1
        return int(not (chip_clk or self.pull_clk)), int(not (chip_dat or self.pull_dat))

    def step(self, k, bench):
        clk, dat = self.lines(k, bench)
        fall, rise = self.clk and not clk, clk and not self.clk
        # --- the host starts a command when it is due and the bus is quiet
        quiet = clk and not self.bits and k - self.last_edge >= 50 * US
        if self.tx is None and self.sends and k >= self.sends[0][0] and quiet:
            _, b, good = self.sends.pop(0)
            p = odd_parity(b) ^ (0 if good else 1)
            self.tx = {"bits": [(b >> i) & 1 for i in range(8)] + [p, 1], "n": 0,
                       "t": k, "stage": "inhibit"}
            self.pull_clk = 1
        if self.tx is not None:
            tx = self.tx
            if tx["stage"] == "inhibit" and k - tx["t"] >= 110 * US:
                self.pull_dat, tx["stage"] = 1, "rts"
            elif tx["stage"] == "rts" and k - tx["t"] >= 115 * US:
                self.pull_clk, tx["stage"] = 0, "bits"
            elif tx["stage"] == "bits" and fall:
                if tx["n"] < 10:
                    self.pull_dat = 1 - tx["bits"][tx["n"]]
                    tx["n"] += 1
                else:                      # the 11th clock: the device's ACK
                    self.acks.append(dat == 0)
                    tx["stage"] = "done"
            elif tx["stage"] == "done" and rise:
                self.tx = None
        elif self.inhibit_until is not None:
            if k >= self.inhibit_until:
                self.pull_clk, self.inhibit_until = 0, None
        else:
            # --- device to host: DATA must not change while CLK is low
            if dat != self.dat and not clk and not self.pull_clk:
                self.errors.append(f"cycle {k}: DATA changed while CLK low")
            if fall:
                if not self.bits:
                    self.frames_started += 1
                self.bits.append(dat)
                ia = self.inhibit_at
                if ia and self.frames_started == ia[0] and len(self.bits) == ia[1]:
                    self.pull_clk, self.inhibit_until = 1, k + ia[2]
                    self.bits, self.inhibit_at = [], None
                elif len(self.bits) == 11:
                    b = self.bits
                    v = sum(x << i for i, x in enumerate(b[1:9]))
                    if b[0] != 0 or b[10] != 1 or b[9] != odd_parity(v):
                        self.errors.append(f"cycle {k}: bad frame {b}")
                    self.got.append(v)
                    self.bits = []
        # --- clock phases inside a device frame: lows, and highs between bits
        if (fall or rise) and not self.pull_clk and self.tx is None:
            in_frame = (rise and self.last_fall_bits >= 1) or (fall and 2 <= len(self.bits) <= 11)
            if in_frame and self.last_edge:
                self.phases.append(k - self.last_edge)
            if fall:
                self.last_fall_bits = len(self.bits)
            self.last_edge = k
        self.clk, self.dat = clk, dat
        mask = (self.pull_clk << CLKP) | (self.pull_dat << DATP)
        return mask, 0


async def ps2_run(dut, host, uart_bytes, cycles, q=240):
    items = [("byte", b, BIT, 1, 20 * BIT) for b in uart_bytes]
    line = Wave(1 << RXP, uart_wave(items, pin=RXP) if items else [1 << RXP], start=400)
    await fw_boot(dut, "ps2_device.s", defines={"Q": q},
                  uio_in=1 << RXP | 1 << CLKP | 1 << DATP)
    bench = Bench(dut, pull=1 << CLKP | 1 << DATP)
    bench.add(line)
    bench.add(host)
    await bench.run(cycles)
    assert not host.errors, host.errors[:5]
    return uart_decode(bench.out, BIT, pin=TXP)


@cocotb.test()
async def test_ps2_device_types_scancodes(dut):
    """Scancodes from the UART reach the PS/2 host as valid frames (start,
    odd parity, stop), in order, with every clock phase inside 30-50 us."""
    host = Ps2Host()
    codes = [0x1C, 0xF0, 0x1C, 0x5A]
    await ps2_run(dut, host, codes, 400 + 4 * 30 * BIT + 4 * 12 * 1000)
    assert host.got == codes, [hex(b) for b in host.got]
    phases = [p for p in host.phases if p < 100 * US]
    assert phases and all(30 * US <= p <= 50 * US for p in phases), \
        (min(phases), max(phases))


@cocotb.test()
async def test_ps2_device_answers_commands(dut):
    """Reset, set LEDs (with its argument), echo and read ID get a keyboard's
    answers; each command byte is ACKed in the 11th clock and reported on the
    UART; a byte with bad parity gets FE."""
    q = 60                             # 4x the clock rate: same protocol, shorter test
    t0, gap = 2000, 9000
    cmds = [(0xFF, True), (0xED, True), (0x02, True), (0xEE, True), (0xF2, True), (0x55, False)]
    host = Ps2Host(sends=[(t0 + i * gap, b, g) for i, (b, g) in enumerate(cmds)])
    back = await ps2_run(dut, host, [], t0 + len(cmds) * gap + 6000, q=q)
    assert host.acks == [True] * len(cmds), host.acks
    assert host.got == [0xFA, 0xAA, 0xFA, 0xFA, 0xEE, 0xFA, 0xAB, 0x83, 0xFE], \
        [hex(b) for b in host.got]
    assert back == [0xFF, 0xED, 0x02, 0xEE, 0xF2], [hex(b) for b in back]


@cocotb.test()
async def test_ps2_device_host_inhibit(dut):
    """The host pulls CLK low in the middle of a frame (after 4 bits, for
    150 us): the device stops, and sends the whole byte again afterwards."""
    q = 60
    host = Ps2Host(inhibit_at=(1, 4, 150 * US))
    await ps2_run(dut, host, [0x1C, 0x32], 400 + 2 * 30 * BIT + 8000, q=q)
    assert host.got == [0x1C, 0x32], [hex(b) for b in host.got]


# ============================================================================
# CAN 2.0A: firmware/can.s
# ============================================================================
CTXP, CRXP = 2, 7
CBT = 480                          # can.s default: 50 kbit/s at 24 MHz
CSP = CBT // 2


def crc15(bits):
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


def msb(v, n):
    return [(v >> (n - 1 - i)) & 1 for i in range(n)]


def can_bits(ident, data, bad_crc=False):
    """A data frame's bits from SOF to the end of the CRC (not stuffed)."""
    bits = [0] + msb(ident, 11) + [0, 0, 0] + msb(len(data), 4)
    for d in data:
        bits += msb(d, 8)
    crc = crc15(bits) ^ (1 if bad_crc else 0)
    return bits + msb(crc, 15)


def stuff(bits):
    out, run, last = [], 0, None
    for b in bits:
        if run == 5:
            out.append(1 - last)
            run, last = 1, 1 - last
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
    if run == 5:
        out.append(1 - last)
    return out


class CanNode:
    """A CAN 2.0A node from the specification, on the bench's CAN pins: the
    bus is the wired AND of its TXD and the chip's, and is what the chip's
    RXD sees.

    It takes part once the bus has been recessive for 10 bits (bus
    integration), hard-synchronises on the falling edge of SOF, drives at bit starts and
    samples at CSP. It receives every frame (destuffing, CRC-15) and ACKs a
    good one if `ack`; it sends each of `frames` (ident, data, bad_crc) once,
    after the bus has been idle 11 bits and not before `start`, or joins the
    chip's SOF when one is waiting and `join` is set (arbitration). It sends
    no error frames and does not resend."""

    def __init__(self, frames=(), ack=True, start=0, join=False, gap=0):
        self.queue, self.ack, self.start, self.join = list(frames), ack, start, join
        self.gap, self.k = gap, 0
        self.txd, self.bus_prev, self.idle_since = 1, 1, 0
        self.active, self.got, self.sent, self.errors = False, [], [], []

    def step(self, k, bench):
        chip = (bench.out[k] >> CTXP) & 1
        bus = chip & self.txd
        if not self.active:
            idle = k - self.idle_since >= 10 * CBT           # bus integration
            if bus == 0 and self.bus_prev == 1 and idle:     # someone's SOF
                self.begin(k, tx=self.join and bool(self.queue), own_sof=False)
            elif bus == 1 and self.queue and k >= self.start \
                    and k - self.idle_since >= 11 * CBT:
                self.begin(k, tx=True, own_sof=True)
        if bus == 0:
            self.idle_since = k
        if self.active:
            self.run(k, bus)
        self.bus_prev = bus
        return 1 << CRXP, (chip & self.txd) << CRXP

    def begin(self, k, tx, own_sof):
        self.active, self.t0, self.tx = True, k, tx
        self.bits, self.runl, self.last, self.stuff_next = [], 0, 1, False
        self.total, self.crc_done, self.phase, self.n = -1, False, "frame", 0
        if tx:
            ident, data, bad = self.queue[0]
            self.txbits, self.txi = stuff(can_bits(ident, data, bad)), 1
            if own_sof:
                self.txd = 0                                 # SOF now

    def run(self, k, bus):
        self.k = k
        t, i = (k - self.t0) % CBT, (k - self.t0) // CBT
        if t == 0 and i > 0:                                 # bit start: drive
            if self.phase == "frame" and self.tx and self.txi < len(self.txbits):
                self.txd = self.txbits[self.txi]
                self.txi += 1
            elif self.phase == "ack" and not self.tx and self.ack and self.crc_ok:
                self.txd = 0
            else:
                self.txd = 1
        if t == CSP:
            self.sample(bus, i)

    def sample(self, b, i):
        if self.phase == "frame":
            if self.tx and i < len(self.txbits) and self.txbits[i] == 1 and b == 0:
                self.tx, self.txd = False, 1                 # lost arbitration
            if self.stuff_next:
                if b == self.last:
                    self.errors.append("stuff error")
                self.runl, self.last, self.stuff_next = 1, b, False
            else:
                self.bits.append(b)
                self.runl = self.runl + 1 if b == self.last else 1
                self.last = b
                self.stuff_next = self.runl == 5
                if len(self.bits) == 19:
                    dlc = sum(x << (3 - j) for j, x in enumerate(self.bits[15:19]))
                    self.total = 19 + 8 * min(dlc, 8) + 15
                if len(self.bits) == self.total:
                    self.crc_ok, self.crc_done = crc15(self.bits) == 0, True
            if self.crc_done and not self.stuff_next:
                self.phase = "delim"                         # the next bit
        elif self.phase == "delim":
            self.phase = "ack"
        elif self.phase == "ack":
            if self.tx:
                self.sent.append(b == 0)                     # ACKed?
                self.queue.pop(0)
                self.start = self.k + self.gap
            elif self.crc_ok:
                bits = self.bits
                ident = sum(x << (10 - j) for j, x in enumerate(bits[1:12]))
                dlc = sum(x << (3 - j) for j, x in enumerate(bits[15:19]))
                data = [sum(x << (7 - j) for j, x in enumerate(bits[19 + 8 * n:27 + 8 * n]))
                        for n in range(min(dlc, 8))]
                self.got.append((ident, dlc, data))
            self.phase, self.n = "eof", 0
        elif self.phase == "eof":
            self.n += 1
            if self.n >= 8:                                  # ACK delimiter + 7 EOF bits
                self.active, self.txd = False, 1


async def can_run(dut, nodes, cmds, cycles):
    items = []
    for c in cmds:
        bs = [ord(x) if isinstance(x, str) else x for x in c]
        items += [("byte", b, BIT, 1, BIT) for b in bs[:-1]]
        items.append(("byte", bs[-1], BIT, 1, 40 * BIT))
    line = Wave(1 << RXP, uart_wave(items, pin=RXP) if items else [1 << RXP], start=400)
    await fw_boot(dut, "can.s", uio_in=1 << RXP | 1 << CRXP)
    bench = Bench(dut)
    bench.add(line)
    for n in nodes:
        bench.add(n)
    await bench.run(cycles)
    back = uart_decode(bench.out, BIT, pin=TXP)
    dut._log.info(f"CAN replies: {[hex(b) for b in back]}")
    for n in nodes:
        assert not n.errors, n.errors
    return back


def tcmd(ident, data):
    return ["T", ident >> 8, ident & 0xFF, len(data)] + list(data)


def rframe(ident, data):
    return [ord("R"), ident >> 8, ident & 0xFF, len(data)] + list(data)


@cocotb.test()
async def test_can_receive_and_ack(dut):
    """A node sends two frames; the chip ACKs each and reports it; a frame
    with a bad CRC is reported as 'E' 'C' and not ACKed. (The frames are
    40 bits apart: the chip reports a frame at 115200 baud and misses a frame
    that starts while it is still reporting.)"""
    node = CanNode(frames=[(0x123, [0x11, 0x22, 0x33], False), (0x7FF, [], False),
                           (0x055, [0xA5], True)], start=2000, gap=40 * CBT)
    back = await can_run(dut, [node], [], 2000 + 3 * 200 * CBT)
    assert node.sent == [True, True, False], node.sent
    assert back == rframe(0x123, [0x11, 0x22, 0x33]) + rframe(0x7FF, []) + [ord("E"), ord("C")], \
        [hex(b) for b in back]


@cocotb.test()
async def test_can_transmit(dut):
    """'T' sends a data frame that a node receives with a good CRC and ACKs;
    the chip replies 'K'."""
    node = CanNode()
    data = [0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0xFF, 0x5A, 0x81]
    back = await can_run(dut, [node], [tcmd(0x2A5, data)], 400 + 40 * BIT * 13 + 200 * CBT)
    assert node.got == [(0x2A5, 8, data)], node.got
    assert back == [ord("K")], [hex(b) for b in back]


@cocotb.test()
async def test_can_arbitration_and_no_ack(dut):
    """The node joins the chip's SOF with a lower identifier: the chip loses
    arbitration in the identifier, receives and reports the node's frame, then
    sends its own again (ACKed: 'K'). With nobody to ACK: 8 tries, then 'N'."""
    node = CanNode(frames=[(0x0F0, [0x42], False)], join=True, start=10 ** 9)
    back = await can_run(dut, [node], [tcmd(0x100, [0x99])], 400 + 40 * BIT * 6 + 400 * CBT)
    assert back == rframe(0x0F0, [0x42]) + [ord("K")], [hex(b) for b in back]
    assert node.sent == [True] and node.got == [(0x100, 1, [0x99])], (node.sent, node.got)
    quiet = CanNode(ack=False)                           # only closes the TXD -> RXD loop
    back = await can_run(dut, [quiet], [tcmd(0x001, [])], 400 + 40 * BIT * 5 + 8 * 80 * CBT + 4000)
    assert back == [ord("N")], [hex(b) for b in back]


# ============================================================================
# 1-Wire: firmware/onewire.s
# ============================================================================
DQP = 3


def crc8_maxim(data):
    crc = 0
    for byte in data:
        for _ in range(8):
            mix = (crc ^ byte) & 1
            crc >>= 1
            if mix:
                crc ^= 0x8C
            byte >>= 1
    return crc


def make_rom(family, serial):
    b = [family] + [(serial >> (8 * i)) & 0xFF for i in range(6)]
    return b + [crc8_maxim(b)]


class OneWireDevice:
    """A 1-Wire slave from the Maxim specification (standard speed). Every
    falling edge the master makes starts a slot: if the device has a bit to
    send and it is 0, it holds the line low for 30 us (a read slot);
    otherwise it reads the master's bit 30 us after the fall (a write slot).
    A reset (low >= 480 us) is answered with a presence pulse 30 us after the
    release, 120 us long. ROM commands: READ ROM 33, SEARCH ROM F0 (each bit,
    its complement, then the master's choice: a device whose bit differs
    drops out until the next reset), MATCH ROM 55, SKIP ROM CC; then READ
    SCRATCHPAD BE returns 9 bytes ending in their CRC-8."""

    def __init__(self, rom, scratch=None):
        self.rom = rom
        self.rombits = [(rom[i // 8] >> (i % 8)) & 1 for i in range(64)]
        s = scratch or [0x50, 0x05, 0x4B, 0x46, 0x7F, 0xFF, 0x0C, 0x10]
        self.scratch = s + [crc8_maxim(s)]
        self.state, self.bits, self.out, self.si = "idle", [], [], 0
        self.pull = (0, 0)                  # cycles [from, to) it pulls low

    def reset(self, k):
        self.state, self.bits, self.out = "rom", [], []
        self.pull = (k + 30 * US, k + 150 * US)

    def fall(self, k):
        if self.state != "idle" and self.out and self.out[0] == 0:
            self.pull = (k, k + 30 * US)

    def slot(self, line):
        """30 us into a slot: a read slot's bit went out, or a write slot's
        bit is `line`."""
        if self.state == "idle":
            return
        if self.out:
            self.out.pop(0)
            if self.state == "search" and not self.out:
                self.state = "search_dir"
            return
        if self.state == "search_dir":
            if line != self.rombits[self.si]:
                self.state = "idle"
                return
            self.si += 1
            if self.si == 64:
                self.state = "func"
            else:
                b = self.rombits[self.si]
                self.out, self.state = [b, 1 - b], "search"
            return
        self.bits.append(line)
        if self.state == "rom" and len(self.bits) == 8:
            c = sum(x << i for i, x in enumerate(self.bits))
            self.bits = []
            if c == 0x33:
                self.out, self.state = list(self.rombits), "func"
            elif c == 0xCC:
                self.state = "func"
            elif c == 0x55:
                self.state = "match"
            elif c == 0xF0:
                b = self.rombits[0]
                self.si, self.out, self.state = 0, [b, 1 - b], "search"
            else:
                self.state = "idle"
        elif self.state == "match" and len(self.bits) == 64:
            self.state = "func" if self.bits == self.rombits else "idle"
            self.bits = []
        elif self.state == "func" and len(self.bits) == 8:
            c = sum(x << i for i, x in enumerate(self.bits))
            self.bits = []
            if c == 0xBE:
                self.out = [(b >> i) & 1 for b in self.scratch for i in range(8)]


class OneWireBus:
    """DQ, open drain: low if the chip or any device pulls it."""

    def __init__(self, devices):
        self.devs, self.chip_prev, self.fall_at = list(devices), False, None

    def step(self, k, bench):
        out, oe = bench.out[k], bench.oe[k]
        chip = bool((oe >> DQP) & 1 and not (out >> DQP) & 1)
        if chip and not self.chip_prev:                     # a slot starts
            self.fall_at = k
            for d in self.devs:
                d.fall(k)
        if not chip and self.chip_prev and k - self.fall_at >= 480 * US:
            for d in self.devs:                             # a reset
                d.reset(k)
        devs = any(d.pull[0] <= k < d.pull[1] for d in self.devs)
        if self.fall_at is not None and k == self.fall_at + 30 * US:
            line = 0 if (chip or devs) else 1
            for d in self.devs:
                d.slot(line)
        self.chip_prev = chip
        devs = any(d.pull[0] <= k < d.pull[1] for d in self.devs)
        return (1 << DQP) if devs else 0, 0


def ow_cycles(c):
    """How long command c keeps the master busy: its slots (70 us each)
    and its replies, with a margin; the probe has a one-byte UART buffer."""
    n = c[1] if c[0] in "Wr" else 0
    return n * 8 * 80 * US + (n + 1) * 11 * BIT + 1000 * US


async def ow_run(dut, devices, cmds, cycles_each=None):
    items = []
    for c in cmds:
        bs = [ord(x) if isinstance(x, str) else x for x in c]
        items += [("byte", b, BIT, 1, BIT) for b in bs[:-1]]
        items.append(("byte", bs[-1], BIT, 1, cycles_each or ow_cycles(c)))
    line = Wave(1 << RXP, uart_wave(items, pin=RXP), start=400)
    await fw_boot(dut, "onewire.s", uio_in=1 << RXP | 1 << DQP)
    bench = Bench(dut, pull=1 << DQP)
    bench.add(line)
    bench.add(OneWireBus(devices))
    await bench.run(line.end + 2000)
    back = uart_decode(bench.out, BIT, pin=TXP)
    dut._log.info(f"1-Wire replies: {[hex(b) for b in back]}")
    return back


@cocotb.test()
async def test_onewire_reset_read_rom_scratchpad(dut):
    """Reset with and without a device; READ ROM returns the 8 ROM bytes with
    a good CRC-8; SKIP ROM + READ SCRATCHPAD returns 9 bytes, CRC good."""
    rom = make_rom(0x28, 0x0000123456AB)
    dev = OneWireDevice(rom)
    back = await ow_run(dut, [dev], [["R"], ["W", 1, 0x33], ["r", 8],
                                     ["R"], ["W", 2, 0xCC, 0xBE], ["r", 9]])
    assert back[:2] == [ord("P"), ord("W")] and back[2:10] == rom, [hex(b) for b in back]
    assert crc8_maxim(back[2:9]) == back[9]
    sp = back[12:21]
    assert back[10:12] == [ord("P"), ord("W")] and sp == dev.scratch, [hex(b) for b in back]
    assert crc8_maxim(sp[:8]) == sp[8]
    back = await ow_run(dut, [], [["R"]])
    assert back == [ord("N")], back


@cocotb.test()
async def test_onewire_search_rom(dut):
    """SEARCH ROM finds each of three devices exactly once, then 'E'."""
    roms = [make_rom(0x28, 0x0000123456AB), make_rom(0x28, 0x0000123456AC),
            make_rom(0x10, 0x00000000BEEF)]
    devs = [OneWireDevice(r) for r in roms]
    back = await ow_run(dut, devs, [["S"]], cycles_each=3 * 200 * 210 * US)
    assert back and back[-1] == ord("E"), [hex(b) for b in back]
    found = [back[1 + 9 * i:9 + 9 * i] for i in range(len(back) // 9)]
    assert all(back[9 * i] == ord("D") for i in range(len(found))), [hex(b) for b in back]
    assert sorted(found) == sorted(roms), [[hex(b) for b in f] for f in found]
