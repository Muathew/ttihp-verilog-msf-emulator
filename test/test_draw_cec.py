# SPDX-License-Identifier: Apache-2.0
"""Drawn protocol cec (docs/draw/): the chip is HDMI-CEC logical address 4.

The peer is a TV at address 0, written from the numbers the Linux kernel's software
CEC states (drivers/media/cec/core/cec-pin.c, include/media/cec.h, cec-adap.c). It
runs on events (pin changes and timers), not every cycle: the five steps take
about half a second of bus time, 6.4 million cycles. It acknowledges frames for
address 0 by holding the line until 1.5 ms, says nothing about broadcasts, and,
once the bus has been quiet for five bit periods after the chip gave up on device
8, sends 04 83 and reads the chip's acknowledgements at 1.05 ms.

What the chip drives is checked afterwards, from its own pulls: start bits low
3.5-3.9 ms and 4.3-4.7 ms long, bits low 0.4-0.8 ms (1) or 1.3-1.7 ms (0) and
2.05-2.75 ms long, the frames' bytes, EOM and the retransmissions, the
signal-free time before each frame (5 bit periods for a new initiator, 7 for the
same initiator's next frame, 3 before a retransmission), and its acknowledgements
of the TV's frame held to 1.3-1.7 ms. The same session wrote this and the firmware,
so it is not an independent check: sigrok's cec decoder is.
"""
import cocotb
from cocotb.triggers import Event, First, Timer
from cocotb.utils import get_sim_time

from fw_tb import fw_boot, save_trace
from msf_tb import CLK_NS, uart_decode

MS = 12000                     # cycles a millisecond at 12 MHz
BIT = 28800                    # 2.4 ms
UBIT = 104


def now():
    return round(get_sim_time("ns") / CLK_NS)


class Bus:
    """The CEC line: low while the chip (uio_oe[0], uio_out[0] = 0) or the TV pulls."""

    def __init__(self, dut):
        self.dut, self.tv = dut, False
        self.t0 = now()
        self.changes = [(0, 1)]           # (cycle, level) of the line
        self.chip = []                    # (fall, rise) of the chip's own pulls
        self.tv_pulls = []
        self.evt = Event()
        dut.uio_in.value = 1
        self.task = cocotb.start_soon(self._watch())

    def t(self):
        return now() - self.t0

    def chip_pulls(self):
        return bool(int(self.dut.uio_oe.value) & 1) and not int(self.dut.uio_out.value) & 1

    def level(self):
        return 0 if self.tv or self.chip_pulls() else 1

    def _note(self):
        lv = self.level()
        self.dut.uio_in.value = lv        # what the chip reads: the wired AND
        if lv != self.changes[-1][1]:
            self.changes.append((self.t(), lv))
            self.evt.set()
            self.evt = Event()

    async def _watch(self):
        was = False
        while True:
            await First(self.dut.uio_oe.value_change, self.dut.uio_out.value_change)
            c = self.chip_pulls()
            if c != was:
                if c:
                    self.chip.append([self.t(), None])
                else:
                    self.chip[-1][1] = self.t()
                was = c
            self._note()

    def pull(self, on):
        self.tv = on
        if on:
            self.tv_pulls.append([self.t(), None])
        else:
            self.tv_pulls[-1][1] = self.t()
        self._note()

    async def wait_level(self, lv, limit=None):
        end = None if limit is None else self.t() + limit
        while self.level() != lv:
            if end is not None and self.t() >= end:
                return False
            trig = [self.evt.wait()]
            if end is not None:
                trig.append(Timer((end - self.t()) * CLK_NS, unit="ns"))
            await First(*trig)
        return True

    async def hold(self, cycles):
        if cycles > 0:
            await Timer(cycles * CLK_NS, unit="ns")


class Tv:
    """Logical address 0: follows frames as they go, acknowledges its own."""

    def __init__(self, bus):
        self.bus = bus
        self.sending = False
        self.saw_absent = False
        self.acks = []                    # what the chip did at the ACK bits of our frame
        self.task = cocotb.start_soon(self.follow())

    async def follow(self):
        b = self.bus
        while True:
            await b.wait_level(0)
            fall = b.t()
            if self.sending:
                await b.wait_level(1)
                continue
            await b.wait_level(1)
            if b.t() - fall >= int(3.5 * MS):        # a start bit: a new frame
                bits, dest = [], None
                while True:
                    ok = await b.wait_level(0, int(2.75 * MS))
                    if not ok:
                        break
                    f = b.t()
                    n = len(bits) % 10
                    if n == 9 and dest == 0:          # an ACK bit for us: a 0
                        b.pull(True)
                        await b.hold(f + int(1.5 * MS) - b.t())
                        b.pull(False)
                    await b.wait_level(1)
                    bits.append(1 if b.t() - f < int(1.05 * MS) else 0)
                    if len(bits) == 8:
                        hdr = int("".join(map(str, bits)), 2)
                        dest = hdr & 0xF
                        if hdr == 0x48:
                            self.saw_absent = True
                    if len(bits) % 10 == 0 and bits[-2] == 1:
                        break                         # EOM, then its ACK bit

    async def send(self, data):
        """As initiator: start bit, then each byte, EOM, and an ACK bit sent as 1."""
        b = self.bus
        self.sending = True
        t = b.t()
        b.pull(True)
        await b.hold(t + int(3.7 * MS) - b.t())
        b.pull(False)
        t += int(4.5 * MS)
        for i, byte in enumerate(data):
            bits = [(byte >> (7 - k)) & 1 for k in range(8)] + [int(i == len(data) - 1), 1]
            for k, v in enumerate(bits):
                await b.hold(t - b.t())
                b.pull(True)
                await b.hold(t + (int(0.6 * MS) if v else int(1.5 * MS)) - b.t())
                b.pull(False)
                if k == 9:
                    await b.hold(t + int(1.05 * MS) - b.t())
                    self.acks.append(b.level() == 0)
                t += BIT
        await b.hold(t - b.t())
        self.sending = False

    async def initiate_when_quiet(self):
        """After the chip's attempts on device 8, wait for 5 quiet bit periods."""
        b = self.bus
        while not self.saw_absent:
            await b.hold(MS)
        while True:
            last_fall = max(t for t, lv in b.changes if lv == 0)
            quiet = b.t() - last_fall - BIT
            if quiet >= 5 * BIT and b.level() == 1:
                break
            await b.hold(MS // 10)
        await self.send([0x04, 0x83])


def frames(pulls):
    """The chip's own pulls -> its frames: (start fall, [(fall, low)] bits)."""
    out = []
    for fall, rise in pulls:
        low = rise - fall
        if low >= int(3 * MS):
            out.append((fall, low, []))
        elif out:
            out[-1][2].append((fall, low))
    return out


@cocotb.test()
async def test_draw_cec(dut):
    """Image View On, Active Source, three retries to an absent device, the TV's Give
    Physical Address acknowledged, Report Physical Address."""
    await fw_boot(dut, "draw_cec.s")
    bus = Bus(dut)
    tv = Tv(bus)
    cocotb.start_soon(tv.initiate_when_quiet())
    uo = []

    async def report():
        while True:
            await dut.uo_out.value_change
            uo.append((bus.t(), int(dut.uo_out.value)))

    cocotb.start_soon(report())
    while not uo or not uo[-1][1] & 2:
        await Timer(5 * MS * CLK_NS, unit="ns")
        assert bus.t() < 800 * MS, "no report after 800 ms"
    # the report, from uo_out's changes
    samples, i, lv = [], 0, 1
    t0 = next(t for t, v in uo if v & 1)
    for t in range(t0, uo[-1][0] + 10 * UBIT):
        while i < len(uo) and uo[i][0] <= t:
            lv = uo[i][1] & 1
            i += 1
        samples.append(lv)
    rep = uart_decode(samples, UBIT)
    errors = []
    # the chip's frames
    chip_pulls = [(f, r) for f, r in bus.chip if r is not None]
    # the TV's own frame: from its start bit to its last pull (its other pulls are
    # acknowledgements of the chip's frame 1)
    tv_start = [f for f, r in bus.tv_pulls if r is not None and r - f >= int(3 * MS)]
    tv_frame = (tv_start[0], bus.tv_pulls[-1][1]) if tv_start else (0, 0)
    own = [(f, r) for f, r in chip_pulls if not tv_frame[0] <= f <= tv_frame[1]]
    acks = [(f, r) for f, r in chip_pulls if tv_frame[0] <= f <= tv_frame[1]]
    got = []
    prev_end = None
    for start, low, bits in frames(own):
        if not int(3.5 * MS) <= low <= int(3.9 * MS):
            errors.append(f"start bit low {low / MS:.3f} ms")
        if bits and not int(4.3 * MS) <= bits[0][0] - start <= int(4.7 * MS):
            errors.append(f"start bit {(bits[0][0] - start) / MS:.3f} ms long")
        val = []
        for j, (f, lw) in enumerate(bits):
            if int(0.4 * MS) <= lw <= int(0.8 * MS):
                val.append(1)
            elif int(1.3 * MS) <= lw <= int(1.7 * MS):
                val.append(0)
            else:
                errors.append(f"a bit low {lw / MS:.3f} ms")
                val.append(None)
            if j + 1 < len(bits) and not int(2.05 * MS) <= bits[j + 1][0] - f <= int(2.75 * MS):
                errors.append(f"a bit {(bits[j + 1][0] - f) / MS:.3f} ms long")
        by = [int("".join(map(str, val[k:k + 8])), 2) for k in range(0, len(val), 10)
              if None not in val[k:k + 8] and len(val[k:k + 8]) == 8]
        eom = [val[k + 8] for k in range(0, len(val) - 8, 10)]
        if val and any(v != 1 for v in val[9::10]):
            errors.append("an ACK bit sent as 0 by the initiator")
        got.append((start, by, eom))
        # signal-free time: from the end of the last bit period before this frame
        if prev_end is not None:
            gap = (start - prev_end) / BIT
            want = 3 if got[-2][1][:1] == by[:1] and by[:1] == [0x48] else 7
            if tv_frame[1] and prev_end < tv_frame[1] < start:
                want, gap = 5, (start - (bus.tv_pulls[-1][0] + BIT)) / BIT
            if gap < want:
                errors.append(f"frame {by}: {gap:.2f} quiet bit periods, needs {want}")
        if prev_end is None and start < 5 * BIT:
            errors.append(f"first frame after {start / BIT:.2f} quiet bit periods, needs 5")
        prev_end = bits[-1][0] + BIT if bits else start + int(4.5 * MS)
    for e in errors[:10]:
        dut._log.error(e)
    dut._log.info(f"frames {[(round(s / MS, 1), [hex(x) for x in b], e) for s, b, e in got]}")
    dut._log.info(f"TV frame acked {tv.acks}; chip's ack pulls "
                  f"{[round((r - f) / MS, 3) for f, r in acks]} ms; report {rep}")
    assert not errors, f"{len(errors)} timing errors"
    seq = [b for _, b, _ in got]
    assert seq[0] == [0x40, 0x04] and got[0][2] == [0, 1], got[0]
    assert seq[1] == [0x4F, 0x82, 0x10, 0x00] and got[1][2] == [0, 0, 0, 1], got[1]
    tries = [b for b in seq[2:] if b == [0x48]]
    assert seq[2:2 + len(tries)] == tries, seq
    assert 2 <= len(tries) <= 6, f"{len(tries)} attempts on device 8: retransmit 1..5 times"
    assert seq[2 + len(tries):] == [[0x4F, 0x84, 0x10, 0x00, 0x04]], seq
    assert tv.acks == [True, True], f"the chip did not acknowledge the TV's frame: {tv.acks}"
    for f, r in acks:
        tv_fall = max(t for t, _ in bus.tv_pulls if t <= f + 20)
        assert int(1.3 * MS) <= r - tv_fall <= int(1.7 * MS), "acknowledgement not 1.3-1.7 ms"
    assert rep == [ord("A"), ord("A"), ord("N"), 0x04, 0x83, ord("A")], rep
    dut._log.info(f"{len(tries)} attempts on device 8")
    # the line for sigrok, a sample every 12 cycles (1 MHz)
    div = 12
    samples, i, lv = [], 0, 1
    end = bus.changes[-1][0] + 4 * BIT
    for t in range(0, end, div):
        while i < len(bus.changes) and bus.changes[i][0] <= t:
            lv = bus.changes[i][1]
            i += 1
        samples.append(lv)
    # sigrok's summary of each frame: who to whom, the opcode, operands and the result.
    # Address 8 is Playback_2 in its table; a broadcast nobody refused shows as ACK.
    hdr = "HDR: Playback_1, "
    expect = [hdr + "TV | OPC: IMAGE_VIEW_ON | R: ACK",
              hdr + "Broadcast | OPC: ACTIVE_SOURCE | OPS: 0x10, 0x00 | R: ACK"]
    expect += [hdr + "Playback_2 | OPC: NONE. Aborted cmd | R: NACK"] * len(tries)
    expect += ["HDR: TV, Playback_1 | OPC: GIVE_PHYSICAL_ADDRESS | R: ACK",
               hdr + "Broadcast | OPC: REPORT_PHYSICAL_ADDRESS | OPS: 0x10, 0x00, 0x04 | R: ACK"]
    save_trace("draw_cec", samples, "cec:cec=0", "cec=sections", expect, {"pin": 0, "hold": 600},
               rate=12_000_000 // div)
