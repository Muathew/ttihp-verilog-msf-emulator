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
