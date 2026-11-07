# SPDX-License-Identifier: Apache-2.0
"""Record it, learn it, speak it, on the RTL: a transmitter the chip has never seen
is recorded by the logic analyser firmware, tools/msf_learn.py works out the protocol
from the dump, tools/msf_gen.py turns it into firmware the deadline checker certifies,
and the chip sends a message the transmitter never sent. An independent receiver
written from the protocol's specification reads the result, and so does sigrok.

The infrared timing is scaled down 20 times to keep the simulation short (sigrok is
told the rate is 20 times lower, so that it sees the real timing)."""
import os
import sys
import tempfile

import cocotb

from fw_tb import Bench, DONE, HERE, Recorder, fw_boot, save_trace
from msf_tb import uart_decode
from test_monitors import Wave

sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import msf_capture as mc  # noqa: E402
import msf_gen  # noqa: E402
import msf_learn as ml  # noqa: E402

SCALE = 20
CYCLES_US = 12 / SCALE             # cycles per microsecond of infrared time
PIN_IN, PIN_OUT = 3, 2


def us(t):
    return int(round(t * CYCLES_US))


def nec_levels(addr, cmd):
    """A NEC frame as a demodulated IR receiver gives it: idle high, marks low. From
    the specification: 9 ms and 4.5 ms leader, 562.5 us bits, LSB first, the address,
    its complement, the command, its complement, a stop mark."""
    lv = [0] * us(9000) + [1] * us(4500)
    word = addr | (addr ^ 0xFF) << 8 | cmd << 16 | (cmd ^ 0xFF) << 24
    for i in range(32):
        lv += [0] * us(562.5) + [1] * us(1687.5 if (word >> i) & 1 else 562.5)
    return lv + [0] * us(562.5)


def nec_read(levels):
    """The specification's receiver: marks and spaces against NEC's windows. Returns
    (addr, cmd) of the first frame, checking each length within 10%, or raises."""
    edges = [i for i in range(1, len(levels)) if levels[i] != levels[i - 1]]
    runs = [(levels[a], b - a) for a, b in zip(edges, edges[1:])]
    i = next(k for k, r in enumerate(runs) if r[0] == 0 and abs(r[1] - us(9000)) < us(900))

    def window(got, want, what):
        assert abs(got - us(want)) <= us(want) * 0.1 + 1, f"{what}: {got} cycles, want {us(want)}"

    window(runs[i][1], 9000, "leader mark")
    window(runs[i + 1][1], 4500, "leader space")
    word, k = 0, i + 2
    for b in range(32):
        window(runs[k][1], 562.5, f"bit {b} mark")
        long_ = runs[k + 1][1] > us(1125)
        window(runs[k + 1][1], 1687.5 if long_ else 562.5, f"bit {b} space")
        word |= int(long_) << b
        k += 2
    window(runs[k][1], 562.5, "stop mark")
    a, ai, c, ci = word & 0xFF, word >> 8 & 0xFF, word >> 16 & 0xFF, word >> 24
    assert a == ai ^ 0xFF and c == ci ^ 0xFF, f"{word:#x}"
    return a, c


async def record(dut, levels, pin, defines):
    """Run logic_analyzer.s against these levels; the dump as words."""
    await fw_boot(dut, "logic_analyzer.s", defines=defines, uio_in=1 << pin)
    bench = Bench(dut, pull=1 << pin)
    bench.add(Wave(1 << pin, [v << pin for v in levels], start=600))
    await bench.until(lambda b: b.uo and b.uo[-1] == DONE, 900000, "the dump")
    raw = uart_decode(bench.out, 12)
    assert -1 not in raw
    return mc.parse_dump(bytes(raw))


async def speak(dut, table):
    """Generate, certify, load and run. Returns the pin's samples."""
    text = msf_gen.generate(table)
    _, _, worst = msf_gen.certify(text)
    dut._log.info(f"generated firmware certified, worst slack {worst} cycles")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "learned.s")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        await fw_boot(dut, path)
        rec = Recorder(dut)
        await rec.until_uo(lambda v: v == DONE, 400000)
    return rec


@cocotb.test()
async def test_record_learn_speak_nec(dut):
    """Three buttons of a NEC remote, then a button it never sent."""
    gap = [1] * us(40000)
    addr = 0x10
    levels = [1] * us(1000)
    words = []
    for cmd in (0x45, 0x46, 0x47, 0x1C):
        levels += nec_levels(addr, cmd) + gap
        words.append(addr | (addr ^ 0xFF) << 8 | cmd << 16 | (cmd ^ 0xFF) << 24)
    div = 8                        # a word per change, or per 255 ticks without one
    defines = {"N": 8, "BASE": 512, "MODE": 0x4000, "DIV": div, "MASK": 1 << PIN_IN,
               "VAL": 0, "EDGE": 0x1000, "POST": 300, "BIT": 12}
    dump = await record(dut, levels, PIN_IN, defines)
    ev, end = mc.change_events(dump["words"], div)
    runs = ml.runs_of(ev, PIN_IN)
    m = ml.learn_runs(runs, mc.CLOCK_HZ)
    dut._log.info(ml.describe(m))
    assert m["model"] == "distance", m["model"]
    assert m["bits"] == 32 and m["words"][:4] == words[:len(m["words"][:4])], m["words"]
    assert any(c["rule"] == "not" and c["byte"] == 3 for c in m["checks"]), m["checks"]
    new_cmd = 0x77
    table = ml.to_table(m, send=[addr | (addr ^ 0xFF) << 8 | new_cmd << 16], pin=PIN_OUT)
    rec = await speak(dut, table)
    out = [(v >> PIN_OUT) & 1 for v in rec.out]
    assert nec_read(out) == (addr, new_cmd)
    tail = out + [1] * us(2000)
    save_trace("learned_nec", [v << PIN_OUT for v in tail], f"ir_nec:ir={PIN_OUT}:polarity=active-low",
               "ir_nec=addr:addr-inv:cmd:cmd-inv",
               ["Address: 0x10", "Address#: 0xEF", "Command: 0x77", "Command#: 0x88"],
               {"pin": PIN_OUT, "hold": us(1700)}, rate=12_000_000 // SCALE)


@cocotb.test()
async def test_learn_speak_manchester(dut):
    """A Manchester line, RC-5 like: 14 bits, the half bit from the recording. The chip
    sends a frame it never heard, and its bits come back from the wire."""
    half = 200

    def frame(w, n=14):
        lv = []
        for i in range(n):
            b = (w >> i) & 1
            lv += [1 - b] * half + [b] * half     # 0: high then low
        return lv
    # bit 0 always 1, so the first half is the idle (low) level
    frames = [(w << 1 | 1) & 0x3FFF for w in (0x0A5, 0x1FF, 0x0F0, 0x333)]
    runs_lv = [0] * 2000
    for w in frames:
        runs_lv += frame(w) + [0] * (half * 20)
    runs = []
    for v in runs_lv:
        if runs and runs[-1][0] == v:
            runs[-1] = (v, runs[-1][1] + 1)
        else:
            runs.append((v, 1))
    m = ml.learn_runs(runs, mc.CLOCK_HZ)
    assert m["model"] == "manchester" and m["bits"] == 14, (m["model"], m.get("bits"))
    new = 0x2AAB
    rec = await speak(dut, ml.to_table(m, send=[new], pin=PIN_OUT))
    out = [(v >> PIN_OUT) & 1 for v in rec.out]
    start = next(i for i, v in enumerate(out) if v) - half    # the first half is hidden
    bits = [out[start + (2 * i + 1) * half + half // 2] for i in range(14)]
    got = sum(b << i for i, b in enumerate(bits))
    assert got == new or got == new ^ 0x3FFF, (hex(got), hex(new))


@cocotb.test()
async def test_learn_speak_uart_and_sigrok(dut):
    """UART from a VCD-like recording: format and rate found, and new bytes sent, read by
    sigrok's own UART decoder."""
    bit = 104
    msg = b"hello there, 0123 UART"
    lv = [1] * 1000
    for c in msg:
        for x in [0] + [(c >> i) & 1 for i in range(8)] + [1]:
            lv += [x] * bit
        lv += [1] * 40
    runs = []
    for v in lv:
        if runs and runs[-1][0] == v:
            runs[-1] = (v, runs[-1][1] + 1)
        else:
            runs.append((v, 1))
    m = ml.learn_runs(runs, mc.CLOCK_HZ)
    assert m["model"] == "serial" and m["bit"] == bit and m["chars"] == list(msg), m
    new = b"new"
    rec = await speak(dut, ml.to_table(m, send=list(new), pin=PIN_OUT))
    out = [v & (1 << PIN_OUT) for v in rec.out]
    assert bytes(uart_decode(rec.out, bit, pin=PIN_OUT)) == new
    save_trace("learned_uart", out + [1 << PIN_OUT] * 200, f"uart:rx={PIN_OUT}:baudrate=115200",
               "uart=rx-data", [f"{c:02X}" for c in new], {"pin": PIN_OUT, "hold": bit})


@cocotb.test()
async def test_learned_carrier_generated(dut):
    """Bursts of a carrier under the marks: taken off when learning, put back when
    sending, on the cycle counter."""
    period, high, unit = 80, 26, 800
    active, idle = 0, 1
    runs = [(1, 6000)]

    def burst(n):
        out = []
        for _ in range(n):
            out += [(active, high), (idle, period - high)]
        return out

    for w in (0xA5, 0x3C, 0xF0, 0x0F, 0x96, 0x69):
        for i in range(8):
            runs += burst(unit // period)
            runs[-1] = (idle, period - high + (3 * unit if (w >> i) & 1 else unit))
        runs += burst(unit // period)
        runs[-1] = (idle, 20 * unit)
    m = ml.learn_runs(runs, mc.CLOCK_HZ)
    assert m["carrier"] and abs(m["carrier"]["period"] - period) <= 1, m["carrier"]
    assert m["model"] == "distance" and m["bits"] == 8, m["model"]
    assert m["words"] == [0xA5, 0x3C, 0xF0, 0x0F, 0x96, 0x69], m["words"]
    rec = await speak(dut, ml.to_table(m, send=[0x5A], pin=PIN_OUT))
    out = [(v >> PIN_OUT) & 1 for v in rec.out]
    falls = [i for i in range(1, len(out)) if out[i - 1] and not out[i]]
    gaps = {b - a for a, b in zip(falls, falls[1:]) if b - a < 2 * period}
    assert gaps == {period}, sorted(gaps)[:8]


@cocotb.test()
async def test_generator_refuses_what_it_cannot_keep(dut):
    """A symbol too short for the loop: msf_timing.py cannot prove it, so no firmware."""
    table = {"name": "t", "pin": 2, "idle": 1, "clock_hz": 12000000, "model": "distance",
             "carrier": None, "symbols": {"zero": [[0, 5], [1, 5]], "one": [[0, 5], [1, 15]]},
             "frames": [{"bits": 8, "word": 0xA5}]}
    try:
        msf_gen.certify(msf_gen.generate(table))
    except msf_gen.Refused as e:
        dut._log.info(f"refused: {e}")
    else:
        raise AssertionError("certified a 5-cycle phase")
