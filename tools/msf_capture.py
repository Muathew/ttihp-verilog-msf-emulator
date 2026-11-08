#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Turn what the logic analyser firmware sends into files other tools open, and
turn a recording from any tool into firmware that plays it back.

Formats (isa.md §6.6, firmware/logic_analyzer.s, firmware/la_stream.s):
  dump     0xA55A, CNT, trigger index, status, then the buffer, oldest first:
           16-bit words, low byte first
  stream   0xA55B, CNT, then change-mode words as they are recorded; a word
           0x0000 means the sender fell behind and the rest is lost
  vcd/sr   a VCD, or a sigrok session: read and written, so PulseView and
           sigrok-cli can open a capture and a recording of theirs can be replayed

    python tools/msf_capture.py export cap.bin --mode change --div 1 -o cap.vcd
    python tools/msf_capture.py export cap.bin --mode sample -o cap.sr
    python tools/msf_capture.py replay cap.vcd --pins 0,1 -o replay.json

A trace is (events, rate): events = [(sample index, pins byte), ...], the pins at
that sample and until the next event.
"""
import argparse
import json
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "test"))

CLOCK_HZ = 12_000_000
# firmware/replay.s: a word is pins << 8 | ticks, and the shortest interval it
# can keep, in cycles (msf_timing.py certifies this with its @range annotation)
REPLAY_MIN_CYCLES = 16
REPLAY_FW = os.path.join(HERE, "..", "firmware", "replay.s")
REPLAY_WORDS = 512                     # SRAM 512..1023


# ---------------------------------------------------------------- the chip's words
def le_words(raw):
    return [raw[i] | raw[i + 1] << 8 for i in range(0, len(raw) - 1, 2)]


def parse_dump(raw):
    """Bytes from logic_analyzer.s -> dict(cnt, trigger, status, words)."""
    w = le_words(raw)
    if not w or w[0] != 0xA55A:
        raise ValueError("not a logic analyser dump: no 0xA55A header")
    return {"cnt": w[1], "trigger": w[2], "status": w[3], "words": w[4:]}


def parse_stream(raw):
    """Bytes from la_stream.s -> dict(cnt, words, overrun)."""
    w = le_words(raw)
    if not w or w[0] != 0xA55B:
        raise ValueError("not a logic analyser stream: no 0xA55B header")
    words, overrun = [], False
    for x in w[2:]:
        if x == 0:
            overrun = True
            break
        words.append(x)
    return {"cnt": w[1], "words": words, "overrun": overrun}


def change_events(words, div=1):
    """Change-mode words -> events. A word holds the pins and the ticks since the
    last word (never 0, so a 0 tick count is a word never written)."""
    t, out = 0, []
    for x in words:
        ticks = x & 0xFF
        if ticks == 0:
            continue
        t += ticks * div if out else 0
        pins = x >> 8
        if not out or out[-1][1] != pins:
            out.append((t, pins))
    end = t
    return out, end


def sample_events(words, div=1):
    """Sample-mode words (two samples a word, the earlier in the low byte) -> events."""
    t, out = 0, []
    for x in words:
        for pins in (x & 0xFF, x >> 8):
            if not out or out[-1][1] != pins:
                out.append((t, pins))
            t += div
    return out, t


# ---------------------------------------------------------------- VCD
_IDS = [chr(33 + i) for i in range(8)]


def write_vcd(path, events, end, rate=CLOCK_HZ, names=None):
    names = names or [f"pin{i}" for i in range(8)]
    # a cycle is rarely a whole number of any unit, so times are in ps, rounded
    ps = lambda t: (t * 1_000_000_000_000 + rate // 2) // rate
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write("$timescale 1 ps $end\n$scope module msfcore $end\n")
        for i in range(8):
            f.write(f"$var wire 1 {_IDS[i]} {names[i]} $end\n")
        f.write("$upscope $end\n$enddefinitions $end\n")
        last = None
        for t, pins in events:
            f.write(f"#{ps(t)}\n")
            for i in range(8):
                b = pins >> i & 1
                if last is None or (last >> i & 1) != b:
                    f.write(f"{b}{_IDS[i]}\n")
            last = pins
        f.write(f"#{ps(max(end, events[-1][0] if events else 0))}\n")


_VCD_UNIT = {"s": 10**12, "ms": 10**9, "us": 10**6, "ns": 10**3, "ps": 1, "fs": 1e-3}


def read_vcd(path, signals=None):
    """A VCD's 1-bit signals -> (events, rate, names). signals: names to keep,
    in pin order; default the first eight."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    head, _, body = text.partition("$enddefinitions")
    body = body.partition("$end")[2]
    m = re.search(r"\$timescale\s*(\d+)\s*(\w+)\s*\$end", head)
    if not m:
        raise ValueError("VCD without a $timescale")
    unit_ps = int(m.group(1)) * _VCD_UNIT[m.group(2)]
    decl = re.findall(r"\$var\s+\w+\s+(\d+)\s+(\S+)\s+(\S+)", head)
    ones = [(code, name) for size, code, name in decl if size == "1"]
    if signals:
        by_name = {name: code for code, name in ones}
        missing = [s for s in signals if s not in by_name]
        if missing:
            raise ValueError(f"not in the VCD: {missing}; it has {sorted(by_name)}")
        chosen = [(by_name[s], s) for s in signals]
    else:
        chosen = ones[:8]
    bit = {code: i for i, (code, _) in enumerate(chosen)}
    pins, t, events = 0, 0, []
    for tok in body.split():
        if tok[0] == "#":
            nt = int(tok[1:])
            if nt != t and (not events or events[-1][1] != pins):
                events.append((t, pins))
            t = nt
        elif tok[0] in "01xXzZ" and tok[1:] in bit:
            i = bit[tok[1:]]
            pins = pins | 1 << i if tok[0] == "1" else pins & ~(1 << i)
    if not events or events[-1][1] != pins:
        events.append((t, pins))
    rate = int(round(1e12 / unit_ps))
    return events, rate, [n for _, n in chosen]


# ---------------------------------------------------------------- sigrok session
def write_sr(path, events, end, rate=CLOCK_HZ, div=1, names=None):
    """A sigrok session stores samples: one byte each, every div samples."""
    names = names or [str(i) for i in range(8)]   # as sigrok's binary input names them
    n = max(end, events[-1][0] + 1 if events else 1) // div
    data = bytearray(n)
    for (t, pins), nxt in zip(events, events[1:] + [(n * div, 0)]):
        lo, hi = t // div, min(nxt[0] // div, n)
        data[lo:hi] = bytes([pins]) * max(0, hi - lo)
    meta = ["[global]", "sigrok version=0.5.2", "", "[device 1]", "capturefile=logic-1",
            f"total probes=8", f"samplerate={rate // div} Hz", "total analog=0"]
    meta += [f"probe{i + 1}={names[i]}" for i in range(8)]
    meta += ["unitsize=1", ""]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("version", "2")
        z.writestr("metadata", "\n".join(meta))
        z.writestr("logic-1-1", bytes(data))


_RATE = re.compile(r"samplerate\s*=\s*([\d.]+)\s*([kKmMgG]?)\s*Hz")     # old sessions have spaces


def read_sr(path):
    """A sigrok session -> (events, rate, names). Channels past the first eight
    are dropped."""
    with zipfile.ZipFile(path) as z:
        meta = z.read("metadata").decode("utf-8", "replace")
        m = _RATE.search(meta)
        if not m:
            raise ValueError("sigrok session without a samplerate")
        rate = int(float(m.group(1)) * {"": 1, "k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6,
                                        "g": 1e9, "G": 1e9}[m.group(2)])
        unit = int((re.search(r"unitsize\s*=\s*(\d+)", meta) or [0, "1"])[1])
        names = re.findall(r"probe\d+\s*=\s*(\S+)", meta)[:8]
        chunks = sorted((n for n in z.namelist() if n.startswith("logic-1")),
                        key=lambda n: int(n.rsplit("-", 1)[1]) if n.count("-") > 1 else 0)
        data = b"".join(z.read(n) for n in chunks)
    events, last = [], None
    for i in range(0, len(data), unit):
        v = data[i]
        if v != last:
            events.append((i // unit, v))
            last = v
    return events, rate, names


def read_trace(path, signals=None):
    """A capture file (.vcd or .sr) -> (events, rate, names)."""
    if path.lower().endswith(".sr"):
        return read_sr(path)
    return read_vcd(path, signals)


# ---------------------------------------------------------------- replay
def retime(events, rate, clock_hz=CLOCK_HZ):
    """Events in samples at `rate` -> events in cycles at clock_hz."""
    if rate == clock_hz:
        return list(events)
    return [((t * clock_hz + rate // 2) // rate, p) for t, p in events]


def replay_words(events, keep=None, max_shift=0, report=None):
    """Events in cycles -> firmware/replay.s's table: one word pins << 8 | ticks,
    a tick being a cycle; intervals over 255 become several words. keep: pins that
    matter, a mask. A change closer to the last than the firmware can keep moves
    later by up to max_shift cycles; past that it is refused, and the error says
    where. report: a dict that gets the worst shift used."""
    keep = 0xFF if keep is None else keep
    merged, last, worst = [], None, 0
    for t, p in events:
        p &= keep
        if p == last:
            continue
        if merged:
            earliest = merged[-1][0] + REPLAY_MIN_CYCLES
            if t < earliest:
                if earliest - t > max_shift:
                    raise ValueError(f"at cycle {t}: {t - merged[-1][0]} cycles after the last "
                                     f"change; replay.s needs at least {REPLAY_MIN_CYCLES}")
                worst = max(worst, earliest - t)
                t = earliest
        merged.append((t, p))
        last = p
    if not merged:
        raise ValueError("nothing to replay")
    if report is not None:
        report["worst_shift"] = worst
    words = [(merged[0][1] << 8) | 1]
    for (t0, p0), (t1, p1) in zip(merged, merged[1:]):
        gap = t1 - t0
        while gap > 255:
            step = 255 if gap - 255 >= REPLAY_MIN_CYCLES else 255 - REPLAY_MIN_CYCLES
            words.append((p0 << 8) | step)
            gap -= step
        words.append((p1 << 8) | gap)
    if len(words) > REPLAY_WORDS - 1:
        raise ValueError(f"{len(words)} words; the table holds {REPLAY_WORDS - 1}")
    return words + [0]                 # a word with 0 ticks ends the table


def replay_image(events, rate, keep=None, clock_hz=CLOCK_HZ, max_shift=0):
    """Everything fw_boot or msf_boot.py needs: the table, for SRAM 512 on, and the
    pins to drive."""
    ev = retime(events, rate, clock_hz)
    keep = 0xFF if keep is None else keep
    rep = {}
    words = replay_words(ev, keep, max_shift, rep)
    return {"firmware": "replay.s", "defines": {"OEMASK": keep, "START": 2000},
            "table_base": 512, "table": words, "worst_shift": rep["worst_shift"]}


# ---------------------------------------------------------------- command line
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="a dump or stream from the chip -> .vcd or .sr")
    e.add_argument("input", help="raw bytes as received")
    e.add_argument("--mode", choices=("sample", "change"), default="sample")
    e.add_argument("--div", type=int, default=1, help="the capture's DIV")
    e.add_argument("--clock", type=int, default=CLOCK_HZ)
    e.add_argument("--stream", action="store_true", help="la_stream.s, not a dump")
    e.add_argument("-o", required=True)
    r = sub.add_parser("replay", help="a .vcd or .sr -> replay.s's table")
    r.add_argument("input")
    r.add_argument("--pins", help="comma list of pins to drive, default all")
    r.add_argument("--signals", help="VCD signals to use, comma list, in pin order")
    r.add_argument("--clock", type=int, default=CLOCK_HZ)
    r.add_argument("--max-shift", type=int, default=0,
                   help="cycles a change may move later if it is closer than the firmware can keep")
    r.add_argument("-o", required=True)
    a = ap.parse_args()
    if a.cmd == "export":
        with open(a.input, "rb") as f:
            raw = f.read()
        words = parse_stream(raw)["words"] if a.stream else parse_dump(raw)["words"]
        conv = change_events if (a.mode == "change" or a.stream) else sample_events
        ev, end = conv(words, a.div)
        if a.o.lower().endswith(".sr"):
            write_sr(a.o, ev, end, a.clock)
        else:
            write_vcd(a.o, ev, end, a.clock)
        print(f"{a.o}: {len(ev)} changes over {end} cycles")
    else:
        ev, rate, names = read_trace(a.input, a.signals.split(",") if a.signals else None)
        keep = sum(1 << int(p) for p in a.pins.split(",")) if a.pins else None
        img = replay_image(ev, rate, keep, a.clock, a.max_shift)
        with open(a.o, "w", encoding="utf-8") as f:
            json.dump(img, f, indent=1)
        print(f"{a.o}: {len(img['table'])} words, {len(ev)} changes, worst shift "
              f"{img['worst_shift']} cycles; pins {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
