# SPDX-License-Identifier: Apache-2.0
"""tools/msf_learn.py without a chip: protocols nobody designed, drawn at random,
must be decoded word for word or refused; a wrong word fails. Signals with no data
in them must be refused. pytest test/test_learn_tool.py (no simulator needed)."""
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import msf_learn as ml  # noqa: E402

RATE = 1_000_000


def pulse_runs(rng, coding, unit, ratio, words, nbits, leader, idle, jitter=0.0, stop=True):
    """A pulse code's frames as (level, samples) runs, with their gaps."""
    active = 1 - idle
    runs = []

    def j(d):
        return max(1, int(round(d * (1 + rng.uniform(-jitter, jitter)))))

    long_ = int(unit * ratio)
    gap = (nbits + 4) * (unit + long_) * 3
    for w in words:
        if leader:
            runs += [(active, j(unit * 16)), (idle, j(unit * 8))]
        for i in range(nbits):
            b = (w >> i) & 1
            if coding == "distance":
                runs += [(active, j(unit)), (idle, j(long_ if b else unit))]
            elif coding == "length":
                runs += [(active, j(long_ if b else unit)), (idle, j(unit))]
            else:
                runs += [(active, j(long_ if b else unit)), (idle, j(unit if b else long_))]
        if stop or coding == "distance":
            runs.append((active, j(unit)))
            runs.append((idle, gap))
        else:
            runs.append((idle, gap))
    # merge the two idle runs a frame without a stop mark leaves
    out = []
    for lv, d in runs:
        if out and out[-1][0] == lv:
            out[-1] = (lv, out[-1][1] + d)
        else:
            out.append((lv, d))
    return [(idle, gap)] + out


def manchester_runs(rng, half, words, nbits, idle, jitter=0.0):
    active = 1 - idle
    runs = []
    gap = half * (nbits + 8) * 4

    def j(d):
        return max(1, int(round(d * (1 + rng.uniform(-jitter, jitter)))))

    for w in words:
        seq = []
        for i in range(nbits):
            b = (w >> i) & 1
            seq += [active, idle] if b == idle else [idle, active]
        cur, n = seq[0], 0
        for v in seq:
            if v == cur:
                n += 1
            else:
                runs.append((cur, j(n * half)))
                cur, n = v, 1
        runs.append((cur, j(n * half)))
        runs.append((idle, gap))
    out = [(idle, gap)]
    for lv, d in runs:
        if out[-1][0] == lv:
            out[-1] = (lv, out[-1][1] + d)
        else:
            out.append((lv, d))
    return out


def serial_runs(rng, bit, chars, idle, data_bits=8, parity=None, stops=1, gap=0):
    levels = [idle] * (bit * 5)
    for c in chars:
        cells = [1 - idle]
        bits = [(c >> i) & 1 for i in range(data_bits)]
        cells += [idle if b else 1 - idle for b in bits]
        if parity:
            p = sum(bits) & 1 if parity == "even" else 1 - (sum(bits) & 1)
            cells.append(idle if p else 1 - idle)
        cells += [idle] * stops
        for x in cells:
            levels += [x] * bit
        levels += [idle] * gap
    out = []
    for v in levels:
        if out and out[-1][0] == v:
            out[-1] = (v, out[-1][1] + 1)
        else:
            out.append((v, 1))
    return out


def random_words(rng, n, nbits):
    return [rng.getrandbits(nbits) for _ in range(n)]


@pytest.mark.parametrize("seed", range(60))
def test_random_pulse_codes(seed):
    rng = random.Random(seed)
    coding = rng.choice(["distance", "length", "width"])
    unit = rng.randrange(20, 2000)
    ratio = rng.uniform(2, 4)
    nbits = rng.choice([8, 12, 16, 20, 24, 32, 48])
    words = random_words(rng, 6, nbits)
    idle = rng.randrange(2)
    leader = rng.random() < 0.5
    runs = pulse_runs(rng, coding, unit, ratio, words, nbits, leader, idle)
    m = ml.learn_runs(runs, RATE)
    assert m["model"] == coding, (m["model"], coding)
    assert m["bits"] == nbits and m["words"] == words


@pytest.mark.parametrize("seed", range(30))
def test_random_manchester(seed):
    rng = random.Random(1000 + seed)
    half = rng.randrange(10, 1000)
    nbits = rng.choice([8, 14, 16, 24, 32])
    idle = rng.randrange(2)
    # a frame must start with a bit whose first half is the idle level, as RC-5's does,
    # or the decoder cannot see that half: bit 0 here is always such a bit
    words = [(w & ~1) | (0 if idle else 1) for w in random_words(rng, 6, nbits)]
    runs = manchester_runs(rng, half, words, nbits, idle)
    m = ml.learn_runs(runs, RATE)
    assert m["model"] == "manchester", m["model"]
    assert m["bits"] == nbits
    # bit polarity is a convention the waveform cannot settle, so either reading counts
    mask = (1 << nbits) - 1
    assert m["words"] == words or m["words"] == [w ^ mask for w in words], (m["words"], words)


@pytest.mark.parametrize("seed", range(30))
def test_random_serial(seed):
    rng = random.Random(2000 + seed)
    bit = rng.randrange(8, 400)
    fmt = rng.choice([(8, None, 1), (8, "even", 1), (8, "odd", 1), (7, None, 1), (8, None, 2)])
    idle = 1 if rng.random() < 0.8 else 0
    chars = [rng.randrange(1 << fmt[0]) for _ in range(40)]
    chars[0] = 0x55 & ((1 << fmt[0]) - 1)        # at least one single-bit run
    runs = serial_runs(rng, bit, chars, idle, *fmt, gap=rng.choice([0, bit, 3 * bit]))
    m = ml.learn_runs(runs, RATE)
    assert m["model"] == "serial", m["model"]
    assert m["bit"] == bit and m["chars"] == chars, (m["bit"], bit, m["format"], fmt)


@pytest.mark.parametrize("seed", range(30))
def test_under_abuse_decodes_or_refuses(seed):
    """Jitter on every run: it may refuse, but a word it does give must be right."""
    rng = random.Random(3000 + seed)
    coding = rng.choice(["distance", "length", "width"])
    unit = rng.randrange(40, 800)
    nbits = rng.choice([8, 16, 24, 32])
    words = random_words(rng, 6, nbits)
    runs = pulse_runs(rng, coding, unit, 3, words, nbits, rng.random() < 0.5, 1, jitter=0.1)
    try:
        m = ml.learn_runs(runs, RATE)
    except ml.Refused:
        return
    assert m["words"] == words, (m["model"], coding)


def run_list(durations, idle=1):
    level, out = idle, []
    for d in durations:
        out.append((level, d))
        level = 1 - level
    return out


@pytest.mark.parametrize("seed", range(20))
def test_noise_is_refused(seed):
    rng = random.Random(4000 + seed)
    kind = seed % 3
    if kind == 0:
        ds = [rng.randrange(1, 5000) for _ in range(300)]
    elif kind == 1:
        ds = [int(rng.expovariate(1 / 300)) + 1 for _ in range(300)]
    else:
        ds = [int(rng.lognormvariate(5, 1)) + 1 for _ in range(300)]
    with pytest.raises(ml.Refused):
        ml.learn_runs(run_list(ds), RATE)


def test_square_wave_is_no_data():
    with pytest.raises(ml.Refused):
        ml.learn_runs(run_list([100] * 400), RATE)


def test_servo_pulses_are_no_data():
    rng = random.Random(1)
    ds = []
    for _ in range(50):
        p = rng.randrange(1000, 2000)      # any width: a continuous range, not two symbols
        ds += [p, 20000 - p]
    with pytest.raises(ml.Refused):
        ml.learn_runs(run_list(ds, idle=0), RATE)


def test_tone_bursts_are_no_data():
    ds = []
    for _ in range(20):
        ds += [10, 10] * 40 + [10, 3000]
    with pytest.raises(ml.Refused):
        ml.learn_runs(run_list(ds), RATE)


def test_too_short_is_refused():
    with pytest.raises(ml.Refused):
        ml.learn_runs(run_list([10, 20, 10]), RATE)


def test_checks_and_new_message():
    """An NEC-style frame: two complements, found from the data; a new command gets
    its complement recomputed."""
    rng = random.Random(5)
    words = []
    for _ in range(10):
        a, c = 0x10, rng.randrange(256)
        words.append(a | (a ^ 0xFF) << 8 | c << 16 | (c ^ 0xFF) << 24)
    runs = pulse_runs(rng, "distance", 562, 3, words, 32, True, 1)
    m = ml.learn_runs(runs, RATE)
    assert m["words"] == words
    # the address never changes, so that rule is not claimed: a constant proves nothing
    rules = {(c["byte"], c["rule"], c.get("of")) for c in m["checks"]}
    assert (3, "not", 2) in rules and not any(b == 1 for b, _, _ in rules), rules
    t = ml.to_table(m, send=[words[0] & 0xFFFF | 0x77 << 16])
    w = t["frames"][0]["word"]
    assert w >> 16 & 0xFF == 0x77 and w >> 24 == 0x88


def test_crc_found_and_recomputed():
    """A frame with a CRC-8 no table named: polynomial found from the frames alone,
    and a new message gets the right CRC."""
    rng = random.Random(9)
    poly = 0x2F

    def crc8(bs):
        c = 0
        for b in bs:
            for i in range(7, -1, -1):
                f = (c >> 7) ^ ((b >> i) & 1)
                c = (c << 1) & 0xFF
                if f:
                    c ^= poly
        return c

    words = []
    for _ in range(24):
        bs = [rng.randrange(256) for _ in range(3)]
        words.append(sum(b << (8 * i) for i, b in enumerate(bs)) | crc8(bs) << 24)
    runs = pulse_runs(rng, "distance", 100, 3, words, 32, True, 1)
    m = ml.learn_runs(runs, RATE)
    assert m["crc"]["width"] == 8 and m["crc"]["poly"] == poly and m["crc"]["order"] == "msb", m.get("crc")
    new = [0x11, 0x22, 0x33]
    w = ml.fill_checks(sum(b << (8 * i) for i, b in enumerate(new)), m)
    assert w >> 24 == crc8(new)
