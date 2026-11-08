# SPDX-License-Identifier: Apache-2.0
"""LIN master (firmware/lin_master.s) on a bus with a transceiver and a slave node,
both written from the LIN Specification Package 2.2A: the transceiver is a wired AND
of TXD and the slave, read back on RXD; the slave takes a dominant run of 11 bits or
more as a break (§2.3.1.1), reads the sync byte and the protected identifier
(checking P0 = ID0^ID1^ID2^ID4, P1 = ~(ID1^ID3^ID4^ID5)), receives what the master
publishes and checks its enhanced checksum (§2.3.1.5), and answers the identifier it
publishes after a response space of one bit."""
import cocotb
from cocotb.triggers import RisingEdge

from fw_tb import fw_boot, save_trace
from pins import samples

BIT = 625
DONE, BAD = 0x55, 0xEE
ID_A, ID_B = 0x10, 0x21
DATA_A = [0x01, 0x02, 0xA5, 0xFF]
DATA_B = [0x3C, 0xC3]


def pid(i):
    p0 = (i ^ i >> 1 ^ i >> 2 ^ i >> 4) & 1
    p1 = ~(i >> 1 ^ i >> 3 ^ i >> 4 ^ i >> 5) & 1
    return i | p0 << 6 | p1 << 7


def checksum(bs):
    s = 0
    for b in bs:
        s += b
        if s > 255:
            s -= 255
    return s ^ 0xFF


class Bus:
    """Transceiver and slave, a cycle at a time."""

    def __init__(self, dut, corrupt=False):
        self.dut, self.corrupt = dut, corrupt
        self.t, self.prev, self.low_from = 0, 1, None
        self.slave, self.sched = 1, []
        self.state, self.cur, self.frames, self.breaks = "idle", [], [], []
        self.changes = [(0, 1)]
        cocotb.start_soon(self.run())

    def respond(self, start, p):
        bs = DATA_B + [checksum([p] + DATA_B) ^ (1 if self.corrupt else 0)]
        t = start
        for b in bs:
            for lv in [0] + [(b >> k) & 1 for k in range(8)] + [1]:
                self.sched.append((t, lv))
                t += BIT

    async def run(self):
        dut = self.dut
        while True:
            await RisingEdge(dut.clk)
            self.t += 1
            oe, out = int(dut.uio_oe.value) & 1, int(dut.uio_out.value) & 1
            txd = out if oe else 1
            while self.sched and self.sched[0][0] <= self.t:
                self.slave = self.sched.pop(0)[1]
            line = txd & self.slave
            dut.uio_in.value = line << 1 | line
            if line != self.prev:
                self.changes.append((self.t, line))
            self.step(line)
            self.prev = line

    def step(self, line):
        t = self.t
        if line == 0 and self.prev == 1:
            self.low_from = t
        if line == 1 and self.prev == 0 and t - self.low_from >= 11 * BIT:
            self.breaks.append((self.low_from, t))
            self.state, self.cur, self.bits = "wait", [], None
            return
        if self.state == "wait" and line == 0 and self.prev == 1:
            self.state, self.t0, self.bits = "byte", t, []
        if self.state == "byte" and (t - self.t0) == BIT // 2 + len(self.bits) * BIT:
            self.bits.append(line)
            if len(self.bits) == 10:
                assert self.bits[0] == 0 and self.bits[9] == 1, f"a frame error: {self.bits}"
                self.cur.append(sum(b << k for k, b in enumerate(self.bits[1:9])))
                self.state = "wait"
                self.byte_done(t)

    def byte_done(self, t):
        c = self.cur
        if len(c) == 1:
            assert c[0] == 0x55, f"sync byte {c[0]:#04x}"
        elif len(c) == 2:
            assert c[1] == pid(c[1] & 0x3F), f"PID {c[1]:#04x}: parity"
            if c[1] & 0x3F == ID_B:
                # the stop bit's second half, then a one-bit response space
                self.respond(t + BIT // 2 + BIT, c[1])
        need = {ID_A: 2 + len(DATA_A) + 1, ID_B: 2 + len(DATA_B) + 1}.get(c[1] & 0x3F if len(c) > 1 else -1)
        if need and len(c) == need:
            self.frames.append(list(c))
            self.state = "idle"


async def run_lin(dut, corrupt=False):
    await fw_boot(dut, "lin_master.s")
    bus = Bus(dut, corrupt)
    for _ in range(400):
        await RisingEdge(dut.clk)
    while int(dut.uo_out.value) not in (DONE, BAD):
        for _ in range(1000):
            await RisingEdge(dut.clk)
        assert bus.t < 400_000, "no result"
    return bus


@cocotb.test()
async def test_lin_two_frames(dut):
    """The schedule twice: the master publishes ID 0x10, the slave answers ID 0x21."""
    bus = await run_lin(dut)
    assert int(dut.uo_out.value) == DONE
    assert len(bus.breaks) == 4
    for b0, b1 in bus.breaks:
        assert b1 - b0 == 13 * BIT, f"break {(b1 - b0) / BIT} bits"
    assert bus.frames[2:] == bus.frames[:2], bus.frames
    fa, fb = bus.frames[:2]
    assert fa[1] == pid(ID_A) and fa[2:6] == DATA_A, fa
    assert fa[6] == checksum([fa[1]] + DATA_A), f"checksum {fa[6]:#04x}"
    assert fb[1] == pid(ID_B) and fb[2:4] == DATA_B
    mem = dut.user_project.u_sram.mem
    resp = DATA_B + [checksum([pid(ID_B)] + DATA_B)]       # the data, then the checksum
    assert [int(mem[64 + i].value) for i in range(6)] == resp * 2
    t1 = bus.changes[-1][0] + 20 * BIT
    # sigrok shows a frame when the next break begins: A, B and A of the four
    def shown(f):
        return (["Sync", f"ID: {f[1] & 0x3F:02X} Parity: {f[1] >> 6} (ok)"]
                + [f"Data: 0x{b:02X}" for b in f[2:-1]] + [f"Checksum: 0x{f[-1]:02X}"])
    lines = shown(fa) + shown(fb) + shown(fa)
    save_trace("lin_master", samples(bus.changes, 0, t1, 25), "uart:rx=0:baudrate=19200,lin", "lin=data",
               lines, {"pin": 0, "hold": 18}, rate=12_000_000 // 25)


@cocotb.test()
async def test_lin_bad_checksum(dut):
    """A slave response with its checksum one off: the master reports BAD."""
    bus = await run_lin(dut, corrupt=True)
    assert int(dut.uo_out.value) == BAD
    assert len(bus.frames) == 4
