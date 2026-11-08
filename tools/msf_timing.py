#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Static deadline check for msfcore firmware: is any WAIT_CMP ever late?

Firmware schedules pin edges on absolute cycle-counter targets: a deadline
register is read from CNT (`CNT_R`), advanced by a period (`ADDS R5, R5, R6`)
and waited for (`WAIT_CMP R5`). The edge is exact only if the program reaches
the `WAIT_CMP` before its target; otherwise the target has passed (isa.md §5:
the wait releases at once with Z=1 and sets LATE, and the edge is late).

Every instruction's cycle cost is fixed by isa.md §5 and §7, so lateness can
be decided before the program runs. This tool assembles a program and
explores its control flow with abstract register values:

  constant          ("c", k)
  time-relative     ("t", lo, hi): the value is now + d, d in [lo, hi], where
                    "now" is CNT in the cycle the next instruction issues
  unknown           ("u",)

Each step applies the instruction to the registers and then advances "now"
by the instruction's cost (an interval: blocking ops on outside events are
bounded by the thread's timeout, or unbounded). Branches on known flags take
one way, so counted loops (`LDR R4, #10 ... DECR R4 / JNZ`) unroll exactly;
other branches take both. A program point that collects many states joins
them (and widens growing intervals to infinity), so the search ends.

A deadline moved on by a value from data (firmware/replay.s) can't be derived
from the program, so the source declares its range on the instruction that uses
it, `ADDS R5, R5, R2  ; @range R2 16 255`. Where the checker cannot derive the
value, that is a promise it takes on trust, and the tool that writes the data has to
keep it. Where it can (a constant, or a range it computed), it checks the promise
and refuses the program if it does not hold: a planted bug that cut margin_i2c.s's
THIGH to 15 under `@range R7 SWEEP_LO TMAX` was certified, and ran 45 cycles below
the bound, until 8 Nov.

At each `WAIT_CMP rs` with rs time-relative, d at issue must be >= 1: then
the wait releases when CNT == target and the next instruction issues at
target+1 (single-thread mode). The report lists, per deadline, the range of
d over every path (its minimum - 1 is the slack in cycles) and, with n
threads running, whether the release is jitter-free (d a multiple of n).

With n threads running every instruction costs n cycles (isa.md §7). Using n
for the whole program is conservative: code that runs in single-thread mode
is never slower than that. Threads started with `TSTART` from a constant
address are analysed too, from a cleared state.

`--check-model N` runs the program on the ISA model (test/isa_model.py) for
N cycles and checks that every WAIT_CMP issue it observes lies in the range
the analysis computed. The model has no SERDES, so this covers few programs,
with their pins held constant; it says how many deadlines it compared.

`--rtl-log FILE` is the check that counts: the firmware tests write what the
RTL did at each deadline, with the tests' peers on the pins (DeadlineWatch in
test/fw_tb.py, which fails a test on the spot). This reports how many of the
deadlines were reached, and fails on any that was not.

Usage:
    python tools/msf_timing.py firmware/uart_tx.s [--threads N] [-D NAME=V]
           [--check-model CYCLES]
    python tools/msf_timing.py --all        every firmware/*.s with a WAIT_CMP
Exit status 1 if any deadline may be missed or is not derived from CNT.
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "test"))
import assembler as asm  # noqa: E402

M16 = 0xFFFF
M10 = 0x3FF
U = ("u",)
INF = None

# opcodes (isa.md §3)
(LDR, ADDS, SUBS, AND, OR, XOR, SHL, SHR, MOV, JMP, JZ, JNZ, JP, JN, NOP, EXT,
 PIN_W, PIN_R, SETM, CLRM, CNT_R, INCR, DECR, IN_RD, CALL, RET, WAIT_EDGE, WAIT_CMP,
 SHIFT_OUT, SHIFT_IN, MEM_W, LDH) = range(32)
(X_SRLD, X_SRRD, X_XFER, X_SERCFG, X_OUTW, X_MEMA, X_LD, X_ST, X_SWAP, X_JR, X_TMO,
 X_STAT, X_TXCFG, X_TXDIV, X_TXPUSH, X_TXWAIT, X_RXCFG, X_RXDIV, X_RXPOP, X_CRCCFG,
 X_CRCW, X_CRCRDL, X_CRCRDH, X_CRCSETL, X_CRCSETH, X_POLL, X_POLH, X_TSTART, X_TSTOP,
 X_TID, X_PW, X_PR) = range(32)
WRITES_RA = {X_SRRD, X_LD, X_SWAP, X_STAT, X_RXPOP, X_CRCRDL, X_CRCRDH, X_TID, X_PR}

MAX_STATES = 256      # distinct states kept per program point before joining
WIDEN_AFTER = 8       # joins at a point before growing bounds are widened


def signed16(v):
    v &= M16
    return v - 0x10000 if v & 0x8000 else v


AHEAD_MAX = 65280     # isa.md §5: a target up to 65,280 cycles ahead is waited for


def offset(k):
    """A 16-bit offset added to a deadline: forward up to AHEAD_MAX, else the
    0-255 cycles behind that the hardware treats as late."""
    k &= M16
    return k if k <= AHEAD_MAX else k - 0x10000


def model_d(v):
    """Target minus CNT as the hardware sees it: late (<= 0) or ahead (>= 1)."""
    v &= M16
    return v if 1 <= v <= AHEAD_MAX else v - 0x10000 if v else 0


def add_iv(a, b):
    return None if a is None or b is None else a + b


# ---------------------------------------------------------------- values
def shift(v, lo, hi):
    """Advance time by [lo, hi] cycles: a time value's d decreases."""
    if v[0] != "t":
        return v
    return ("t", None if v[1] is None or hi is None else v[1] - hi,
            None if v[2] is None else v[2] - lo)


def join(a, b):
    if a == b:
        return a
    if a[0] == "t" and b[0] == "t":
        lo = None if a[1] is None or b[1] is None else min(a[1], b[1])
        hi = None if a[2] is None or b[2] is None else max(a[2], b[2])
        return ("t", lo, hi)
    # two plain values (constants or ranges): their hull. A deadline moved on by
    # either (DShot's 1 or 0 high time, chosen by a branch) keeps a range instead
    # of becoming unknown when the paths meet
    if a[0] in ("c", "i") and b[0] in ("c", "i"):
        alo, ahi = (a[1], a[1]) if a[0] == "c" else (a[1], a[2])
        blo, bhi = (b[1], b[1]) if b[0] == "c" else (b[1], b[2])
        return ("i", min(alo, blo), max(ahi, bhi))
    return U


# Widening with thresholds: a bound that keeps moving snaps to the next of
# these instead of to infinity, so the search still ends, and a lower bound
# that stays >= 1 (never late) is never widened below 1. Between -2048 and
# 2048 they are 16 apart: a deadline register is often far behind "now"
# just before its period is added (can.s: up to ~175 cycles), and snapping
# that to the next power of two made the next deadline look late.
_FINE = list(range(-2048, 2049, 16))
TH_LO = sorted(set([1 << i for i in range(16)] + [0] + [-(1 << i) for i in range(17)]
                   + _FINE), reverse=True)
TH_HI = sorted(set([1 << i for i in range(16)] + [65280, 65536] + _FINE))


def widen(old, new):
    if old[0] == "t" and new[0] == "t":
        lo, hi = old[1], old[2]
        if lo is not None and (new[1] is None or new[1] < lo):
            lo = None if new[1] is None else next((t for t in TH_LO if t <= new[1]), None)
        if hi is not None and (new[2] is None or new[2] > hi):
            hi = None if new[2] is None else next((t for t in TH_HI if t >= new[2]), None)
        return ("t", lo, hi)
    j = join(old, new)
    # a plain range still growing (a pointer walking a table) gives up at once,
    # so the search ends
    return U if j[0] == "i" and j != old else j


class State:
    __slots__ = ("pc", "regs", "z", "n", "tmo")

    def __init__(self, pc, regs, z, n, tmo):
        self.pc, self.regs, self.z, self.n, self.tmo = pc, tuple(regs), z, n, tmo

    def key(self):
        return (self.pc, self.regs, self.z, self.n, self.tmo)


# ---------------------------------------------------------------- source map
RE_RANGE = re.compile(r";\s*@range\s+R(\d)\s+(\S+)\s+(\S+)", re.I)


def source_map(text, base, defines, include_dirs):
    """address -> source line (pass 1 of the assembler, kept here), the symbols,
    and address -> (register, lo, hi) for each @range annotation."""
    text = asm._expand_includes(text, include_dirs + [asm.FIRMWARE_DIR])
    raw = text.splitlines()
    syms = dict(defines or {})
    out = {}
    ranges = {}
    pc = base
    for ln, labels, mn, args in asm._parse_lines(text):
        for lab in labels:
            syms[lab] = pc
        if mn is None:
            continue
        if mn == ".EQU":
            name, val = [a.strip() for a in args.split(",", 1)]
            if name not in (defines or {}):
                syms[name] = asm._eval(val, syms)
        elif mn == ".ORG":
            pc = asm._eval(args, syms)
        elif mn == ".WORD":
            pc += len(asm._split(args))
        elif mn.startswith("."):
            continue
        else:
            out[pc] = f"{mn} {args}".strip()
            m = RE_RANGE.search(raw[ln - 1])
            if m:
                ranges[pc] = (int(m.group(1)), m.group(2), m.group(3))
            pc += asm._size(mn)
    ranges = {pc: (r, asm._eval(lo, syms), asm._eval(hi, syms))
              for pc, (r, lo, hi) in ranges.items()}
    return out, syms, ranges


# ---------------------------------------------------------------- analysis
ALL_REGS = frozenset(range(8))


def uses_defs(w):
    """Registers an instruction may read and registers it certainly writes."""
    op = w >> 11
    a, b, c = (w >> 8) & 7, (w >> 5) & 7, (w >> 2) & 7
    if ADDS <= op <= XOR:
        return {b, c}, {a}
    if op in (SHL, SHR, MOV):
        return {b}, {a}
    if op in (LDR, CNT_R, PIN_R, IN_RD):
        return set(), {a}
    if op in (LDH, INCR, DECR):
        return {a}, {a}
    if op in (PIN_W, WAIT_CMP, SHIFT_OUT, MEM_W):
        return {a}, set()
    if op == CALL:
        return set(), {7}
    if op == RET:
        return {7}, set()
    if op == EXT:
        sub = w & 0x1F
        return {a, b}, ({a} if sub in WRITES_RA else set())
    return set(), set()


def successors(w, pc):
    """Static successors; None if the target is computed (RET, JR)."""
    op = w >> 11
    nxt = (pc + 1) & M10
    if op in (JMP, CALL):
        return [w & M10]
    if JZ <= op <= JN:
        rel = (w >> 2) & 0x1FF
        rel = rel - 512 if rel & 0x100 else rel
        return [(pc + 1 + rel) & M10, nxt]
    if op == RET or (op == EXT and (w & 0x1F) == X_JR):
        return None
    return [nxt]


def liveness(mem):
    """Registers live on entry to each address (backward dataflow). A RET goes to
    the instruction after some CALL; a JR may go anywhere. Getting this wrong could
    only make a value unknown, never wrong: a dropped register reads as unknown."""
    live = {pc: set() for pc in mem}
    returns = [(pc + 1) & M10 for pc, w in mem.items() if w >> 11 == CALL]
    changed = True
    while changed:
        changed = False
        for pc in sorted(mem, reverse=True):
            u, d = uses_defs(mem[pc])
            succ = successors(mem[pc], pc)
            if mem[pc] >> 11 == RET and returns:
                out = {7} | set().union(*[live.get(t, ALL_REGS) for t in returns])
            elif succ is None:
                out = set(ALL_REGS)
            else:
                out = set().union(*[live.get(t, ALL_REGS) for t in succ])
            new = u | (out - d)
            if new != live[pc]:
                live[pc] = new
                changed = True
    return {pc: frozenset(v) for pc, v in live.items()}


class Analysis:
    def __init__(self, words, base, nthreads, srcmap, ranges=None):
        self.ranges = ranges or {}
        self.mem = {base + i: w for i, w in enumerate(words)}
        self.live = liveness(self.mem)
        self.returns = {(pc + 1) & M10 for pc, w in self.mem.items() if w >> 11 == CALL}
        self.base = base
        self.n = nthreads
        self.src = srcmap
        self.deadlines = {}      # pc -> {"lo", "hi", "aligned", "unchecked", "late"}
        self.problems = []       # (pc, message)
        self.spawns = {}         # entry pc -> tmo

    def word(self, pc):
        if pc not in self.mem:
            raise KeyError(pc)
        return self.mem[pc]

    def problem(self, pc, msg):
        if (pc, msg) not in self.problems:
            self.problems.append((pc, msg))

    # cost helpers ----------------------------------------------------
    def plain(self):
        return (self.n, self.n)

    def jump_cost(self):
        return (2, 2) if self.n == 1 else (self.n, self.n)

    def ld_cost(self):
        return (2 * self.n, 2 * self.n)

    def blocking(self, tmo, may_be_ready):
        n = self.n
        lo = n if may_be_ready else 2 * n
        hi = None if not tmo else tmo + 1 + 2 * n
        return (lo, hi)

    # one instruction ---------------------------------------------------
    def step(self, s):
        """Successor states of s, as (state, cost_lo, cost_hi, fixed): the
        registers are shifted by the cost, then those in `fixed` are set."""
        pc = s.pc
        try:
            w = self.word(pc)
        except KeyError:
            self.problem(pc, "execution reaches an address outside the program")
            return []
        op = w >> 11
        fa, fb, fc = (w >> 8) & 7, (w >> 5) & 7, (w >> 2) & 7
        imm8 = w & 0xFF
        R = list(s.regs)
        if pc in self.ranges:
            r, lo, hi = self.ranges[pc]
            v = R[r]
            if v == U:
                R[r] = ("i", lo, hi)        # the source's promise about a value from data
            elif v[0] == "c" and lo <= v[1] <= hi:
                pass                        # known, and inside the promise: keep it
            elif v[0] == "i" and lo <= v[1] and v[2] <= hi:
                pass
            else:                           # a promise the program itself breaks
                shown = {"c": lambda: str(v[1]), "i": lambda: f"{v[1]}..{v[2]}",
                         "t": lambda: "a deadline"}[v[0]]()
                self.problem(pc, f"@range R{r} {lo} {hi} does not hold: the program "
                                 f"gives R{r} = {shown} here")
        z, nf, tmo = s.z, s.n, s.tmo
        nxt = (pc + 1) & M10
        cost = self.plain()
        out = []

        def const(v):
            return v[1] if v[0] == "c" else None

        if op == LDR:
            R[fa] = ("c", imm8)
        elif ADDS <= op <= MOV:
            x, y = R[fb], R[fc]
            cx, cy = const(x), const(y)
            if op == MOV:
                R[fa] = x
            elif op in (ADDS, SUBS):
                sgn = 1 if op == ADDS else -1
                if cx is not None and cy is not None:
                    v = (cx + sgn * cy) & M16
                    R[fa] = ("c", v)
                    z, nf = int(v == 0), (v >> 15) & 1
                else:
                    if x[0] == "t" and cy is not None:
                        k = offset(sgn * cy)
                        R[fa] = ("t", add_iv(x[1], k), add_iv(x[2], k))
                    elif y[0] == "t" and cx is not None and op == ADDS:
                        k = offset(cx)
                        R[fa] = ("t", add_iv(y[1], k), add_iv(y[2], k))
                    elif x[0] == "t" and y[0] == "i" and op == ADDS:
                        R[fa] = ("t", add_iv(x[1], y[1]), add_iv(x[2], y[2]))
                    elif x[0] == "t" and y[0] == "t" and op == SUBS and \
                            x[1] == x[2] and y[1] == y[2] and x[1] is not None and y[1] is not None:
                        R[fa] = ("c", (x[1] - y[1]) & M16)
                    else:
                        R[fa] = U
                    z = nf = None
            else:
                if op in (SHL, SHR):
                    R[fa] = ("c", ((cx << fc) if op == SHL else (cx >> fc)) & M16) \
                        if cx is not None else U
                elif cx is not None and cy is not None:
                    R[fa] = ("c", {AND: cx & cy, OR: cx | cy, XOR: cx ^ cy}[op])
                else:
                    R[fa] = U
        elif op == JMP:
            nxt, cost = w & M10, self.jump_cost()
        elif JZ <= op <= JN:
            flag = z if op in (JZ, JNZ) else nf
            want = {JZ: 1, JNZ: 0, JP: 0, JN: 1}[op]
            rel = (w >> 2) & 0x1FF
            rel = rel - 512 if rel & 0x100 else rel
            tgt = (pc + 1 + rel) & M10
            regs = tuple(R)
            if flag is None or flag == want:
                out.append((State(tgt, regs, z, nf, tmo), *self.jump_cost(), None))
            if flag is None or flag != want:
                out.append((State(nxt, regs, z, nf, tmo), *self.plain(), None))
            return out
        elif op in (PIN_R, IN_RD):
            R[fa] = U
        elif op == CNT_R:
            R[fa] = ("t", 0, 0)
        elif op in (INCR, DECR):
            d = 1 if op == INCR else -1
            v = R[fa]
            if v[0] == "c":
                r = (v[1] + d) & M16
                R[fa] = ("c", r)
                z, nf = int(r == 0), (r >> 15) & 1
            else:
                R[fa] = ("t", add_iv(v[1], d), add_iv(v[2], d)) if v[0] == "t" else U
                z = nf = None
        elif op == CALL:
            R[7] = ("c", (pc + 1) & M10)
            nxt, cost = w & M10, self.jump_cost()
        elif op == RET:
            t = const(R[7])
            if t is None:
                # R7 unknown (states from several call sites joined): return
                # to every call site, which over-approximates the real one
                R[7] = U
                regs = tuple(R)
                return [(State(r, regs, z, nf, tmo), *self.jump_cost(), None)
                        for r in sorted(self.returns)]
            nxt, cost = t & M10, self.jump_cost()
        elif op == WAIT_EDGE:
            cost = self.blocking(tmo, False)
            z = None
        elif op == WAIT_CMP:
            return self.wait_cmp(s, pc, fa, R, nf, tmo)
        elif op == LDH:
            v = R[fa]
            R[fa] = ("c", (imm8 << 8) | (v[1] & 0xFF)) if v[0] == "c" else U
        elif op == EXT:
            sub = w & 0x1F
            ra, rb = fa, fb
            if sub == X_JR:
                t = const(R[rb])
                if t is None:
                    self.problem(pc, "JR to an address the analysis cannot follow")
                    return []
                nxt, cost = t & M10, self.jump_cost()
            elif sub == X_TMO:
                tmo = const(R[rb])
            elif sub == X_TSTART:
                t = const(R[rb])
                if t is None:
                    self.problem(pc, "TSTART of an address the analysis cannot follow")
                else:
                    self.spawns.setdefault(t & M10, tmo)
                if (ra & 3) == 0:   # restarting itself is not followed
                    pass
            elif sub == X_SWAP:
                v = const(R[rb])
                R[ra] = ("c", ((v & 0xFF) << 8) | (v >> 8)) if v is not None else U
            elif sub == X_TID:
                R[ra] = U
            elif sub in (X_LD,) or (sub == X_PR and rb == 0):
                cost = self.ld_cost()
                R[ra] = U
                if sub == X_PR:
                    z = None
            elif sub in (X_TXPUSH, X_TXWAIT, X_RXPOP):
                cost = self.blocking(tmo, True)
                z = None
                if sub == X_RXPOP:
                    R[ra] = U
            elif sub in (X_CRCW, X_CRCRDL):
                cost = (self.n, 17 + 2 * self.n)
                z = None
                if sub == X_CRCRDL:
                    R[ra] = U
            elif sub == X_PW and ra == 0:
                z = None                       # FIFO_PUT: Z = full
            elif sub in WRITES_RA:
                R[ra] = U
        out.append((State(nxt, tuple(R), z, nf, tmo), *cost, None))
        return out

    def wait_cmp(self, s, pc, fa, R, nf, tmo):
        n = self.n
        v = R[fa]
        dl = self.deadlines.setdefault(pc, {"lo": None, "hi": None, "seen": False,
                                            "aligned": True, "unchecked": False, "late": False})
        out = []
        if v[0] != "t":
            dl["unchecked"] = True
            self.problem(pc, "WAIT_CMP target is not derived from CNT: its slack is unknown")
            st = State(pc + 1, tuple(U if x[0] == "t" else x for x in R), None, nf, tmo)
            return [(st, 2, None, None)]
        lo, hi = v[1], v[2]
        if not dl["seen"]:
            dl["lo"], dl["hi"], dl["seen"] = lo, hi, True
        else:
            dl["lo"] = None if lo is None or dl["lo"] is None else min(dl["lo"], lo)
            dl["hi"] = None if hi is None or dl["hi"] is None else max(dl["hi"], hi)
        if hi is None or hi > AHEAD_MAX:
            dl["late"] = True
            self.problem(pc, f"WAIT_CMP target may be more than {AHEAD_MAX} cycles ahead")
        if lo is None or lo < 1:
            dl["late"] = True
            # the late release: 2 slots, Z = 1, the edge is late
            late = State(pc + 1, R, 1, nf, tmo)
            out.append((late, 2 * n, 2 * n, None))
        if hi is not None and hi < 1:
            return out
        dlo = max(lo, 1) if lo is not None else 1
        if n > 1 and not (lo is not None and lo == hi and lo % n == 0):
            dl["aligned"] = False
        # on time: next issue at the first own slot at or after the target, + n
        def adv(d):
            return None if d is None else n * (-(-d // n)) + n
        alo, ahi = adv(dlo), adv(hi)
        # the waited-on register: next issue - target = adv(d) - d, i.e. 1 in
        # single-thread mode; with n threads n..2n-1, exact if d is known
        if lo is not None and lo == hi:
            rd = lo - adv(lo)
            fixed = {fa: ("t", rd, rd)}
        else:
            fixed = {fa: ("t", -(2 * n - 1), -n)}
        st = State(pc + 1, R, 0, nf, tmo)
        out.append((st, alo, ahi, fixed))
        return out

    # exploration -----------------------------------------------------
    def run(self, entry, tmo):
        start = State(entry, [("c", 0)] * 8, 0, 0, tmo)
        seen = {}
        joined = {}
        joins = {}
        work = [start]
        while work:
            s = work.pop()
            for st, clo, chi, fixed in self.step(s):
                regs = [shift(x, clo, chi) for x in st.regs]
                for r, v in (fixed or {}).items():
                    regs[r] = v
                live = self.live.get(st.pc, ALL_REGS)
                regs = tuple(x if i in live else U for i, x in enumerate(regs))
                ns = State(st.pc, regs, st.z, st.n, st.tmo)
                # states are only joined within one calling context: the
                # same program point and the same return addresses, in R7 or
                # saved in another register around a nested call
                ctx = (ns.pc, tuple(x[1] if x[0] == "c" and x[1] in self.returns else None
                                    for x in ns.regs))
                pcs = seen.setdefault(ctx, set())
                k = ns.key()
                if k in pcs:
                    continue
                if len(pcs) < MAX_STATES and ctx not in joined:
                    pcs.add(k)
                    work.append(ns)
                    continue
                # too many states here: keep one joined state and widen it
                old = joined.get(ctx)
                if old is None:
                    merged = ns
                    for kk in pcs:
                        merged = self._join(merged, State(*kk))
                else:
                    j = joins.get(ctx, 0) + 1
                    joins[ctx] = j
                    merged = self._join(old, ns, widen_=j > WIDEN_AFTER)
                    if merged.key() == old.key():
                        continue
                joined[ctx] = merged
                pcs.add(merged.key())
                work.append(merged)

    @staticmethod
    def _join(a, b, widen_=False):
        f = widen if widen_ else join
        regs = tuple(f(x, y) for x, y in zip(a.regs, b.regs))
        return State(a.pc, regs,
                     a.z if a.z == b.z else None,
                     a.n if a.n == b.n else None,
                     a.tmo if a.tmo == b.tmo else None)


# ---------------------------------------------------------------- driver
def analyse(path, nthreads=None, defines=None, base=128):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    words = asm.assemble_file(path, base_addr=base, defines=defines)
    srcmap, syms, ranges = source_map(text, base, defines, [os.path.dirname(os.path.abspath(path))])
    # first pass with one thread finds the threads the program starts
    a = Analysis(words, base, 1, srcmap, ranges)
    a.run(base, 0xFFFF)
    entries = dict(a.spawns)
    n = nthreads or min(4, 1 + len(entries))
    a = Analysis(words, base, n, srcmap, ranges)
    a.run(base, 0xFFFF)
    done = set()
    while True:
        todo = [e for e in a.spawns if e not in done]
        if not todo:
            break
        for e in todo:
            done.add(e)
            a.run(e, a.spawns[e])
    return a, words, syms


def fmt(v):
    return "-inf" if v is None else str(v)


def certified(a):
    """True if the analysis found no deadline it could not prove."""
    if any(dl["late"] or dl["unchecked"] for dl in a.deadlines.values()):
        return False
    return not any("not derived from CNT" not in msg for _, msg in a.problems)


def report(path, a):
    name = os.path.relpath(path, ROOT)
    bad = False
    print(f"{name}: {a.n} thread(s), {len(a.deadlines)} WAIT_CMP deadline(s)")
    for pc in sorted(a.deadlines):
        dl = a.deadlines[pc]
        src = a.src.get(pc, "")
        if dl["unchecked"] and not dl["seen"]:
            print(f"  {pc:#05x}  {src:28s} target not from CNT: unchecked")
            bad = True
            continue
        lo, hi = dl["lo"], dl["hi"]
        hi_s = "inf" if hi is None else str(hi)
        if dl["late"]:
            verdict = "MAY BE LATE"
            bad = True
        else:
            verdict = f"slack >= {lo - 1}"
        extra = ""
        if a.n > 1:
            extra = ", release exact" if dl["aligned"] else ", release may lag up to n-1 cycles"
        if dl["unchecked"]:
            extra += "; also reached with a target not from CNT"
            bad = True
        print(f"  {pc:#05x}  {src:28s} d at issue {fmt(lo)}..{hi_s}: {verdict}{extra}")
    for pc, msg in a.problems:
        if "not derived from CNT" in msg:
            continue
        print(f"  {pc:#05x}  {a.src.get(pc, ''):28s} {msg}")
        bad = True                     # a path the analysis could not follow
    return bad


def check_model(path, a, words, cycles, base=128, data=None):
    """Run the ISA model; every observed WAIT_CMP d must lie in the computed range."""
    from isa_model import IsaModel
    mem = {base + i: w for i, w in enumerate(words)}
    for i, v in enumerate(data or []):
        mem[i] = v
    m = IsaModel(asm.bootloader_words(), mem, uio_pad=lambda c: 0xFF, ui_pad=lambda c: 1)
    try:
        m.run(cycles)
    except NotImplementedError as e:
        return f"skipped ({e})"
    seen = set()
    for e in m.trace:
        if e[0] != "I":
            continue
        _, c, t, pc, w, st = e
        if w >> 11 != WAIT_CMP or pc not in a.deadlines:
            continue
        tgt = st[f"regs{t}"][(w >> 8) & 7]
        d = model_d(tgt - (c & M16))
        dl = a.deadlines[pc]
        lo, hi = dl["lo"], dl["hi"]
        if (lo is not None and d < lo) or (hi is not None and d > hi):
            raise AssertionError(f"{path}: model WAIT_CMP at {pc:#05x}, cycle {c}: d = {d} "
                                 f"outside the analysed range {fmt(lo)}..{fmt(hi)}")
        seen.add(pc)
    return seen


def check_rtl_log(path, files):
    """Which deadlines of `files` the firmware tests reached on the RTL."""
    with open(path, encoding="utf-8") as f:
        log = json.load(f)
    total = reached = 0
    bad = False
    for fn in files:
        name = os.path.basename(fn)
        a0, _, _ = analyse(fn)
        sites = sorted(a0.deadlines)
        got = set()
        with open(fn, encoding="utf-8") as f:
            slow = "@slow-analysis" in f.read()
        for key, seen in log.get(name, {}).items():
            defines = {k: int(v) for k, v in (kv.split("=") for kv in key.split(","))} if key else None
            a = analyse(fn, None, defines)[0] if defines and not slow else a0
            for pc, (lo, _hi) in seen.items():
                pc = int(pc)
                dl = a.deadlines.get(pc)
                if dl is None:
                    print(f"  {name}: the log has a WAIT_CMP at {pc:#05x}, the analysis does not")
                    bad = True
                    continue
                if dl["lo"] is not None and lo < dl["lo"]:
                    print(f"  {name} [{key}]: {pc:#05x} d = {lo} on the RTL, bound {dl['lo']}")
                    bad = True
                if pc in sites:
                    got.add(pc)
        missing = [pc for pc in sites if pc not in got]
        total += len(sites)
        reached += len(got)
        print(f"  {name}: {len(got)} of {len(sites)} deadlines reached on the RTL"
              + (f"; not reached: {', '.join(f'{p:#05x}' for p in missing)}" if missing else ""))
        bad |= bool(missing)
    print(f"RTL cross-check: {reached} of {total} deadlines reached with the tests' peers, "
          f"none below the checker's bound" if not bad else
          f"RTL cross-check: {reached} of {total} deadlines reached, see above")
    return bad


def _uses_wait_cmp(path):
    with open(path, encoding="utf-8") as f:
        return "WAIT_CMP" in f.read()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--threads", type=int)
    ap.add_argument("-D", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--check-model", type=int, metavar="CYCLES")
    ap.add_argument("--rtl-log", metavar="FILE", help="test/deadlines_seen.json from the firmware tests")
    a = ap.parse_args()
    defines = {}
    for d in a.D:
        k, v = d.split("=", 1)
        defines[k] = int(v, 0)
    files = a.files
    if a.all:
        files = sorted(f for f in glob.glob(os.path.join(ROOT, "firmware", "*.s"))
                       if _uses_wait_cmp(f))
    if not files:
        ap.error("no input files")
    bad = False
    total = compared = 0
    for f in files:
        an, words, _ = analyse(f, a.threads, defines or None)
        bad |= report(f, an)
        if a.check_model:
            total += len(an.deadlines)
            n = check_model(f, an, words, a.check_model,
                            data=[ord(ch) for ch in "Hi!"] + [0])
            if isinstance(n, str):
                print(f"  model: {n}")
            else:
                compared += len(n)
                print(f"  model: {len(n)} of {len(an.deadlines)} deadlines issued in "
                      f"{a.check_model} cycles, all inside the ranges")
    if a.check_model:
        print(f"model cross-check: compared {compared} of {total} deadlines")
    if a.rtl_log:
        bad |= check_rtl_log(a.rtl_log, files)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
