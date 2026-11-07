#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Learn a protocol from a recording of one wire, so the chip can speak it.

Point the logic analyser at a transmitter nobody documented: this works out how
its bits are coded and what its frames hold, from the timing alone, and writes a
table that tools/msf_gen.py turns into firmware. Then the chip can send a message
the transmitter never sent. The method (try several line codes, keep the one that
writes the recording down in the fewest bits, refuse what fits none) is the one
Escapement's esclearn and openPIO's learn.py use; this is a separate implementation.

    python tools/msf_learn.py capture.sr --pin 3
    python tools/msf_learn.py capture.vcd --signal ir -o remote.json
    python tools/msf_learn.py capture.sr --pin 3 --send 10ff00 -o remote.json

Line codes it knows, all on one wire and self-timed:
  pulse distance  marks alike, two lengths of space (NEC)
  pulse length    spaces alike, two lengths of mark (Sony SIRC)
  pulse width     two symbols of equal period (PT2262, WS2812)
  Manchester      every bit a transition in its middle (RC-5, DALI)
  serial          UART framing: start bit, 5-9 data bits, parity, stop bits
Also: a carrier under the marks (infrared) is taken off first and remembered; check
bytes (a complement of another byte, the XOR, sum or negated sum of those before)
and CRCs (polynomial and bit order, computed from the frames) are found and
recomputed for a new message, when enough frames vary to show them.

What it will not do: guess. Noise, square waves, servo pulses and other signals with
structure but no data are refused, with the reason. It does not do clocked buses
(SPI, I2C) or conversations, and a transmitter's timing is only what the recording
shows. Sending is tools/msf_gen.py's job, and msf_timing.py has the last word on
whether the chip can keep up.
"""
import argparse
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import msf_capture  # noqa: E402

MIN_SAMPLES = 4          # a bit or half-bit must span at least this many samples
GAP_RATIO = 4.0          # frames end at a space this many times longer than others
CLUSTER_RATIO = 1.3      # lengths further apart than this are different lengths
PARAM_BITS = 16          # what naming one length costs, for the description length


class Refused(Exception):
    """The recording does not fit: the message says why."""


# ---------------------------------------------------------------- runs and lengths
def runs_of(events, pin, end=None):
    """Events (sample, pins) -> [(level, samples)] for one pin, whole runs only."""
    edges = []
    for t, pins in events:
        v = pins >> pin & 1
        if not edges or edges[-1][1] != v:
            edges.append((t, v))
    out = [(a[1], b[0] - a[0]) for a, b in zip(edges, edges[1:])]
    if end is not None and edges and end > edges[-1][0]:
        out.append((edges[-1][1], end - edges[-1][0]))
    return out


def clusters(values):
    """Sorted lengths -> [(min, max, median, count)], split where the next length is
    more than CLUSTER_RATIO (and more than 2 samples) above the one before."""
    vs = sorted(values)
    if not vs:
        return []
    groups, cur = [], [vs[0]]
    for v in vs[1:]:
        if v > cur[-1] * CLUSTER_RATIO and v > cur[-1] + 2:
            groups.append(cur)
            cur = []
        cur.append(v)
    groups.append(cur)
    return [(g[0], g[-1], statistics.median(g), len(g)) for g in groups]


def tight(cl):
    """Every length in these clusters is one length, give or take jitter."""
    return all(c[1] <= c[0] * 1.7 + 2 for c in cl)


def near(v, target, tol=0.28):
    return abs(v - target) <= max(2, target * tol)


def strip_carrier(runs):
    """Bursts of short regular pulses are a carrier: each becomes one mark. Returns
    (runs, carrier) with carrier = {"period", "high"} or None. A carrier means every
    mark on the line is such a burst, with spaces between bursts far longer than a
    period: a pulse code's run of equal symbols is regular too, but it ends in a mark
    of its own, or it has no long space to stand between."""
    if len(runs) < 16:
        return runs, None
    idle = idle_level(runs)
    periods = sorted(a[1] + b[1] for a, b in zip(runs, runs[1:]))
    period = periods[len(periods) // 2]
    if period < 2:
        return runs, None
    out, highs, i = [], [], 0
    while i < len(runs):
        if runs[i][0] == idle:
            if out and out[-1][0] == idle:
                out[-1] = (idle, out[-1][1] + runs[i][1])
            else:
                out.append(runs[i])
            i += 1
            continue
        j = i
        while (j + 2 < len(runs) and near(runs[j][1] + runs[j + 1][1], period, 0.2)
               and near(runs[j + 1][1] + runs[j + 2][1], period, 0.2)):
            j += 2
        if (j - i) // 2 < 8:
            return runs, None          # a mark that is no burst: there is no carrier
        highs += [r[1] for r in runs[i:j + 1:2]]
        out.append((runs[i][0], sum(r[1] for r in runs[i:j + 1])))
        i = j + 1
    spaces = [d for lv, d in out if lv == idle]
    if not spaces or min(spaces) < 4 * period:
        return runs, None
    return out, {"period": period, "high": statistics.median(highs)}


def idle_level(runs):
    """The level of the longest runs: where the line rests."""
    return max(runs, key=lambda r: r[1])[0]


def split_frames(runs, idle, gap):
    """Runs -> frames: lists of runs between idle runs of at least `gap` samples. A
    frame starts with the first run away from idle."""
    frames, cur = [], []
    for lv, d in runs:
        if lv == idle and d >= gap:
            if cur:
                frames.append(cur)
            cur = []
        elif cur or lv != idle:
            cur.append((lv, d))
    if cur:
        frames.append(cur)
    return frames


def gap_candidates(runs, idle):
    """Lengths of idle runs that could end a frame: at each jump of GAP_RATIO among the
    sorted idle runs, the longer side. The recording's first and last runs don't count:
    how long the line sat idle before and after says nothing about the protocol."""
    spaces = sorted(d for lv, d in runs[1:-1] if lv == idle)
    # the threshold lies between the two sides: a gap's length varies with how the
    # frames around it end, and the last run may be shorter than the others
    out = [int((a * b) ** 0.5) for a, b in zip(spaces, spaces[1:]) if b >= a * GAP_RATIO]
    return sorted(set(out))[:6]


def description_cost(n_params, payload_bits, unread_runs):
    """Bits to write the recording down with a model. A run the model could not read
    costs 16 bits."""
    return n_params * PARAM_BITS + payload_bits + 16 * unread_runs


# ---------------------------------------------------------------- pulse codes
def pulse_model(frames, active):
    """Try a two-symbol pulse code. Returns a dict, or None."""
    leader, trailer = None, None
    pairs_by_frame = []
    for fr in frames:
        if fr[0][0] != active:
            return None
        pairs = [(fr[i][1], fr[i + 1][1] if i + 1 < len(fr) else None)
                 for i in range(0, len(fr), 2)]
        pairs_by_frame.append(pairs)
    # the frame's common length is the data; other shapes (repeat codes) are noted
    lens = [len(p) for p in pairs_by_frame]
    common = max(set(lens), key=lens.count)
    good = [p for p in pairs_by_frame if len(p) == common]
    others = [p for p in pairs_by_frame if len(p) != common]
    if common < 5:
        return None
    # the marks and spaces of the middle of the frames say what the symbols are
    body = [q for p in good for q in p[1:-1]]
    if len(body) < 8:
        return None
    mids_m, mids_s = clusters([q[0] for q in body]), clusters([q[1] for q in body])
    first = [p[0] for p in good]
    has_leader = (first[0][0] > mids_m[-1][1] * 1.5 or
                  (first[0][1] is not None and first[0][1] > mids_s[-1][1] * 1.5))
    if has_leader:
        leader = (statistics.median(f[0] for f in first), statistics.median(f[1] for f in first))
    data = [p[1:] if has_leader else p for p in good]
    last = [p[-1] for p in data]
    all_pairs = [q for p in data for q in p if q[1] is not None]
    has_stop = all(q[1] is None for q in last) and (len(mids_s) == 2 or
                                                    near(statistics.median(q[0] for q in last),
                                                         mids_m[0][2]))
    marks, spaces = clusters([q[0] for q in all_pairs]), clusters([q[1] for q in all_pairs])
    if len(marks) > 2 or len(spaces) > 2 or (len(marks) == 1 and len(spaces) == 1):
        return None
    if not (tight(marks) and tight(spaces)):
        return None                    # a chain of near lengths is a range, not a symbol
    if len(marks) == 2 and len(spaces) == 2:
        # pulse width: the period is constant, and a pair is short-long or long-short
        kind = "width"
        ms, ml, ss, sl = marks[0][2], marks[1][2], spaces[0][2], spaces[1][2]
        zero, one = (ms, sl), (ml, ss)
        if not near(ms + sl, ml + ss, 0.2):
            return None
    elif len(spaces) == 2:
        kind = "distance"
        zero, one = (marks[0][2], spaces[0][2]), (marks[0][2], spaces[1][2])
    else:
        kind = "length"
        zero, one = (marks[0][2], spaces[0][2]), (marks[1][2], spaces[0][2])
    # the two lengths must be two, not a cut through a continuous range
    for cl in (marks, spaces):
        if len(cl) == 2 and cl[1][0] < cl[0][1] * 1.25:
            return None
    res = {"model": kind, "zero": zero, "one": one, "leader": leader, "others": len(others)}

    def bit(q):
        d0 = abs(q[0] - zero[0]) + abs(q[1] - zero[1])
        d1 = abs(q[0] - one[0]) + abs(q[1] - one[1])
        return 1 if d1 < d0 else 0

    # every pair must be one of the two symbols (Manchester's runs fall into two
    # lengths as well, but pair up in all four ways)
    for q in all_pairs:
        if not any(near(q[0], s[0]) and near(q[1], s[1]) for s in (zero, one)):
            return None
    words, nbits = [], None
    for p in data:
        bits = []
        for k, q in enumerate(p):
            if q[1] is None:
                if has_stop:
                    continue
                # the frame's last bit: its space is hidden in the gap, so its mark tells
                if kind == "distance":
                    return None
                q = (q[0], zero[1] if near(q[0], zero[0]) else one[1])
            bits.append(bit(q))
        words.append(sum(b << i for i, b in enumerate(bits)))
        nbits = len(bits)
    if nbits is None or nbits < 4:
        return None
    if len(set(words)) < 1 or not any(0 < bin(w).count("1") < nbits for w in words):
        return None                    # no frame mixes the symbols: no data shown
    res["trailer"] = statistics.median(q[0] for q in last) if has_stop else None
    res["bits"], res["words"], res["others_shapes"] = nbits, words, sorted({len(p) for p in others})
    n_params = 4 + (2 if leader else 0) + (1 if has_stop else 0) + 1
    res["cost"] = description_cost(n_params, nbits * len(words),
                                   sum(2 * len(p) for p in others) if others else 0)
    return res


# ---------------------------------------------------------------- Manchester
def manchester_model(frames, idle):
    all_runs = [d for fr in frames for _, d in fr]
    cl = clusters(all_runs)
    if len(cl) not in (1, 2) or not tight(cl):
        return None
    half = cl[0][2]
    if half < MIN_SAMPLES:
        return None
    if len(cl) == 2 and not near(cl[1][2], 2 * half, 0.3):
        return None
    out = []
    for fr in frames:
        levels = []
        for lv, d in fr:
            n = round(d / half)
            if n not in (1, 2):
                return None
            levels += [lv] * n
        # the idle level hides the first half of a frame that starts with one
        for prefix in ([], [idle]):
            seq = prefix + levels
            if len(seq) % 2:
                seq = seq + [idle]
            pairs = [(seq[i], seq[i + 1]) for i in range(0, len(seq), 2)]
            if all(a != b for a, b in pairs):
                out.append([b for _, b in pairs])     # the level after the transition
                break
        else:
            return None
    if not out:
        return None
    n = max(set(len(b) for b in out), key=[len(b) for b in out].count)
    good = [b for b in out if len(b) == n]
    words = [sum(x << i for i, x in enumerate(b)) for b in good]
    if n < 4 or not any(0 < bin(w).count("1") < n for w in words):
        return None
    return {"model": "manchester", "half": half, "bits": n, "words": words, "idle": idle,
            "others": len(out) - len(good), "cost": description_cost(1, n * len(words), 0)}


# ---------------------------------------------------------------- serial
FORMATS = [(8, None, 1), (8, "even", 1), (8, "odd", 1), (7, None, 1), (7, "even", 1),
           (7, "odd", 1), (8, None, 2), (9, None, 1), (6, None, 1), (5, None, 1)]


def serial_read(runs, idle, bit, fmt):
    """The runs as UART characters. Returns (chars, errors) with edges checked: a
    character's edges must fall on its bit boundaries."""
    data_bits, parity, stops = fmt
    cells = 1 + data_bits + (1 if parity else 0) + stops
    # a list of edges, in samples
    t, edges = 0, []
    for lv, d in runs:
        edges.append((t, lv))
        t += d
    end = t
    chars, errors, i = [], 0, 0

    def level_at(x):
        lo, hi = 0, len(edges) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if edges[mid][0] <= x:
                lo = mid
            else:
                hi = mid - 1
        return edges[lo][1]

    while i < len(edges):
        ts, lv = edges[i]
        if lv == idle:
            i += 1
            continue
        if ts + cells * bit > end + bit:
            break
        vals = [level_at(ts + (k + 0.5) * bit) for k in range(cells)]
        ok = vals[0] != idle and all(v == idle for v in vals[-stops:])
        bits = [1 if v == idle else 0 for v in vals[1:1 + data_bits]]
        if parity:
            p = 1 if vals[1 + data_bits] == idle else 0
            want = sum(bits) & 1 if parity == "even" else 1 - (sum(bits) & 1)
            ok = ok and p == want
        # the edges inside the character must sit on bit boundaries
        j = i + 1
        while j < len(edges) and edges[j][0] < ts + (cells - stops) * bit + bit / 2:
            off = (edges[j][0] - ts) / bit
            if abs(off - round(off)) > 0.3:
                ok = False
            j += 1
        if ok:
            chars.append(sum(b << k for k, b in enumerate(bits)))
        else:
            errors += 1
        while j < len(edges) and edges[j][1] == idle:
            j += 1
        i = j if j > i else i + 1
    return chars, errors


def serial_model(runs, idle):
    """UART characters, with this level as the line's rest. A space between characters
    that is no whole number of bits costs extra: a UART has no reason to keep such
    gaps often, while a pulse code read as characters is full of them."""
    if not runs:
        return None
    shortest = clusters([d for _, d in runs])[0]
    # the shortest run's own median is the bit time, unless a run of two bits was the
    # shortest there is: then no character here has a single-bit run, and it is refused
    bit = shortest[2]
    if bit < MIN_SAMPLES:
        return None
    best = None
    active = [d for lv, d in runs if lv != idle]
    if not active or any(abs(d / bit - round(d / bit)) > 0.3 for d in active):
        return None
    for fmt in FORMATS:
        chars, errors = serial_read(runs, idle, bit, fmt)
        if not chars or errors * 10 > len(chars):
            continue
        off = sum(1 for lv, d in runs if lv == idle and abs(d / bit - round(d / bit)) > 0.3)
        cost = description_cost(2, fmt[0] * len(chars) + 8 * off, 8 * errors)
        if best is None or cost < best["cost"]:
            best = {"model": "serial", "bit": bit, "format": fmt, "chars": chars,
                    "errors": errors, "idle": idle, "cost": cost}
    if best is None or len(set(best["chars"])) < 2:
        return None                    # one value over and over is a clock, not data
    return best


# ---------------------------------------------------------------- check fields
def bytes_of(word, nbits):
    return [(word >> (8 * i)) & 0xFF for i in range(nbits // 8)]


def find_checks(words, nbits):
    """Bytes that follow from others in every frame. A rule counts only if its inputs
    take at least three values across the frames, as a constant proves nothing."""
    if nbits % 8 or nbits < 16 or len(words) < 3:
        return []
    frames = [bytes_of(w, nbits) for w in words]
    n = len(frames[0])
    out = []

    def varied(positions):
        return len({tuple(f[p] for p in positions) for f in frames}) >= 3

    for i in range(n):
        for j in range(i):
            if varied([j]) and all(f[i] == f[j] ^ 0xFF for f in frames):
                out.append({"byte": i, "rule": "not", "of": j})
                break
        else:
            if i >= 1 and varied(list(range(i))):
                if all(f[i] == _xor(f[:i]) for f in frames):
                    out.append({"byte": i, "rule": "xor"})
                elif all(f[i] == sum(f[:i]) & 0xFF for f in frames):
                    out.append({"byte": i, "rule": "sum"})
                elif all(f[i] == -sum(f[:i]) & 0xFF for f in frames):
                    out.append({"byte": i, "rule": "negsum"})
    return out


def _xor(bs):
    v = 0
    for b in bs:
        v ^= b
    return v


def _poly_deg(p):
    return p.bit_length() - 1


def _poly_mod(a, b):
    db = _poly_deg(b)
    while a and _poly_deg(a) >= db:
        a ^= b << (_poly_deg(a) - db)
    return a


def _poly_gcd(a, b):
    while b:
        a, b = b, _poly_mod(a, b)
    return a


def find_crc(words, nbits, width):
    """A CRC of `width` bits in the top of the frame, with any polynomial, init and
    final XOR. Two frames of a length differ by a multiple of the polynomial: the
    gcd of those differences is the polynomial, if there are enough of them. Tried in
    both bit orders. Returns {"width","poly","order","constant"} or None. Needs frames
    whose message bits vary, and at least width + 4 frames, as a gcd of fewer can
    leave a multiple of the real polynomial."""
    uniq = sorted(set(words))
    if nbits <= width + 4 or len(uniq) < width // 4 + 4:
        return None
    msg_bits = nbits - width
    for order in ("msb", "lsb"):
        def stream(w):
            bits = [(w >> i) & 1 for i in range(nbits)]
            if order == "msb":
                # sent LSB first in `words`; bytes reversed bit by bit
                bits = [b for k in range(0, nbits, 8) for b in reversed(bits[k:k + 8])]
                return bits
            return bits
        polys = []
        for w in uniq:
            b = stream(w)
            polys.append(sum(x << (nbits - 1 - i) for i, x in enumerate(b)))
        g = 0
        for w in polys[1:]:
            g = _poly_gcd(g, w ^ polys[0]) if g else w ^ polys[0]
        if not g or _poly_deg(g) != width:
            continue
        # the rest is one constant for this length of message
        k = {_poly_mod(w, g) for w in polys}
        if len(k) != 1:
            continue
        return {"width": width, "poly": g ^ (1 << width), "order": order,
                "constant": k.pop(), "message_bits": msg_bits}
    return None


def crc_fill(word, nbits, crc):
    """A frame's word with its CRC field recomputed for its message bits."""
    width, msg_bits, order = crc["width"], crc["message_bits"], crc["order"]
    g = crc["poly"] | 1 << width

    def forward(w):
        bits = [(w >> i) & 1 for i in range(nbits)]
        if order == "msb":
            bits = [b for k in range(0, nbits, 8) for b in reversed(bits[k:k + 8])]
        return bits

    def back(bits):
        if order == "msb":
            bits = [b for k in range(0, nbits, 8) for b in reversed(bits[k:k + 8])]
        return sum(b << i for i, b in enumerate(bits))

    bits = forward(word)
    bits[msg_bits:] = [0] * width
    v = sum(x << (nbits - 1 - i) for i, x in enumerate(bits))
    rem = _poly_mod(v, g) ^ crc["constant"]
    bits[msg_bits:] = [(rem >> (width - 1 - i)) & 1 for i in range(width)]
    return back(bits)


# ---------------------------------------------------------------- the learner
def learn_runs(runs, rate):
    """Runs of one wire -> everything found, or Refused with the reason."""
    if len(runs) < 12:
        raise Refused(f"only {len(runs)} runs: too short a recording to learn from")
    runs, carrier = strip_carrier(runs)
    idle = idle_level(runs)
    active = 1 - idle
    candidates = []
    for gap in gap_candidates(runs, idle) or [max(d for _, d in runs) + 1]:
        frames = split_frames(runs, idle, gap)
        if len(frames) < 1:
            continue
        for m in (pulse_model(frames, active), manchester_model(frames, idle)):
            if m:
                m["gap"] = statistics.median(d for lv, d in runs[1:-1] if lv == idle and d >= gap)                     if any(lv == idle and d >= gap for lv, d in runs[1:-1]) else gap
                m["frames"] = len(frames)
                candidates.append(m)
    # a long low character can outlast every idle run, so the rest level is tried both
    # ways for serial; ties go to the pulse codes, tried first
    for lv in (idle, active):
        s = serial_model(runs, lv)
        if s:
            candidates.append(s)
    if not candidates:
        raise Refused("no line code fits: not a pulse code, Manchester or serial, or it "
                      "has structure but no data")
    best = min(candidates, key=lambda m: m["cost"])
    best.update(idle=best.get("idle", idle), carrier=carrier, rate=rate,
                others=best.get("others", 0))
    if best["model"] != "serial":
        best["checks"] = find_checks(best["words"], best["bits"])
        for width in (16, 8):
            c = None if best["checks"] else find_crc(best["words"], best["bits"], width)
            if c:
                best["crc"] = c
                break
    return best


def describe(m):
    us = 1e6 / m["rate"]
    lines = [f"{m['model']}, idle {'high' if m['idle'] else 'low'}"]
    if m.get("carrier"):
        c = m["carrier"]
        lines.append(f"carrier {m['rate'] / c['period'] / 1000:.1f} kHz, duty {c['high'] / c['period']:.2f}")
    if m["model"] == "serial":
        d, p, st = m["format"]
        lines.append(f"{m['rate'] / m['bit']:.0f} baud, {d}{(p or 'N')[0].upper()}{st}")
        lines.append("bytes " + " ".join(f"{c:02x}" for c in m["chars"][:24]))
        return "\n".join(lines)
    if m["model"] == "manchester":
        lines.append(f"half bit {m['half'] * us:.1f} us, {m['bits']} bits a frame")
    else:
        if m.get("leader"):
            lines.append(f"leader mark {m['leader'][0] * us:.1f} us, space {m['leader'][1] * us:.1f} us")
        z, o = m["zero"], m["one"]
        lines.append(f"zero mark {z[0] * us:.1f} us, space {z[1] * us:.1f} us; one mark {o[0] * us:.1f} us, "
                     f"space {o[1] * us:.1f} us; {m['bits']} bits a frame")
        if m.get("trailer"):
            lines.append(f"stop mark {m['trailer'] * us:.1f} us")
    for w in m["words"][:24]:
        lines.append(f"frame {w:#0{m['bits'] // 4 + 3}x}")
    for c in m.get("checks", []):
        lines.append(f"byte {c['byte']} is the {c['rule']} of " +
                     (f"byte {c['of']}" if "of" in c else "the bytes before it"))
    if m.get("crc"):
        c = m["crc"]
        lines.append(f"CRC-{c['width']}, polynomial {c['poly']:#x}, {c['order']} first")
    if m.get("others"):
        lines.append(f"{m['others']} frames of another shape are not read")
    return "\n".join(lines)


def to_table(m, send=None, pin=0, clock_hz=12_000_000):
    """The learned protocol as msf_gen.py's table, in clock cycles. send: words to
    send instead of what the recording held; check bytes and CRCs are recomputed."""
    k = clock_hz / m["rate"]
    cy = lambda v: max(1, int(round(v * k)))
    active, idle = 1 - m["idle"], m["idle"]
    table = {"name": "learned", "pin": pin, "idle": idle, "clock_hz": clock_hz,
             "model": m["model"], "symbols": {}, "carrier": None}
    if m.get("carrier"):
        table["carrier"] = {"period": cy(m["carrier"]["period"]), "high": cy(m["carrier"]["high"])}
    if m["model"] == "serial":
        bit = cy(m["bit"])
        d, parity, stops = m["format"]
        table["symbols"] = {"zero": [[0 if idle else 1, bit]], "one": [[idle, bit]]}
        table["serial"] = {"bit": bit, "data_bits": d, "parity": parity, "stops": stops}
        table["frames"] = [{"bits": d, "word": c} for c in (send if send is not None else m["chars"])]
        return table
    if m["model"] == "manchester":
        h = cy(m["half"])
        table["symbols"] = {"zero": [[1, h], [0, h]], "one": [[0, h], [1, h]]}
    else:
        table["symbols"] = {"zero": [[active, cy(m["zero"][0])], [idle, cy(m["zero"][1])]],
                            "one": [[active, cy(m["one"][0])], [idle, cy(m["one"][1])]]}
        if m.get("leader"):
            table["symbols"]["leader"] = [[active, cy(m["leader"][0])], [idle, cy(m["leader"][1])]]
        if m.get("trailer"):
            table["symbols"]["trailer"] = [[active, cy(m["trailer"])]]
    table["symbols"]["gap"] = [[idle, cy(m.get("gap", 0))]]
    words = list(m["words"]) if send is None else [fill_checks(w, m) for w in send]
    table["frames"] = [{"bits": m["bits"], "word": w} for w in words]
    return table


def fill_checks(word, m):
    """Recompute a frame's check bytes and CRC for what it now says."""
    nbits = m["bits"]
    for c in m.get("checks", []):
        bs = bytes_of(word, nbits)
        i = c["byte"]
        if c["rule"] == "not":
            bs[i] = bs[c["of"]] ^ 0xFF
        elif c["rule"] == "xor":
            bs[i] = _xor(bs[:i])
        elif c["rule"] == "sum":
            bs[i] = sum(bs[:i]) & 0xFF
        else:
            bs[i] = -sum(bs[:i]) & 0xFF
        word = sum(b << (8 * k) for k, b in enumerate(bs))
    if m.get("crc"):
        word = crc_fill(word, nbits, m["crc"])
    return word


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("capture", help=".vcd or .sr")
    ap.add_argument("--pin", type=int, default=0, help="the channel to read")
    ap.add_argument("--signal", help="a VCD signal's name, instead of --pin")
    ap.add_argument("--send", help="hex words to send, comma list; check fields are recomputed")
    ap.add_argument("--out-pin", type=int, default=0, help="the uio pin to send on")
    ap.add_argument("--clock", type=int, default=12_000_000)
    ap.add_argument("-o", help="write msf_gen.py's table here")
    a = ap.parse_args()
    events, rate, _ = msf_capture.read_trace(a.capture, [a.signal] if a.signal else None)
    pin = 0 if a.signal else a.pin
    try:
        m = learn_runs(runs_of(events, pin), rate)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(describe(m))
    if a.o:
        send = [int(x, 16) for x in a.send.split(",")] if a.send else None
        with open(a.o, "w", encoding="utf-8") as f:
            json.dump(to_table(m, send, a.out_pin, a.clock), f, indent=1)
        print(f"table: {a.o}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
