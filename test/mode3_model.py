# SPDX-License-Identifier: Apache-2.0
"""The RX engine's mode 3 (phase-tracking Manchester, src/tt_um_msf_serdes.v) in Python, bit for
bit, with and without fine mode (RX_OPT bit 6), against 10BASE-T frames with edge jitter.

It is the study behind fine mode (plan-v4 6.5 f) and the reference test_eth10_rx compares the
RTL with, frame by frame. The receiver: a 16-bit phase rx_tmr advances RX_DIV a cycle; the first
centre sets 0.5; the lock pulls the next centres toward 0.5 (v3: six, half way each; fine: 30,
the k-th 1/2^(1 + floor(log2 k)), capped at 1/32); after it a centre counts only in the bit's
middle half (phase 0.25-0.75) and pulls 1/16 (fine: 1/32); a bit with no centre ends the frame;
the level after a centre is the bit. Fine mode also samples the pin half a cycle before each
rising edge: when an edge is seen and that sample already has the new level, the edge came in
the first half of the cycle and its phase is rx_tmr - RX_DIV/2.

The line (test/test_eth10_rx.py draws it the same way): preamble 7 x 0x55, SFD 0xD5, the frame,
bytes LSB first, the first half of a cell the inverse of the bit; 10 Mbit/s off by +-100 ppm;
every edge moved by up to +-J ns, uniform or always at +-J ("extreme"); TP_IDL, then idle low.

    python mode3_model.py [--frames 1000] [--jitter 10,13.5,15] [--dist uniform|extreme]

What it found (19 Nov 2026, minimum frames, +-100 ppm):
* v3 at 13.5 ns: about half the frames wrong. Half-cycle sampling alone: 1 % (uniform),
  28 % (extreme). Fine mode: 0 of 4000 (uniform, also at 14 ns), 3 of 4000 (extreme).
* The hard limit: an edge is seen to the nearest 10 ns (v3: 20 ns), and +-100 ppm slides the
  grid under the edges, so a group of them can step a whole grid point at once:
  a centre lands up to J + 10 ns from where the receiver expects it, the window allows 25.
  Fine mode's worst case is 15 ns; v3's is 5 ns (its 10 ns holds for uniform jitter only).
"""
import argparse
import bisect
import random

NS = 20.0          # 50 MHz
DIV = 13107        # RX_DIV: 65536 x 10 MHz / 50 MHz


def gear(n_centres=31, track=5):
    """Shifts for lock centres 2..n: the k-th centre pulls 1/2^(1 + floor(log2 k)), capped."""
    return tuple(min(k.bit_length(), track) for k in range(1, n_centres))


V3_LOCK, V3_TRACK = (1,) * 6, 4          # src/tt_um_msf_serdes.v with RX_OPT bit 6 clear
FINE_LOCK, FINE_TRACK = gear(31, 5), 5   # with it set


def pull(te, s):
    """te pulled 1/2^s of the way to 0.5, as the RTL rounds it."""
    if s == 1:
        return (te + 0x8000) >> 1                  # p_half
    return (te + ((0x8000 - te) >> s)) & 0xFFFF    # p_frac


def bits_of(frame):
    """Preamble, SFD and the frame's bytes, LSB first."""
    out = []
    for b in bytes([0x55] * 7 + [0xD5]) + bytes(frame):
        out += [(b >> i) & 1 for i in range(8)]
    return out


def random_frame(rng, payload=46):
    body = bytes([0x02, 0, 0, 0, 0, 0x01, 0x02, 0x4D, 0x53, 0x46, 0x33, 0x00, 0x88, 0xB5])
    return body + bytes(rng.randrange(256) for _ in range(payload + 4))   # any 4 bytes as FCS


def line_edges(bits, ppm, jitter, rng, t0, dist="uniform"):
    """(edge times in ns, the level after each); the line idles low."""
    halves = []
    for b in bits:
        halves += [1 - b, b]
    h = 50.0 * (1 + ppm / 1e6)
    times, levels, lv = [], [], 0
    for i, v in enumerate(halves):
        if v != lv:
            d = rng.uniform(-jitter, jitter) if dist == "uniform" else rng.choice((-jitter, jitter))
            times.append(t0 + i * h + d)
            levels.append(v)
            lv = v
    end = t0 + len(halves) * h
    if lv == 0:
        times.append(end); levels.append(1)
    times.append(end + 300.0); levels.append(0)              # TP_IDL, then idle
    return times, levels


def level_at(times, levels, t):
    i = bisect.bisect_right(times, t)
    return levels[i - 1] if i else 0


def samples(times, levels, phase=0.0):
    """[(pin half a cycle before rising edge k, pin at rising edge k)] for k = 1, 2, ...: what
    the engine sees, and what a testbench drives on the falling and rising edges."""
    n = int((times[-1] + 400) / NS)
    return [(level_at(times, levels, k * NS + phase - NS / 2), level_at(times, levels, k * NS + phase))
            for k in range(1, n)]


def receive(seq, fine, lock_s=None, track_s=None, trace=None):
    """The bits of the first frame, as mode 3 takes them from samples(). With a list as
    trace, (rx_tmr, rx_ones) after each sample is appended to it, until the frame ends."""
    lock_s = (FINE_LOCK if fine else V3_LOCK) if lock_s is None else lock_s
    track_s = (FINE_TRACK if fine else V3_TRACK) if track_s is None else track_s
    tmr, run, mw, ones, prev = 0, False, False, 0, 0
    bits = []
    for half, pin in seq:
        edge = pin != prev
        back = DIV // 2 if fine and edge and half == pin else 0     # p_early: rx_half
        te = (tmr - back) & 0xFFFF
        win = ((te >> 15) ^ (te >> 14)) & 1
        lock = ones == len(lock_s) + 1
        if edge and (not run or not lock or win):
            if not run:
                base, ones = 0x8000, 1
            elif not lock:
                base, ones = pull(te, lock_s[ones - 1]), ones + 1
            else:
                base = pull(te, track_s)
            tmr = (base + back + DIV) & 0xFFFF                     # base is the edge's phase
            run, mw = True, True
            bits.append(pin)
            if trace is not None:
                trace.append((tmr, ones))
            prev = pin
            continue
        elif run:
            s = tmr + DIV
            tmr = s & 0xFFFF
            if s >> 16:
                if mw:
                    mw = False
                else:
                    return bits                                    # a bit with no centre
        if trace is not None:
            trace.append((tmr, ones))
        prev = pin
    return bits


def sweep(frames, jitters, seed, fine, payload=46, dist="uniform", lock_s=None, track_s=None):
    out = {}
    for j in jitters:
        rng = random.Random(seed)
        wrong = 0
        for _ in range(frames):
            bits = bits_of(random_frame(rng, payload))
            ppm = rng.choice((-100.0, 100.0))
            times, levels = line_edges(bits, ppm, j, rng, 40 * NS + rng.uniform(0, NS), dist)
            wrong += receive(samples(times, levels, rng.uniform(0, NS)), fine, lock_s, track_s) != bits
        out[j] = wrong
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=1000)
    ap.add_argument("--jitter", default="10,12,13.5,15")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--payload", type=int, default=46, help="46: minimum frame, 1500: maximum")
    ap.add_argument("--dist", default="uniform", choices=["uniform", "extreme"])
    a = ap.parse_args()
    js = [float(x) for x in a.jitter.split(",")]
    v3 = sweep(a.frames, js, a.seed, False, a.payload, a.dist)
    fine = sweep(a.frames, js, a.seed, True, a.payload, a.dist)
    print("frames wrong of %d (payload %d bytes, +-100 ppm), %s edge jitter +-J ns"
          % (a.frames, a.payload, a.dist))
    print("%8s %10s %10s" % ("J (ns)", "v3", "fine"))
    for j in js:
        print("%8.1f %10d %10d" % (j, v3[j], fine[j]))
