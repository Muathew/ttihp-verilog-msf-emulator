# SPDX-License-Identifier: Apache-2.0
"""The logic analyser's output reaching other tools (tools/msf_capture.py), and
a recording played back (firmware/la_stream.s, firmware/replay.s)."""
import os
import sys
import tempfile

import cocotb
from cocotb.triggers import ClockCycles

from fw_tb import Bench, DONE, HERE, Recorder, fw_boot, save_trace, spawn
from msf_tb import sram, uart_decode
from test_capture import CS_DONE, CS_TRIG, cap_ref, drive
from test_firmware import I2cSlave, i2c_data

sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import msf_capture as mc  # noqa: E402

LOST = 0xEE


def bytes_on_pin(rec, bit=12):
    raw = uart_decode(rec.out, bit)
    assert -1 not in raw, "framing error on the analyser's UART"
    return bytes(raw)


def events_of(levels):
    """Per-cycle samples -> events."""
    out = []
    for t, v in enumerate(levels):
        if not out or out[-1][1] != v:
            out.append((t, v))
    return out


@cocotb.test()
async def test_export_dump_round_trip(dut):
    """A dump through msf_capture, into a VCD and a sigrok session and back, gives
    the samples the pins carried."""
    defines = {"DIV": 2, "MASK": 0xF0, "VAL": 0x30, "EDGE": 0x1000, "N": 4,
               "BASE": 512, "POST": 9, "BIT": 12}
    f = lambda t: t >> 2
    drv = cocotb.start_soon(drive(dut, f))
    await fw_boot(dut, "logic_analyzer.s", defines=defines)
    rec = Recorder(dut)
    await rec.until_uo(lambda v: v == DONE, 60000)
    drv.cancel()
    dump = mc.parse_dump(bytes_on_pin(rec))
    assert dump["status"] & (CS_TRIG | CS_DONE) == CS_TRIG | CS_DONE
    ev, end = mc.sample_events([w for w in dump["words"]], div=1)
    samples = [f(dump["cnt"] + 2 * k) & 0xFF for k in range(4096)]
    words, _ = cap_ref(samples, False, 0xF0, 0x30, True, defines["POST"])
    size = len(dump["words"])
    want_samples = [b for w in ([0] * size + words)[-size:] for b in (w & 0xFF, w >> 8)]
    got_samples = []
    for (t0, p), (t1, _) in zip(ev, ev[1:] + [(end, 0)]):
        got_samples += [p] * (t1 - t0)
    assert got_samples == want_samples
    with tempfile.TemporaryDirectory() as tmp:
        vcd, sr = os.path.join(tmp, "c.vcd"), os.path.join(tmp, "c.sr")
        mc.write_vcd(vcd, ev, end)
        mc.write_sr(sr, ev, end)
        ev_vcd, rate_vcd, _ = mc.read_trace(vcd)
        ev_sr, rate_sr, _ = mc.read_trace(sr)
    assert rate_sr == mc.CLOCK_HZ
    assert mc.retime(ev_vcd, rate_vcd) == ev, "VCD round trip"
    assert ev_sr == ev, "sigrok session round trip"


@cocotb.test()
async def test_stream_keeps_up_past_the_ring(dut):
    """la_stream.s sends far more words than its ring holds, in order, with every
    change on the cycle the pins made it (a square wave of changing period)."""
    bit = 12
    size = 2 << 5                          # a ring of 64 words
    defines = {"N": 5, "BIT": bit}
    period = 250                           # a word each: the 240 cycles it takes, and a bit
    f = lambda t: (t // period) & 0xFF
    drv = cocotb.start_soon(drive(dut, f))
    await fw_boot(dut, "la_stream.s", defines=defines, uio_in=0)
    rec = Recorder(dut)
    await ClockCycles(dut.clk, 60000)
    drv.cancel()
    assert int(dut.uo_out.value) != LOST
    s = mc.parse_stream(bytes_on_pin(rec, bit))
    assert not s["overrun"]
    words = s["words"]
    assert len(words) > 2 * size, f"only {len(words)} words"
    ev, _ = mc.change_events(words)
    changes = [e[0] for e in ev]
    gaps = {b - a for a, b in zip(changes[1:], changes[2:])}
    assert gaps == {period}, sorted(gaps)[:6]
    pins = [e[1] for e in ev]
    assert all(((b - a) & 0xFF) == 1 for a, b in zip(pins[1:], pins[2:])), "a change was lost"


@cocotb.test()
async def test_stream_says_when_it_falls_behind(dut):
    """Changes faster than the UART can carry: the stream ends with the word
    0x0000 and uo_out = LOST, and everything before it is true."""
    bit = 12
    defines = {"N": 4, "BIT": bit}
    f = lambda t: (t // 16) & 0xFF
    drv = cocotb.start_soon(drive(dut, f))
    await fw_boot(dut, "la_stream.s", defines=defines, uio_in=0)
    rec = Recorder(dut)
    await rec.until_uo(lambda v: v == LOST, 40000)
    await ClockCycles(dut.clk, 100)
    drv.cancel()
    s = mc.parse_stream(bytes_on_pin(rec, bit))
    assert s["overrun"]
    pins = [w >> 8 for w in s["words"]]
    assert all(((b - a) & 0xFF) == 1 for a, b in zip(pins[1:], pins[2:])), "a word was wrong"


async def run_replay(dut, events, rate=mc.CLOCK_HZ, keep=None, table=None, cycles=None,
                     max_shift=0):
    img = mc.replay_image(events, rate, keep, max_shift=max_shift) if table is None else \
        {"table": table, "defines": {"OEMASK": 0xFF, "START": 2000}, "worst_shift": 0}
    data = {512 + i: w for i, w in enumerate(img["table"])}
    await fw_boot(dut, "replay.s", data=data, defines=img["defines"],
                  late_ok=table is not None)
    rec = Recorder(dut)
    await rec.until_uo(lambda v: v in (DONE, 0xEE), cycles or 2000000)
    rec.worst_shift = img["worst_shift"]
    return rec


@cocotb.test()
async def test_replay_i2c_edge_for_edge(dut):
    """An I2C transaction recorded at the pads, replayed: every change comes back
    on its cycle, except those the loop cannot keep (SDA moving just after SCL):
    those move later by at most the worst shift the tool reports, and nothing
    drifts."""
    await fw_boot(dut, "i2c_master.s", data=i2c_data(0x50, [0x12, 0xA5]), uio_in=0x60)
    I2cSlave(dut, 0x50)
    rec0 = Recorder(dut)
    await rec0.until_uo(lambda v: v & 0x80, 200000)
    await ClockCycles(dut.clk, 50)
    pad = rec0.pad[:]
    keep = 0x60
    want = mc.retime(events_of([p & keep for p in pad]), mc.CLOCK_HZ)
    rec = await run_replay(dut, want, keep=keep, max_shift=mc.REPLAY_MIN_CYCLES)
    assert rec.uo[-1] == DONE, f"uo {rec.uo[-1]:#x}"
    got = events_of([v & keep for v in rec.out])
    # the first event of each is the idle level; compare the changes after it
    a = want[1:]
    b = got[-len(a):]
    assert [p for _, p in a] == [p for _, p in b], "the levels differ"
    t0a, t0b = a[0][0], b[0][0]
    errs = [(tb - t0b) - (ta - t0a) for (ta, _), (tb, _) in zip(a, b)]
    assert all(0 <= e <= rec.worst_shift for e in errs), (min(errs), max(errs), rec.worst_shift)
    dut._log.info(f"replay of {len(a)} changes: worst shift {rec.worst_shift} cycles")


@cocotb.test()
async def test_replay_what_the_chip_recorded(dut):
    """Record on the chip in change mode, and play the buffer back as it stands:
    the two use the same word format, so no host has to convert it."""
    period = 40
    f = lambda t: (t // period) & 0x0F
    defines = {"N": 5, "BASE": 512, "MODE": 0x4000, "MASK": 0, "VAL": 0, "POST": 40, "BIT": 12}
    drv = cocotb.start_soon(drive(dut, f))
    await fw_boot(dut, "logic_analyzer.s", defines=defines)
    rec0 = Recorder(dut)
    await rec0.until_uo(lambda v: v == DONE, 60000)
    drv.cancel()
    words = [sram(dut, 512 + i) for i in range(41)]
    assert all(w & 0xFF for w in words)
    table = words[:40] + [0]
    rec = await run_replay(dut, [], table=table, cycles=40000)
    assert rec.uo[-1] == DONE, f"uo {rec.uo[-1]:#x}"
    ev = events_of([v & 0x0F for v in rec.out])
    gaps = {b[0] - a[0] for a, b in zip(ev[2:], ev[3:])}
    assert gaps == {period}, sorted(gaps)
    assert all(((b[1] - a[1]) & 0x0F) == 1 for a, b in zip(ev[2:], ev[3:]))


@cocotb.test()
async def test_replay_reports_what_it_could_not_keep(dut):
    """A table the checker's promise does not cover (an interval too short for the
    loop) ends with the LATE report, not with a silent wrong waveform."""
    table = [0xAA00 | 1, 0x5500 | 200, 0xAA00 | 3, 0x5500 | 200, 0]
    rec = await run_replay(dut, [], table=table, cycles=20000)
    assert rec.uo[-1] == 0xEE, f"uo {rec.uo[-1]:#x}"


@cocotb.test()
async def test_replay_tool_refuses_too_dense(dut):
    """msf_capture refuses what the firmware cannot keep up with, and says where;
    long gaps become several words, and each interval stays in the promised range."""
    try:
        mc.replay_words([(0, 1), (100, 2), (110, 3)])
    except ValueError as e:
        assert "110" in str(e)
    else:
        raise AssertionError("a 10-cycle interval was accepted")
    w = mc.replay_words([(0, 1), (100000, 2)])
    ticks = [x & 0xFF for x in w[1:-1]]
    assert sum(ticks) == 100000 and all(mc.REPLAY_MIN_CYCLES <= t <= 255 for t in ticks)
    assert w[-1] == 0


@cocotb.test()
async def test_capture_opens_in_sigrok(dut):
    """UART bytes recorded by the chip, sent to the host, written as a sigrok session;
    tools/sigrok_check.py then reads that file with sigrok's own UART decoder."""
    msg = b"msf"
    bit = 104
    wave = []
    for b in msg:
        for level in [0] + [(b >> i) & 1 for i in range(8)] + [1]:
            wave += [level << 3] * bit
        wave += [1 << 3] * bit
    from test_monitors import Wave
    defines = {"N": 6, "BASE": 512, "MODE": 0x4000, "SRC": 0, "MASK": 0x08, "VAL": 0,
               "EDGE": 0x1000, "POST": 60, "BIT": 12}
    await fw_boot(dut, "logic_analyzer.s", defines=defines, uio_in=1 << 3)
    bench = Bench(dut, pull=1 << 3)
    bench.add(Wave(1 << 3, wave, start=800))
    await bench.until(lambda b: b.uo and b.uo[-1] == DONE, 120000, "the dump")
    raw = uart_decode(bench.out, 12)
    assert -1 not in raw
    dump = mc.parse_dump(bytes(raw))
    ev, end = mc.change_events(dump["words"])
    os.makedirs(os.path.join(HERE, "traces"), exist_ok=True)
    sr = os.path.join(HERE, "traces", "la_capture.sr")
    mc.write_sr(sr, ev, end + 400)
    save_trace("la_capture", [], "uart:rx=3:baudrate=115200", "uart=rx-data",
               [f"{b:02X}" for b in msg], {"pin": 3, "hold": bit}, sr_file="la_capture.sr")
