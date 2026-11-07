# SPDX-License-Identifier: Apache-2.0
"""Flight recorder (firmware/flight_*.s, flight.inc): a bus monitor that also
keeps the waveform around the first fault. The tests inject a fault of known
length with the monitors' own wave builders, and look for that length in the
ring the chip sends back: the pins as the capture engine saw them, read back at
the chip's report pin only."""
import os
import random
import sys

import cocotb

from fw_tb import Bench, HERE, fw_boot
from msf_tb import uart_decode
from test_monitors import (HMIN, I2cWave, LINE, LMIN, REPORT_BIT, SCKP, SCL, SpiWave, Wave,
                           check_i2c_events, mon_run, uart_wave)

sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import msf_capture as mc  # noqa: E402

FR_SIZE = 256
ARMED = 3000                       # cycles for the reporter to clear the ring and arm it


def split_report(raw):
    """The report bytes -> events before the dump, ring words, its 'E' flag, events
    after. A dump is ('D', 7), FR_SIZE words low byte first, then ('E', flag)."""
    before, after, words, flag, i = [], [], None, None, 0
    while i + 1 < len(raw):
        code, data = chr(raw[i]), raw[i + 1]
        i += 2
        if code == "D" and words is None:
            assert data == 7, data
            n = 2 * FR_SIZE
            body = raw[i:i + n]
            assert len(body) == n, f"a short dump: {len(body)} bytes"
            words = [body[k] | body[k + 1] << 8 for k in range(0, n, 2)]
            i += n
            assert chr(raw[i]) == "E", (i, raw[i])
            flag = raw[i + 1]
            i += 2
        elif words is None:
            before.append((code, data))
        else:
            after.append((code, data))
    return before, words, flag, after


async def flight(dut, name, wave, cycles, defines=None):
    """Run a flight firmware against a wave; (events, ring words, flag, later events)."""
    await fw_boot(dut, name, defines=defines, uio_in=wave.idle)
    bench = Bench(dut)
    bench.add(wave)
    await bench.run(wave.end + cycles)
    raw = uart_decode(bench.out, REPORT_BIT)
    assert -1 not in raw, "framing error in the report"
    return split_report(raw)


def pin_runs(words, pin):
    """The (cycles, level) runs of one pin in a ring, whole runs only."""
    ev, _ = mc.change_events(words)
    levels = []
    for t, p in ev:
        v = p >> pin & 1
        if not levels or levels[-1][1] != v:
            levels.append((t, v))
    return [(b[0] - a[0], a[1]) for a, b in zip(levels, levels[1:])]


def i2c_start(w):
    w.idle(ARMED)
    w.start()
    return w


def with_fault(want, kind, val):
    """I2cWave's list with the timing event where the monitor reports it: in front
    of the byte it was in."""
    out = []
    for c, d in want:
        if c == "A" and d == 0x5A:
            out.append((kind, val))
        out.append((c, d))
    return out


@cocotb.test()
async def test_flight_i2c_keeps_the_fault(dut):
    """A short SCL high and a short low, each in its own run: the events are the
    monitor's, and the ring holds that phase at its true length."""
    for kind, val in (("H", 30), ("L", 40)):
        w = i2c_start(I2cWave())
        w.byte_(0x50 << 1)
        w.byte_(0x5A, phases={3: {"low" if kind == "L" else "high": val}})
        w.byte_(0x33)
        w.stop()
        w.idle(200)
        before, words, flag, after = await flight(dut, "flight_i2c.s", w.wave(), 90000)
        assert words is not None, f"no dump: {before}"
        assert flag == 0, "the capture engine lost a word"
        check_i2c_events(before + after, with_fault(w.want, kind, val))
        level = 1 if kind == "H" else 0
        runs = pin_runs(words, SCL)
        assert (val, level) in runs, f"{val} cycles at {level} not in the ring: {sorted(set(runs))}"


@cocotb.test()
async def test_flight_i2c_silent_on_a_legal_bus(dut):
    """Random legal traffic: the same events as mon_i2c.s, and no dump."""
    rng = random.Random(3)
    w = i2c_start(I2cWave(tlow=LMIN, thigh=HMIN))
    for _ in range(4):
        v = rng.randrange(256)
        ph = {i: {"low": rng.randrange(LMIN, 3 * LMIN), "high": rng.randrange(HMIN, 3 * HMIN)}
              for i in range(9)}
        w.byte_(v, ack=rng.randrange(2), phases=ph)
    w.stop()
    before, words, _, _ = await flight(dut, "flight_i2c.s", w.wave(), 6000)
    assert words is None, "dumped on a legal bus"
    check_i2c_events(before, w.want)


@cocotb.test()
async def test_flight_watching_thread_is_mon_i2c(dut):
    """The watching thread's accuracy carries over: one short phase per byte, as in
    test_mon_i2c_single_faults, gives exactly the events mon_i2c.s gives, with the
    ring in between, since a thread's timing depends only on which threads run."""
    rng = random.Random(11)
    w = i2c_start(I2cWave())
    want = []
    for bit in range(1, 8):
        for kind, val in (("L", 30), ("L", 50), ("H", 12), ("H", 40)):
            v = rng.randrange(256)
            w.byte_(v, phases={bit: {"low" if kind == "L" else "high": val}})
            want += [(kind, val), ("A", v)]
    w.stop()
    got, _ = await mon_run(dut, "mon_i2c.s", w.wave(), extra=40000)
    check_i2c_events(got, [("S", 0)] + want + [("P", 0)])
    before, words, flag, after = await flight(dut, "flight_i2c.s", w.wave(), 150000)
    assert words is not None and flag == 0
    assert before + after == got, (before, after, got)


@cocotb.test()
async def test_flight_uart_glitch(dut):
    """A glitch of known length on a UART line: 'G', and the ring holds it."""
    items = [("byte", 0x41, 104, 1, 300), ("glitch", 20, 800), ("byte", 0x42, 104, 1, 300)]
    wave = Wave(1 << LINE, [1 << LINE] * ARMED + uart_wave(items, pin=LINE))
    before, words, flag, _ = await flight(dut, "flight_uart.s", wave, 90000)
    assert any(e[0] == "G" for e in before), before
    assert words is not None and flag == 0
    assert (20, 0) in pin_runs(words, LINE), sorted(set(pin_runs(words, LINE)))[:12]


@cocotb.test()
async def test_flight_spi_cut_short(dut):
    """CS rising part way through a byte: 'E' with the bits left over, and the ring
    holds the clock. A whole transfer before it raises nothing."""
    half = 30
    w = SpiWave(0)
    w.hold(ARMED)
    w.select()
    w.byte(0x81, 0x7E, half=half)
    w.sck = 0
    w.deselect()
    w.select()
    w.byte(0x5A, 0xA5, half=half)
    for b in (1, 0, 1):
        w.bit(b, 0, half)
    w.sck = 0
    w.deselect(left=3)
    before, words, flag, after = await flight(dut, "flight_spi.s", w.wave(), 90000,
                                              defines={"MODE": 0})
    assert before[-1] == ("E", 3), before
    assert ("E", 0) in before, "a whole transfer ends with 'E' 0, which is no fault"
    assert words is not None and flag == 0
    assert before + after == w.want, (before, after, w.want)
    assert (half, 1) in pin_runs(words, SCKP), sorted(set(pin_runs(words, SCKP)))[:12]
