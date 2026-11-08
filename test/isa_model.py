# SPDX-License-Identifier: Apache-2.0
"""Independent ISA-level reference model of msfcore v2.2.

Written from docs/isa.md, NOT from the RTL: there is no fetch pipeline,
no flush flag and no pc-hold trick here -- only instruction semantics plus the
documented cost rules:

  single mode : only T0 runs. Instruction = 1 cycle; taken branch/JMP/
                CALL/RET/JR = 2; a blocking op that stalls releases in a
                stall cycle and the next instruction issues in the following
                cycle.
  multi mode  : T0 and up to three of T1-T3 run; every cycle has an owner
                (the next running thread in the order T0, T1, T2, T3, after
                the previous owner) and every instruction = 1 slot; a stalled
                blocking op releases in one of its own slots. When TSTART /
                TSTOP changed the running set in cycle c, c+1 belongs to the
                thread that would have owned it before, if it still runs and
                was not restarted; otherwise to the next running thread after
                c's owner, and c+1 is a bubble for it.
  inputs      : a pad value in cycle t is seen by instructions in cycle t+2.
  reset       : cycle 0 is the reset bubble; ROM[0] issues in cycle 1.

The model is stepped one clock at a time (`step()`), so peripheral timing
(CRC busy) and per-cycle events (edges, CNT match, timeouts) are exact.
TX/RX engines and the capture engine are not modelled (the random tests
don't use them; they are checked by protocol decoders in test_peripherals.py
and by test_capture.py).

Trace: every issue appends ("I", cycle, thread, pc, word, state) and every
stall release appends ("R", cycle, thread, timed_out). `state` is the
architectural state at the *start* of the cycle (see snapshot()).
"""

M16 = 0xFFFF
M10 = 0x3FF                     # 10-bit PC and addresses (1024 words)

# opcodes
LDR, ADDS, SUBS, AND, OR, XOR, SHL, SHR, MOV, JMP, JZ, JNZ, JP, JN, NOP, EXT = range(16)
PIN_W, PIN_R, SETM, CLRM, CNT_R, INCR, DECR, IN_RD, CALL, RET, WAIT_EDGE, WAIT_CMP, \
    SHIFT_OUT, SHIFT_IN, MEM_W, LDH = range(16, 32)
# EXT sub-ops
(X_SRLD, X_SRRD, X_XFER, X_SERCFG, X_OUTW, X_MEMA, X_LD, X_ST, X_SWAP, X_JR, X_TMO,
 X_STAT, X_TXCFG, X_TXDIV, X_TXPUSH, X_TXWAIT, X_RXCFG, X_RXDIV, X_RXPOP, X_CRCCFG,
 X_CRCW, X_CRCRDL, X_CRCRDH, X_CRCSETL, X_CRCSETH, X_POLL, X_POLH, X_TSTART, X_TSTOP,
 X_TID, X_PW, X_PR) = range(32)
P_FIFO, P_FCFG, P_LATE = 0, 1, 7   # peripheral ports
P_CAPTURE = {2, 3, 4, 5, 6}     # capture engine, glitch filter: not modelled

BLOCKING_EXT = {X_LD, X_TXPUSH, X_TXWAIT, X_RXPOP, X_CRCW, X_CRCRDL}
UNMODELLED_EXT = {X_TXCFG, X_TXDIV, X_TXPUSH, X_TXWAIT, X_RXCFG, X_RXDIV, X_RXPOP}


class Thread:
    def __init__(self, tid):
        self.tid = tid
        self.regs = [0] * 8
        self.z = 0
        self.n = 0
        self.pc = 0
        self.tmo = 0xFFFF       # blocking-op timeout reload (per thread)
        self.ready_at = 0       # earliest cycle the next instruction may issue
        self.blk = None         # pending blocking op (dict) or None
        self.late = 0           # sticky: a WAIT_CMP found its target already passed


class IsaModel:
    def __init__(self, rom, sram=None, uio_pad=None, ui_pad=None):
        """rom: 128 words. sram: {addr: word}. *_pad(cycle) -> pad value."""
        self.rom = list(rom) + [0x7000] * (128 - len(rom))
        self.mem = [0] * 1024
        for a, w in (sram or {}).items():
            self.mem[a] = w & M16
        self.uio_pad = uio_pad or (lambda c: 0)
        self.ui_pad = ui_pad or (lambda c: 0)
        self.th = [Thread(i) for i in range(4)]
        self.th[0].ready_at = 1
        self.alive = 1             # running threads (bit mask; T0 always)
        self.own = 0             # owner of the current cycle
        self.bubble = False      # the current cycle's slot is a bubble
        self._restarted = None   # thread restarted by TSTART this cycle
        self.cycle = 0
        self.out = self.oe = self.uo = 0
        self.lguard = self.gpins = 0   # the late guard (port 7, bit 15)
        self.sr = 0
        self.sr_po, self.sr_pi, self.sr_lsb = 7, 7, 0
        self.waddr = 128
        self.fbase, self.fn = 64, 5     # FIFO: SRAM block and size 2 << fn
        self.fwp = self.frp = 0         # words pushed / popped since configured
        self.fdrop = 0
        self.crc = 0
        self.poly = 0
        self.crc_len_m1 = 7
        self.crc_refl = 0
        self.crc_dsr = 0
        self.crc_rem = 0
        self.trace = []

    # ---------------------------------------------------------------- inputs
    def uio(self, c=None):
        c = self.cycle if c is None else c
        return self.uio_pad(c - 2) & 0xFF

    def ui(self, c=None):
        c = self.cycle if c is None else c
        return self.ui_pad(c - 2) & 0xFF

    @property
    def cnt(self):
        return self.cycle & M16

    def crc_busy(self):
        return self.crc_rem != 0

    @property
    def multi(self):
        return (self.alive & 0b1110) != 0

    def running(self, tid):
        return (self.alive >> tid) & 1 == 1

    @staticmethod
    def next_after(a, run):
        """The next running thread after a (cyclically, a itself if alone)."""
        for k in range(1, 5):
            x = (a + k) & 3
            if (run >> x) & 1:
                return x

    def oe_off(self):
        """The late guard: the pins held off the bus (isa.md section 6.5)."""
        if any(self.th[k].late and (self.lguard >> k) & 1 for k in range(4)):
            return self.gpins
        return 0

    def snapshot(self):
        snap = {"out": self.out, "oe": self.oe, "uo": self.uo, "sr": self.sr,
                "oe_pad": self.oe & ~self.oe_off() & 0xFF,
                "run": self.alive, "fcount": self.fcount(), "fdrop": self.fdrop,
                "fcfg": (self.fbase, self.fn)}
        for t in self.th:
            k = t.tid
            snap.update({f"regs{k}": list(t.regs), f"z{k}": t.z, f"n{k}": t.n,
                         f"tmo{k}": t.tmo})
        return snap

    # ---------------------------------------------------------------- FIFO
    def fsize(self):
        return 2 << self.fn

    def fcount(self):
        return self.fwp - self.frp

    def faddr(self, n):
        """SRAM address of the FIFO's n-th word (the block is size-aligned)."""
        m = self.fsize() - 1
        return (self.fbase & ~m & M10) | (n & m)

    def fetch(self, a):
        return self.rom[a] if a < 128 else self.mem[a]

    # ---------------------------------------------------------------- clock
    def step(self):
        c = self.cycle
        cur = self.own
        run_before = self.alive
        self._restarted = None
        # 1) event tracking for every stalled thread (every cycle)
        for t in self.th:
            b = t.blk
            if b is None or not self.running(t.tid):
                continue
            if b["kind"] == "edge":
                pin = ((self.ui(c) if b["bank"] else self.uio(c)) >> b["pin"]) & 1
                prev = b["prev"]
                cond = b["cond"]
                e = {1: (not prev) and pin, 2: prev and not pin, 4: not pin, 5: pin}.get(
                    cond, pin != prev)
                b["now"] = bool(e)
                b["prev"] = pin
            elif b["kind"] == "cmp":
                b["now"] = self.cnt == b["target"]
            b["tmo_now"] = b["tmo_en"] and b["tmo"] == 0

        # 2) the owning thread: release or issue
        t = self.th[cur]
        crc_step_pending = self.crc_rem != 0
        if t.blk is not None:
            self._try_release(t, c)           # (also in a bubble slot)
        elif not self.bubble and c >= t.ready_at:
            self._issue(t, c)

        # 2b) who owns the next cycle
        was = self.next_after(cur, run_before)
        if self.running(was) and was != self._restarted:
            self.own, self.bubble = was, False
        else:
            self.own, self.bubble = self.next_after(cur, self.alive), True

        # 3) end-of-cycle bookkeeping
        for th in self.th:
            b = th.blk
            if b is None:
                continue
            if b.get("issued") == c:       # issued this cycle: tracking starts next cycle
                continue
            if b["kind"] in ("edge", "cmp") and b.get("now"):
                b["hit"] = True
            if b["tmo_en"] and b["tmo"] != 0:
                b["tmo"] -= 1
        if crc_step_pending:
            self._crc_step()
        self.cycle += 1

    def _next_ready(self, t, c, redirect):
        if self.multi:
            t.ready_at = c + 1                # its next own slot (no redirect cost)
        else:
            t.ready_at = c + (2 if redirect else 1)

    # ---------------------------------------------------------------- CRC
    def _crc_step(self):
        if self.crc_refl:
            bit = self.crc_dsr & 1
            fb = (self.crc & 1) ^ bit
            self.crc = ((self.crc >> 1) ^ (self.poly if fb else 0)) & 0xFFFFFFFF
            self.crc_dsr >>= 1
        else:
            bit = (self.crc_dsr >> 15) & 1
            fb = ((self.crc >> 31) & 1) ^ bit
            self.crc = (((self.crc << 1) & 0xFFFFFFFF) ^ (self.poly if fb else 0)) & 0xFFFFFFFF
            self.crc_dsr = (self.crc_dsr << 1) & M16
        self.crc_rem -= 1

    def _crc_write(self, v):
        n = self.crc_len_m1 + 1
        self.crc_dsr = v if self.crc_refl else (v << (16 - n)) & M16
        self.crc_rem = n

    # ---------------------------------------------------------------- blocking ops
    def _resource_ready(self, kind):
        if kind == "ld":
            return True
        if kind in ("crcw", "crcrd"):
            return not self.crc_busy()
        raise NotImplementedError(f"blocking kind {kind} not modelled")

    def _complete(self, t, b):
        """Transfer performed when a blocking op completes successfully."""
        k = b["kind"]
        if k == "ld":
            if b["data"] is not None:       # None: a pop of an empty FIFO
                t.regs[b["rd"]] = b["data"]
        elif k == "crcw":
            self._crc_write(b["data"])
        elif k == "crcrd":
            t.regs[b["rd"]] = self.crc & M16

    def _try_release(self, t, c):
        b = t.blk
        if b["kind"] in ("edge", "cmp"):
            ok = b.get("hit", False) or b.get("now", False)
        else:
            ok = self._resource_ready(b["kind"])
        if b.get("late"):                   # target passed at issue: release now, Z=1
            ok = False
            t.late = 1
        if ok or b["tmo_now"] or b.get("late"):
            if ok:
                self._complete(t, b)
            t.z = b.get("z", 0) if ok else 1
            t.blk = None
            self.trace.append(("R", c, t.tid, 0 if ok else 1))
            self._next_ready(t, c, False)

    def _block(self, t, c, **kw):
        b = dict(kw, issued=c, hit=False, now=False, tmo=t.tmo, tmo_en=t.tmo != 0)
        t.blk = b

    # ---------------------------------------------------------------- issue
    def _issue(self, t, c):
        a = t.pc
        w = self.fetch(a)
        self.trace.append(("I", c, t.tid, a, w, self.snapshot()))
        op = w >> 11
        fa, fb, fc = (w >> 8) & 7, (w >> 5) & 7, (w >> 2) & 7
        imm8 = w & 0xFF
        R = t.regs
        nxt = (a + 1) & M10
        redirect = False
        self._ovr = None

        def setzn(v):
            t.z = int((v & M16) == 0)
            t.n = (v >> 15) & 1

        if op == LDR:
            R[fa] = imm8
        elif ADDS <= op <= MOV:
            x, y = R[fb], R[fc]
            v = {ADDS: x + y, SUBS: x - y, AND: x & y, OR: x | y, XOR: x ^ y,
                 SHL: x << fc, SHR: x >> fc, MOV: x}[op] & M16
            R[fa] = v
            if op in (ADDS, SUBS):
                setzn(v)
        elif op == JMP:
            nxt, redirect = w & M10, True
        elif JZ <= op <= JN:
            take = {JZ: t.z, JNZ: not t.z, JP: not t.n, JN: t.n}[op]
            if take:
                rel = (w >> 2) & 0x1FF
                rel = rel - 512 if rel & 0x100 else rel
                nxt, redirect = (a + 1 + rel) & M10, True
        elif op == NOP:
            pass
        elif op == PIN_W:
            self.out = R[fa] & 0xFF
        elif op == PIN_R:
            R[fa] = self.uio(c)
        elif op == SETM:
            sel = fa
            if sel == 0: self.oe |= imm8
            elif sel == 1: self.out |= imm8
            elif sel == 2: self.out ^= imm8
            elif sel == 3: self.uo |= imm8
            elif sel == 4: self.uo ^= imm8
            elif sel == 5: self.oe ^= imm8
        elif op == CLRM:
            sel = fa
            if sel == 0: self.oe &= ~imm8 & 0xFF
            elif sel == 1: self.out &= ~imm8 & 0xFF
            elif sel == 2: self.out = imm8
            elif sel == 3: self.uo &= ~imm8 & 0xFF
            elif sel == 4: self.uo = imm8
            elif sel == 5: self.oe = imm8
        elif op == CNT_R:
            R[fa] = self.cnt
        elif op == INCR:
            R[fa] = (R[fa] + 1) & M16
            setzn(R[fa])
        elif op == DECR:
            R[fa] = (R[fa] - 1) & M16
            setzn(R[fa])
        elif op == IN_RD:
            R[fa] = self.ui(c)
        elif op == CALL:
            R[7] = (a + 1) & M10
            nxt, redirect = w & M10, True
        elif op == RET:
            nxt, redirect = R[7] & M10, True
        elif op == WAIT_EDGE:
            bank, pin = (imm8 >> 3) & 1, imm8 & 7
            p = ((self.ui(c) if bank else self.uio(c)) >> pin) & 1
            self._block(t, c, kind="edge", bank=bank, pin=pin, cond=(imm8 >> 4) & 7, prev=p)
        elif op == WAIT_CMP:
            # isa.md section 5: a target 0-255 cycles at or before CNT is late
            self._block(t, c, kind="cmp", target=R[fa],
                        late=((self.cnt - R[fa]) & M16) < 256)
        elif op == SHIFT_OUT:
            self._sr_out()
            self.sr = (((R[fa] & 1) << 15) | (self.sr >> 1)) if self.sr_lsb else \
                (((self.sr << 1) | (R[fa] & 1)) & M16)
        elif op == SHIFT_IN:
            self._sr_in(c)
        elif op == MEM_W:
            self.mem[self.waddr] = R[fa]
            self.waddr = (self.waddr + 1) & M10
        elif op == LDH:
            R[fa] = (imm8 << 8) | (R[fa] & 0xFF)
        elif op == EXT:
            nxt, redirect = self._ext(t, c, w, fa, fb, nxt, redirect)
        t.pc = nxt
        if t.blk is not None and t.blk.get("issued") == c:
            pass                            # ready_at is set by the release
        elif self._ovr is not None:
            t.ready_at = self._ovr
        else:
            self._next_ready(t, c, redirect)

    def _sr_out(self):
        bit = (self.sr & 1) if self.sr_lsb else (self.sr >> 15) & 1
        self.out = (self.out & ~(1 << self.sr_po) | (bit << self.sr_po)) & 0xFF

    def _sr_in(self, c):
        b = (self.uio(c) >> self.sr_pi) & 1
        self.sr = ((b << 15) | (self.sr >> 1)) if self.sr_lsb else (((self.sr << 1) | b) & M16)

    def _ext(self, t, c, w, ra, rb, nxt, redirect):
        sub = w & 0x1F
        R = t.regs
        if sub in UNMODELLED_EXT:
            raise NotImplementedError(f"EXT sub {sub} (SERDES) not modelled")
        if sub == X_SRLD:
            self.sr = R[rb]
        elif sub == X_SRRD:
            R[ra] = self.sr
        elif sub == X_XFER:
            self._sr_out()
            self._sr_in(c)
        elif sub == X_SERCFG:
            v = R[rb]
            self.sr_po, self.sr_pi, self.sr_lsb = v & 7, (v >> 3) & 7, (v >> 6) & 1
        elif sub == X_OUTW:
            self.uo = R[rb] & 0xFF
        elif sub == X_MEMA:
            self.waddr = R[rb] & M10
        elif sub == X_LD:
            self._block(t, c, kind="ld", rd=ra, data=self.mem[R[rb] & M10])
        elif sub == X_ST:
            self.mem[R[ra] & M10] = R[rb]
        elif sub == X_SWAP:
            v = R[rb]
            R[ra] = ((v & 0xFF) << 8) | (v >> 8)
        elif sub == X_JR:
            nxt, redirect = R[rb] & M10, True
        elif sub == X_TMO:
            t.tmo = R[rb]
        elif sub == X_STAT:
            R[ra] = (t.late << 14) | ((t.tid >> 1) << 13) | (int(self.running(3)) << 12) | \
                (int(self.running(2)) << 11) | \
                (int(self.fcount() == self.fsize()) << 8) | (int(self.fcount() > 0) << 7) | \
                ((t.tid & 1) << 6) | (int(self.running(1)) << 5) | (int(self.crc_busy()) << 4)
        elif sub == X_PW:
            port = ra                       # PW #port, rb
            if port == P_FIFO:
                if self.fcount() < self.fsize():
                    self.mem[self.faddr(self.fwp)] = R[rb]
                    self.fwp += 1
                    t.z = 0
                else:
                    self.fdrop = (self.fdrop + 1) & 0x7F
                    t.z = 1
            elif port == P_FCFG:
                self.fbase, self.fn = R[rb] & M10, (R[rb] >> 10) & 7
                self.fwp = self.frp = self.fdrop = 0
            elif port == P_LATE:            # clear the LATE flag of each thread named,
                if R[rb] & 0x8000:              # or (bit 15) load the late guard
                    self.lguard, self.gpins = (R[rb] >> 8) & 0xF, R[rb] & 0xFF
                else:
                    for k in range(4):
                        if (R[rb] >> k) & 1:
                            self.th[k].late = 0
            elif port in P_CAPTURE:
                raise NotImplementedError("capture engine not modelled")
        elif sub == X_PR:
            port = rb                       # PR ra, #port
            if port == P_FIFO:              # a load from the read slot
                if self.fcount() > 0:
                    self._block(t, c, kind="ld", rd=ra, data=self.mem[self.faddr(self.frp)])
                    self.frp += 1
                else:
                    self._block(t, c, kind="ld", rd=ra, data=None, z=1)
            elif port == P_FCFG:
                R[ra] = (self.fdrop << 9) | self.fcount()
            elif port == P_LATE:
                R[ra] = (self.gpins << 8) | (self.lguard << 4) | \
                    sum(self.th[k].late << k for k in range(4))
            elif port in P_CAPTURE:
                raise NotImplementedError("capture engine not modelled")
            else:
                R[ra] = 0
        elif sub == X_CRCCFG:
            self.crc_len_m1, self.crc_refl = R[rb] & 0xF, (R[rb] >> 4) & 1
        elif sub == X_CRCW:
            if self.crc_busy():
                self._block(t, c, kind="crcw", data=R[rb])
            else:
                self._crc_write(R[rb])
                t.z = 0
        elif sub == X_CRCRDL:
            if self.crc_busy():
                self._block(t, c, kind="crcrd", rd=ra)
            else:
                R[ra] = self.crc & M16
                t.z = 0
        elif sub == X_CRCRDH:
            R[ra] = self.crc >> 16
        elif sub == X_CRCSETL:
            self.crc = (self.crc & 0xFFFF0000) | R[rb]
        elif sub == X_CRCSETH:
            self.crc = (self.crc & 0xFFFF) | (R[rb] << 16)
        elif sub == X_POLL:
            self.poly = (self.poly & 0xFFFF0000) | R[rb]
        elif sub == X_POLH:
            self.poly = (self.poly & 0xFFFF) | (R[rb] << 16)
        elif sub == X_TSTART:
            tt = (ra & 3) or 1              # ra field: the thread (0 = T1)
            th = self.th[tt]
            target = R[rb] & M10
            th.regs = [0] * 8
            th.z = th.n = 0
            th.late = 0
            th.blk = None
            th.tmo = t.tmo                  # the starter's TMO
            if self.running(tt):
                self._restarted = tt
            self.alive |= 1 << tt
            if tt == t.tid:
                nxt = target                # restarting itself
            else:
                th.pc, th.ready_at = target, c + 1
        elif sub == X_TSTOP:
            tp = (ra & 3) or 1
            self.alive &= ~(1 << tp)
        elif sub == X_TID:
            R[ra] = t.tid
        return nxt, redirect

    # ---------------------------------------------------------------- run
    def run(self, cycles):
        for _ in range(cycles):
            self.step()
        return self.trace
