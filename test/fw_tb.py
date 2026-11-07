# SPDX-License-Identifier: Apache-2.0
"""Helpers for the firmware-level tests (firmware/*.s checked at the pins).

fw_boot   assemble a firmware file, preload it, reset the chip; if it uses
          WAIT_CMP, DeadlineWatch checks each issue against tools/msf_timing.py
Recorder  per-cycle samples of uio_out / uio_oe / uo_out
Bench     steps the chip one clock at a time against Python device models
          (the same lock-step idea as the FPGA C harness): each cycle it
          records the outputs, then every model sets the levels it drives,
          and the pads resolve as push-pull where the chip drives
          (uio_oe = 1) and as the models' levels elsewhere
events    decode the bus monitors' report stream into (code, data) pairs
"""
import json
import os
import sys

import cocotb
from cocotb.triggers import RisingEdge, ReadOnly, Timer

from assembler import assemble_file
from msf_tb import start_clock, reset, load_words, uart_decode

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import msf_timing  # noqa: E402

FW = os.path.join(HERE, "..", "firmware")
DONE = 0x55
# what the RTL showed for each deadline: `msf_timing.py --rtl-log` reads it
# (a run with its own MSF_BUILD keeps its own log, so that runs at once don't collide)
DEADLINE_LOG = (os.path.join(HERE, "sim_build", os.environ["MSF_BUILD"] + "_deadlines.json")
                if os.environ.get("MSF_BUILD") else os.path.join(HERE, "deadlines_seen.json"))
TRACES = os.path.join(HERE, "traces")   # for tools/sigrok_check.py
# set by tools/fw_mutants.py: run this firmware's planted-bug version instead
MUTANT = os.environ.get("MSF_FW_MUTANT")

_bg = []


def spawn(coro):
    """Background model task; cancelled by the next fw_boot."""
    t = cocotb.start_soon(coro)
    _bg.append(t)
    return t


_ranges = {}
_seen = {}
if os.path.exists(DEADLINE_LOG) and not MUTANT:     # runs of parts of the suite add up
    with open(DEADLINE_LOG, encoding="utf-8") as _f:
        _seen = json.load(_f)


def _define_key(defines):
    return ",".join(f"{k}={v}" for k, v in sorted((defines or {}).items()))


def _deadline_ranges(path, defines):
    """(threads, {pc: (lo, hi)}) from the static checker, cached. A firmware marked
    `; @slow-analysis` takes minutes per set of defines, so its tests check only that
    nothing is late (d >= 1, LATE flag); CI still certifies it (msf_timing.py --all)."""
    key = (path, _define_key(defines))
    if key not in _ranges:
        with open(path, encoding="utf-8") as f:
            if "@slow-analysis" in f.read():
                _ranges[key] = (0, {})
                return _ranges[key]
        a, _, _ = msf_timing.analyse(path, None, defines)
        _ranges[key] = (a.n, {pc: (dl["lo"], dl["hi"]) for pc, dl in a.deadlines.items()
                              if dl["seen"]})
    return _ranges[key]


class DeadlineWatch:
    """Every WAIT_CMP the RTL issues, checked against the range the static
    checker computed for it, with the test's real peer on the pins: d (target
    minus CNT at issue) must not be below the checker's bound, and no thread's
    LATE flag may rise. The upper bound holds in single-thread programs only: a
    program with threads is analysed as if they ran from the start.
    late_ok: the test is late on purpose. record: tools/fw_mutants.py's run,
    which writes what it saw instead of failing."""

    def __init__(self, dut, name, path, defines, late_ok=False, record=None):
        self.dut, self.name, self.late_ok, self.record = dut, name, late_ok, record
        self.n, self.ranges = _deadline_ranges(path, defines)
        self.key = _define_key(defines)
        self.log = _seen.setdefault(name, {}).setdefault(self.key, {})
        # (a mutant's run writes to its own file, and only what it saw there)
        if record is not None:
            self.log = {}
        self.flag_seen = False
        self.below = []
        self.task = spawn(self._run())

    def _fail(self, msg):
        if not self.late_ok and self.record is None:
            raise AssertionError(f"{self.name} [{self.key}]: {msg}")

    def _issue(self, pc, d):
        d = msf_timing.model_d(d)
        old = self.log.get(str(pc))
        new = [d, d] if old is None else [min(old[0], d), max(old[1], d)]
        if new != old:
            self.log[str(pc)] = new
            self._flush()
        lo, hi = self.ranges.get(pc, (1, None))
        if lo is not None and d < lo:
            self.below.append([pc, d, lo])
            self._flush()
            self._fail(f"WAIT_CMP at {pc:#05x}: d = {d}, the checker's bound is {lo}")
        if hi is not None and self.n == 1 and d > hi and self.record is None:
            self._fail(f"WAIT_CMP at {pc:#05x}: d = {d}, above the checker's {hi}")

    def _flush(self):
        if self.record is not None:
            with open(self.record, "w", encoding="utf-8") as f:
                json.dump({"seen": self.log, "below": self.below, "flag": self.flag_seen}, f)
        elif not self.late_ok:
            with open(DEADLINE_LOG, "w", encoding="utf-8") as f:
                json.dump(_seen, f, indent=0, sort_keys=True)

    async def _run(self):
        c = self.dut.user_project.u_ctrl
        self._flush()
        spawn(self._watch_flag(c))
        while True:
            await self.dut.wcmp_evt.value_change     # tb.v: a WAIT_CMP issued
            self._issue(int(c.dbg_pc.value), int(c.qa.value) - int(c.cnt.value))

    async def _watch_flag(self, c):
        while True:
            await c.late.value_change
            if int(c.late.value) and not self.flag_seen:
                self.flag_seen = True
                self._flush()
                self._fail("a LATE flag rose")


async def fw_boot(dut, name, data=None, defines=None, uio_in=0, late_ok=False):
    while _bg:
        _bg.pop().cancel()
    start_clock(dut)
    path, record = os.path.join(FW, name), None
    if MUTANT:
        with open(MUTANT, encoding="utf-8") as f:
            m = json.load(f)
        if m["firmware"] == name:
            path, record = m["source"], m["out"]
            defines = {**(defines or {}), **m["defines"]}
    words = assemble_file(path, base_addr=128, defines=defines)
    for a in range(1024):
        dut.user_project.u_sram.mem[a].value = 0
    load_words(dut, words, 128)
    for a, w in (data or {}).items():
        load_words(dut, [w], a)
    await reset(dut, ui_in=1, uio_in=uio_in)
    with open(path, encoding="utf-8") as f:
        if "WAIT_CMP" in f.read():
            DeadlineWatch(dut, name, path, defines, late_ok, record)
    return words


def save_trace(name, samples, decoders, annotations, expect, tooth, rate=12_000_000,
               sr_file=None):
    """One test's pins for tools/sigrok_check.py: samples is the pads, one byte a
    cycle. decoders and annotations are sigrok-cli's -P and -A; expect, the lines
    it must print, in order; tooth, {"pin", "hold"}: an edge on that pin held back
    by that many samples, which the decode must notice. sr_file: read this sigrok
    session in traces/ instead of samples."""
    os.makedirs(TRACES, exist_ok=True)
    if sr_file is None:
        with open(os.path.join(TRACES, name + ".bin"), "wb") as f:
            f.write(bytes(samples))
    with open(os.path.join(TRACES, name + ".json"), "w", encoding="utf-8") as f:
        json.dump({"decoders": decoders, "annotations": annotations, "expect": expect,
                   "tooth": tooth, "rate": rate, "sr_file": sr_file}, f, indent=1)


class Recorder:
    """Per-cycle samples of (uio_out, uio_oe, uo_out), and the uio pads."""

    last = None

    def __init__(self, dut):
        self.dut, self.out, self.oe, self.uo, self.pad = dut, [], [], [], []
        self.task = spawn(self._run())
        Recorder.last = self

    async def _run(self):
        while True:
            await RisingEdge(self.dut.clk)
            await ReadOnly()
            out, oe = int(self.dut.uio_out.value), int(self.dut.uio_oe.value)
            self.out.append(out)
            self.oe.append(oe)
            self.uo.append(int(self.dut.uo_out.value))
            self.pad.append(((out & oe) | (int(self.dut.uio_in.value) & ~oe)) & 0xFF)

    async def until_uo(self, pred, limit):
        for _ in range(limit):
            await RisingEdge(self.dut.clk)
            if self.uo and pred(self.uo[-1]):
                return
        raise AssertionError("timeout waiting for uo_out")


class Bench:
    """Lock-step pin bench. Cycle k: record the chip's outputs (out[k], oe[k],
    uo[k]), then each model's step(k, bench) returns the levels it drives as
    (mask, value); the pads become
        pad = chip value where uio_oe = 1, else the models' value, else `pull`
    and are written to uio_in (seen by the chip's synchronisers from the next
    edge). Open-drain lines (I2C): the chip only ever drives them low, and a
    model drives one (mask bit set, value 0) only while pulling it low, so
    with `pull` set the pad is low when either side pulls (wired AND)."""

    def __init__(self, dut, pull=0x00, models=()):
        self.dut, self.pull = dut, pull
        self.models = list(models)
        self.out, self.oe, self.uo, self.pad = [], [], [], []
        self.task = spawn(self._run())
        Bench.last = self

    def add(self, model):
        self.models.append(model)
        return model

    @property
    def cycle(self):
        return len(self.out) - 1

    async def _run(self):
        d = self.dut
        while True:
            await RisingEdge(d.clk)
            await ReadOnly()
            out, oe = int(d.uio_out.value), int(d.uio_oe.value)
            self.out.append(out)
            self.oe.append(oe)
            self.uo.append(int(d.uo_out.value))
            await Timer(1, unit="ns")
            k = len(self.out) - 1
            ext = self.pull
            for m in self.models:
                mask, val = m.step(k, self)
                ext = (ext & ~mask) | (val & mask)
            pad = ((out & oe) | (ext & ~oe)) & 0xFF
            self.pad.append(pad)
            d.uio_in.value = pad

    async def run(self, cycles):
        target = len(self.out) + cycles
        while len(self.out) < target:
            await RisingEdge(self.dut.clk)

    async def until(self, pred, limit, what="condition"):
        for _ in range(limit):
            await RisingEdge(self.dut.clk)
            if self.out and pred(self):
                return
        raise AssertionError(f"timeout waiting for {what}")

    def bits(self, pin, src="pad"):
        """Per-cycle levels of one uio pin (pads, or the chip's uio_out)."""
        return [(v >> pin) & 1 for v in getattr(self, src)]


def edges(levels, start=0):
    """Indices k where levels[k] != levels[k-1]."""
    return [k for k in range(max(1, start), len(levels)) if levels[k] != levels[k - 1]]


def events(samples, bit_cycles, pin=0):
    """Monitor report stream (two bytes per event: code, data) -> list of
    (chr(code), data) pairs, from per-cycle samples of the report pin."""
    by = uart_decode(samples, bit_cycles, pin=pin)
    assert -1 not in by, f"report stream framing error: {by}"
    assert len(by) % 2 == 0, f"odd number of report bytes: {by}"
    return [(chr(by[i]), by[i + 1]) for i in range(0, len(by), 2)]
