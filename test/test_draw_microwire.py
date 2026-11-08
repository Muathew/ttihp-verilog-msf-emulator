# SPDX-License-Identifier: Apache-2.0
"""Drawn protocol microwire (docs/draw/): the chip drives a 93C46 EEPROM.

The peer is a 93C46 in 16-bit mode (64 words) from the Microchip 93AA46/93LC46/
93C46 and ST M93C46 data sheets: SI taken on SK's rising edge, SO moved after it
(tPD), the dummy 0 before a READ's data, writes only after EWEN, the self-timed
write started by CS falling, and the status check (SO low while busy, high when
ready, with CS high). It checks the timing the contract and the sheets set: SK at
1 MHz or less, SK high and low >= 250 ns, SI set up and held >= 100 ns around the
rising edge, CS low >= 250 ns between instructions. The same session wrote this
and the firmware, so it is not an independent check: sigrok's decoders are.
"""
import cocotb

from fw_tb import Bench, fw_boot, save_trace
from msf_tb import uart_decode

CS, SK, SI, SO = 0, 1, 2, 3
NS = 1000 / 12                 # ns a cycle at 12 MHz
T_WRITE = 21600                # 1.8 ms, inside the contract's 2 ms
UBIT = 104


def cyc(ns):
    return int(-(-ns // NS))   # whole cycles covering ns


class Eeprom93C46:
    """93C46, ORG = 1 (x16): start bit, 2-bit opcode, A5-A0, then D15-D0."""

    def __init__(self, preload):
        self.mem = [0xFFFF] * 64
        for a, v in preload.items():
            self.mem[a] = v
        self.ewen = False
        self.errors, self.log = [], []
        self.busy_until = None       # cycle the write ends
        self.pending = None          # (addr, data) to store when the write ends
        self.so = None               # level driven on SO, None = high impedance
        self.so_next = []            # (cycle, level): SO changes, tPD after SK rises
        self.prev = None
        self.last_rise = self.last_fall = self.cs_fall = self.cs_rise = None
        self.reset_frame()
        self.status_polled_busy = False

    def reset_frame(self):
        self.bits, self.started, self.op, self.addr = [], False, None, None
        self.out_bits = []

    def err(self, k, msg):
        self.errors.append(f"cycle {k}: {msg}")

    def step(self, k, b):
        oe, out = b.oe[k], b.out[k]
        lv = {p: (out >> p) & 1 if (oe >> p) & 1 else None for p in (CS, SK, SI)}
        if self.busy_until is not None and k >= self.busy_until:
            a, d = self.pending
            self.mem[a] = d
            self.log.append(("written", a, d, k))
            self.busy_until = self.pending = None
        prev = self.prev
        self.prev = lv
        if prev is None:
            return 1 << SO, 0
        cs, sk, si = lv[CS], lv[SK], lv[SI]
        if cs is None or sk is None or si is None:
            if cs:
                self.err(k, "a pin the chip should drive is floating while CS is high")
        # CS edges
        if cs == 1 and prev[CS] != 1:
            self.cs_rise = k
            if self.cs_fall is not None and k - self.cs_fall < cyc(250):
                self.err(k, f"CS low for {k - self.cs_fall} cycles, tCSL is 250 ns")
            self.reset_frame()
            # the status check: while a write runs, SO is low (tSV after CS rises)
            self.so_next = [(k + 2, 0)] if self.busy_until is not None else []
        if cs == 0 and prev[CS] == 1:
            self.cs_fall = k
            self.so_next, self.so = [], None
            if self.op == "WRITE":
                if len(self.bits) == 9 + 16 and self.ewen:
                    data = int("".join(map(str, self.bits[9:])), 2)
                    self.pending = (self.addr, data)
                    self.busy_until = k + T_WRITE
                    self.log.append(("WRITE", self.addr, data, k))
                elif not self.ewen:
                    self.log.append(("WRITE ignored, not enabled", self.addr, None, k))
                else:
                    self.err(k, f"CS fell after {len(self.bits)} bits of a WRITE")
            elif self.op == "READ":
                self.log.append(("READ", self.addr, len(self.bits) - 9, k))
            elif self.op is not None:
                self.log.append((self.op, None, None, k))
            self.reset_frame()
        if cs == 1:
            # status check: SO goes high when the write ends, until a start bit or CS low
            if not self.started and self.busy_until is None and self.so == 0:
                self.so_next.append((k + 2, 1))
            if not self.started and self.busy_until is not None:
                self.status_polled_busy = True
            # SK edges
            if sk == 1 and prev[SK] == 0:
                if self.last_fall is not None and k - self.last_fall < cyc(250):
                    self.err(k, f"SK low for {k - self.last_fall} cycles, needs 250 ns")
                if self.last_rise is not None and k - self.last_rise < 12:
                    self.err(k, f"SK period {k - self.last_rise} cycles: faster than 1 MHz")
                if self.cs_rise is not None and k - self.cs_rise < cyc(50):
                    self.err(k, "SK rose too soon after CS")
                self.last_rise = k
                self.rise(k, b)
            if sk == 0 and prev[SK] == 1:
                if k - self.last_rise < cyc(250):
                    self.err(k, f"SK high for {k - self.last_rise} cycles, needs 250 ns")
                self.last_fall = k
            # SI held 100 ns after a rising edge
            if self.last_rise is not None and 0 < k - self.last_rise <= cyc(100) \
                    and si != b.out[self.last_rise] >> SI & 1:
                self.err(k, "SI changed within 100 ns after SK rose")
        for t, v in list(self.so_next):
            if k >= t:
                self.so = v
                self.so_next.remove((t, v))
        if self.so is None:
            return 1 << SO, 0           # high impedance: nobody drives (pulled low)
        return 1 << SO, self.so << SO

    def rise(self, k, b):
        si = (b.out[k] >> SI) & 1
        for j in range(1, cyc(100) + 1):  # set up 100 ns before the edge
            if (b.out[k - j] >> SI) & 1 != si:
                self.err(k, "SI changed within 100 ns before SK rose")
                break
        if not self.started:
            if si == 1:
                self.started = True
                self.bits = [1]
                if self.busy_until is None:
                    self.so_next, self.so = [], None   # a start bit ends the status check
            return
        if self.busy_until is not None:
            return                      # busy: instructions are ignored
        self.bits.append(si)
        n = len(self.bits)
        if n == 9:
            op = self.bits[1] << 1 | self.bits[2]
            a = int("".join(map(str, self.bits[3:9])), 2)
            self.addr = a
            if op == 0b10:
                self.op = "READ"
                self.out_bits = [0] + [(self.mem[a] >> (15 - i)) & 1 for i in range(16)]
            elif op == 0b01:
                self.op = "WRITE"
            elif op == 0b11:
                self.op = "ERASE"
            else:
                self.op = {0b11: "EWEN", 0b00: "EWDS", 0b10: "ERAL", 0b01: "WRAL"}[a >> 4]
                if self.op == "EWEN":
                    self.ewen = True
                elif self.op == "EWDS":
                    self.ewen = False
                elif self.op in ("ERAL", "WRAL"):
                    self.err(k, f"unexpected {self.op}")
        if self.op == "READ" and n >= 9:
            if self.out_bits:
                self.so_next.append((k + 2, self.out_bits.pop(0)))   # tPD: 167 ns
            else:
                self.err(k, "clocked past the word: a sequential read was not asked for")


@cocotb.test()
async def test_draw_microwire(dut):
    """EWEN, WRITE 5 = BEEF with the status check, READ 5, READ 6, EWDS."""
    await fw_boot(dut, "draw_microwire.s")
    dev = Eeprom93C46({6: 0x1234})
    bench = Bench(dut, pull=0, models=[dev])
    await bench.until(lambda b: b.uo[-1] & 0x02, 200_000, "the report")
    await bench.run(200)
    report = uart_decode([u & 1 for u in bench.uo], UBIT)
    for e in dev.errors[:10]:
        dut._log.error(e)
    for e in dev.log:
        dut._log.info(f"device saw {e}")
    assert not dev.errors, f"{len(dev.errors)} protocol errors"
    assert report == [0xBE, 0xEF, 0x12, 0x34], f"report {report}"
    ops = [e[0] for e in dev.log]
    assert ops == ["EWEN", "WRITE", "written", "READ", "READ", "EWDS"], ops
    assert dev.log[1][1:3] == (5, 0xBEEF) and dev.mem[5] == 0xBEEF
    assert dev.log[3][1:3] == (5, 16) and dev.log[4][1:3] == (6, 16)
    assert dev.status_polled_busy, "the chip never checked the status while busy"
    # the READ after the write starts only once the write is done
    assert dev.log[3][3] > dev.log[2][3]
    first = next(k for k in range(len(bench.out)) if bench.out[k] & 1)
    save_trace("draw_microwire", bench.pad[max(0, first - 50):],
               "microwire:cs=0:sk=1:si=2:so=3,eeprom93xx:addresssize=6:wordsize=16",
               "eeprom93xx=si-data:so-data",
               ["Write enable", "Write word", "Address: 0x0005", "Data: 0xbeef",
                "Read word", "Address: 0x0005", "Data: 0xbeef",
                "Read word", "Address: 0x0006", "Data: 0x1234", "Write disable"],
               {"pin": SI, "hold": 48})
