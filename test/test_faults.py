# SPDX-License-Identifier: Apache-2.0
"""Timing-fault injection: the controllers' timing knobs (firmware defines)
move edges by exact, documented amounts, so the chip can break a device's
timing on purpose and find how much it tolerates.

  uart_tx.s  BIT (bit time), STOP (stop bit length)
  i2c_master.s  QL / QH (SCL low / high delay counts, 3 cycles each)
  bridge.s   SH / SL (SCK high / low delay counts, 4 cycles each)

Each knob is measured at the pins, and the UART knob drives a tolerance
sweep against a receiver model whose failure points are known exactly.
"""
import cocotb

from fw_tb import Bench, Recorder, fw_boot, edges
from test_monitors import Wave, uart_wave

DONE = 0x55


def runs(levels, start=0):
    """[(level, length)] of a per-cycle level list, from `start`."""
    e = edges(levels, start) + [len(levels)]
    return [(levels[a], b - a) for a, b in zip(e, e[1:])]


async def uart_frames(dut, msg, bit, stop):
    data = {i: b for i, b in enumerate(msg + b"\0")}
    await fw_boot(dut, "uart_tx.s", data=data, defines={"BIT": bit, "STOP": stop})
    rec = Recorder(dut)
    await rec.until_uo(lambda v: v == DONE, 20 * (bit + stop) * (len(msg) + 2))
    return [o & 1 for o in rec.out]


@cocotb.test()
async def test_fault_uart_knobs(dut):
    """Bit time and stop bit length come out exactly as set: every bit of
    0x55 0x55 (all edges) lasts BIT, the stop bit STOP."""
    for bit, stop in ((104, 104), (99, 104), (110, 37), (104, 300)):
        tx = await uart_frames(dut, b"UU", bit, stop)
        first = next(k for k in edges(tx) if tx[k] == 0)     # the first start bit
        r = runs(tx, first)
        # each frame: start and b0..b7 alternate (9 runs of one bit), stop
        bits = [n for lv, n in r[:9]] + [n for lv, n in r[10:19]]
        assert bits == [bit] * 18, (bit, stop, r[:20])
        assert r[9] == (1, stop), (bit, stop, r[9])        # the first stop bit


def mid_bit_receiver(line, nominal):
    """A receiver that finds each start edge and samples every bit at its
    nominal centre (no re-sync inside a frame): bytes, or -1 for a frame
    it gets wrong (a stop bit read low)."""
    out, i = [], 1
    while i < len(line):
        if line[i - 1] == 1 and line[i] == 0:
            s = [line[min(i + nominal // 2 + k * nominal, len(line) - 1)] for k in range(10)]
            if s[0] != 0:
                i += 1
                continue
            v = sum(b << k for k, b in enumerate(s[1:9]))
            out.append(v if s[9] == 1 else -1)
            i += 9 * nominal + nominal // 2
        else:
            i += 1
    return out


@cocotb.test()
async def test_fault_uart_tolerance_sweep(dut):
    """Sweep the bit time around 104 against a mid-bit sampling receiver:
    it decodes 0x55 (every bit an edge) exactly while its stop-bit sample,
    9.5 nominal bits in, stays inside the stop bit and bit 7's inside bit 7,
    i.e. for 99..109 cycles (-4.8 % .. +4.8 %), and fails outside: the knob
    moves the edges by single cycles and the sweep finds the theoretical
    limits."""
    ok = set()
    for bit in range(94, 115):
        tx = await uart_frames(dut, b"UUU", bit, bit)
        if mid_bit_receiver(tx, 104) == [0x55] * 3:
            ok.add(bit)
    assert ok == set(range(99, 110)), sorted(ok)


async def i2c_scl(dut, ql, qh):
    """i2c_master writing to an absent device (address NACKed, then STOP):
    the SCL runs on the pads."""
    data = {0: 0x50, 1: 1, 2: 0xA5, 3: 0}
    await fw_boot(dut, "i2c_master.s", data=data, defines={"QL": ql, "QH": qh},
                  uio_in=0x60)
    bench = Bench(dut, pull=0x60)            # open drain: released = high
    await bench.until(lambda b: b.uo and b.uo[-1] & 0x80, 20000, "i2c_master done")
    return runs(bench.bits(6), edges(bench.bits(6))[0])


@cocotb.test()
async def test_fault_i2c_knobs(dut):
    """SCL high and low phases move by exactly 3 cycles per QH / QL count
    (the address byte's clocks 2..8, a run of identical bits)."""
    base = await i2c_scl(dut, 9, 9)
    hi0 = [n for lv, n in base[2:16] if lv == 1]
    lo0 = [n for lv, n in base[2:16] if lv == 0]
    for ql, qh in ((9, 3), (2, 9), (5, 14), (1, 1)):
        r = await i2c_scl(dut, ql, qh)
        hi = [n for lv, n in r[2:16] if lv == 1]
        lo = [n for lv, n in r[2:16] if lv == 0]
        assert hi == [h + 3 * (qh - 9) for h in hi0], (ql, qh, hi, hi0)
        assert lo == [n + 3 * (ql - 9) for n in lo0], (ql, qh, lo, lo0)


async def bridge_sck(dut, sh, sl):
    line = Wave(1 << 1, uart_wave([("byte", 1, 104, 1, 200), ("byte", 0xA5, 104, 1, 200)], pin=1),
                start=400)
    await fw_boot(dut, "bridge.s", defines={"SH": sh, "SL": sl}, uio_in=1 << 1)
    bench = Bench(dut)
    bench.add(line)
    await bench.run(line.end + 2000)
    sck = [(o >> 2) & 1 for o in bench.out]
    return runs(sck, edges(sck)[0])[:16]


@cocotb.test()
async def test_fault_spi_knobs(dut):
    """The bridge's SCK: high 4 + 4 SH cycles, low 10 + 4 SL (mode 0)."""
    for sh, sl in ((1, 1), (3, 1), (1, 5), (10, 2)):
        r = await bridge_sck(dut, sh, sl)
        hi = {n for lv, n in r if lv == 1}
        lo = {n for lv, n in r[:-1] if lv == 0}       # (the last low: idle)
        assert hi == {4 + 4 * sh} and lo == {10 + 4 * sl}, (sh, sl, r)
