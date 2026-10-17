# SPDX-License-Identifier: Apache-2.0
"""Independent ISA-level reference model of msfcore v2.0.

Written from docs/sam/isa.md, NOT from the RTL: there is no fetch pipeline,
no flush flag and no pc-hold trick here -- only instruction semantics plus the
documented cost rules:

  single mode : instruction = 1 cycle; taken branch/JMP/CALL/RET/JR = 2;
                a blocking op that stalls releases in a stall cycle and the
                next instruction issues in the following cycle.
  dual mode   : cycles alternate T0/T1; every instruction = 1 slot; a
                stalled blocking op releases in one of its own slots.
  inputs      : a pad value in cycle t is seen by instructions in cycle t+2.
  reset       : cycle 0 is the reset bubble; ROM[0] issues in cycle 1.

The model is stepped one clock at a time (`step()`), so peripheral timing
(CRC busy) and per-cycle events (edges, CNT match, timeouts) are exact.
TX/RX engines are not modelled (the random tests don't use them; they are
checked by protocol decoders in test_peripherals.py).

Trace: every issue appends ("I", cycle, thread, pc, word, state) and every
stall release appends ("R", cycle, thread, timed_out). `state` is the
architectural state at the *start* of the cycle (see snapshot()).
"""

M16 = 0xFFFF
M9 = 0x1FF

# opcodes
LDR, ADDS, SUBS, AND, OR, XOR, SHL, SHR, MOV, JMP, JZ, JNZ, JP, JN, NOP, EXT = range(16)
PIN_W, PIN_R, SETM, CLRM, CNT_R, INCR, DECR, IN_RD, CALL, RET, WAIT_EDGE, WAIT_CMP, \
    SHIFT_OUT, SHIFT_IN, MEM_W, LDH = range(16, 32)
# EXT sub-ops
(X_SRLD, X_SRRD, X_XFER, X_SERCFG, X_OUTW, X_MEMA, X_LD, X_ST, X_SWAP, X_JR, X_TMO,
 X_STAT, X_TXCFG, X_TXDIV, X_TXPUSH, X_TXWAIT, X_RXCFG, X_RXDIV, X_RXPOP, X_CRCCFG,
 X_CRCW, X_CRCRDL, X_CRCRDH, X_CRCSETL, X_CRCSETH, X_POLL, X_POLH, X_TSTART, X_TSTOP,
 X_TID) = range(30)

BLOCKING_EXT = {X_LD, X_TXPUSH, X_TXWAIT, X_RXPOP, X_CRCW, X_CRCRDL}
UNMODELLED_EXT = {X_TXCFG, X_TXDIV, X_TXPUSH, X_TXWAIT, X_RXCFG, X_RXDIV, X_RXPOP}


class Thread:
    def __init__(self, tid):
        self.tid = tid
        self.regs = [0] * 8
        self.z = 0
        self.n = 0
        self.pc = 0
        self.ready_at = 0       # earliest cycle the next instruction may issue
        self.blk = None         # pending blocking op (dict) or None


class IsaModel:
    def __init__(self, rom, sram=None, uio_pad=None, ui_pad=None):
        """rom: 128 words. sram: {addr: word}. *_pad(cycle) -> pad value."""
        self.rom = list(rom) + [0x7000] * (128 - len(rom))
        self.mem = [0] * 512
        for a, w in (sram or {}).items():
            self.mem[a] = w & M16
        self.uio_pad = uio_pad or (lambda c: 0)
        self.ui_pad = ui_pad or (lambda c: 0)
        self.th = [Thread(0), Thread(1)]
        self.th[0].ready_at = 1
        self.dual = False
        self.dual_t0_parity = 0  # in dual mode, T0 owns cycles with this parity
        self.cycle = 0
        self.out = self.oe = self.uo = 0
        self.sr = 0
        self.sr_po, self.sr_pi, self.sr_lsb = 7, 7, 0
        self.waddr = 128
        self.tmo = 0xFFFF
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

    def owner(self, c):
        if not self.dual:
            return 0
        return 0 if (c & 1) == self.dual_t0_parity else 1

    def snapshot(self):
        return {
            "regs0": list(self.th[0].regs), "regs1": list(self.th[1].regs),
            "z0": self.th[0].z, "n0": self.th[0].n, "z1": self.th[1].z, "n1": self.th[1].n,
            "out": self.out, "oe": self.oe, "uo": self.uo, "sr": self.sr,
        }

    def fetch(self, a):
        return self.rom[a] if a < 128 else self.mem[a]

    # ---------------------------------------------------------------- clock
    def step(self):
        c = self.cycle
        cur = self.owner(c)
        # 1) event tracking for every stalled thread (every cycle)
        for t in self.th:
            b = t.blk
            if b is None or (t.tid == 1 and not self.dual):
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
            self._try_release(t, c)
        elif c >= t.ready_at:
            self._issue(t, c)

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
        if self.dual:
            t.ready_at = c + 2
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
        if ok or b["tmo_now"]:
            if ok:
                self._complete(t, b)
            t.z = 0 if ok else 1
            t.blk = None
            self.trace.append(("R", c, t.tid, 0 if ok else 1))
            self._next_ready(t, c, False)

    def _block(self, t, c, **kw):
        b = dict(kw, issued=c, hit=False, now=False, tmo=self.tmo, tmo_en=self.tmo != 0)
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
        nxt = (a + 1) & M9
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
            nxt, redirect = w & M9, True
        elif JZ <= op <= JN:
            take = {JZ: t.z, JNZ: not t.z, JP: not t.n, JN: t.n}[op]
            if take:
                rel = (w >> 2) & M9
                rel = rel - 512 if rel & 0x100 else rel
                nxt, redirect = (a + 1 + rel) & M9, True
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
            R[7] = (a + 1) & M9
            nxt, redirect = w & M9, True
        elif op == RET:
            nxt, redirect = R[7] & M9, True
        elif op == WAIT_EDGE:
            bank, pin = (imm8 >> 3) & 1, imm8 & 7
            p = ((self.ui(c) if bank else self.uio(c)) >> pin) & 1
            self._block(t, c, kind="edge", bank=bank, pin=pin, cond=(imm8 >> 4) & 7, prev=p)
        elif op == WAIT_CMP:
            self._block(t, c, kind="cmp", target=R[fa])
        elif op == SHIFT_OUT:
            self._sr_out()
            self.sr = (((R[fa] & 1) << 15) | (self.sr >> 1)) if self.sr_lsb else \
                (((self.sr << 1) | (R[fa] & 1)) & M16)
        elif op == SHIFT_IN:
            self._sr_in(c)
        elif op == MEM_W:
            self.mem[self.waddr] = R[fa]
            self.waddr = (self.waddr + 1) & M9
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
            self.waddr = R[rb] & M9
        elif sub == X_LD:
            self._block(t, c, kind="ld", rd=ra, data=self.mem[R[rb] & M9])
        elif sub == X_ST:
            self.mem[R[ra] & M9] = R[rb]
        elif sub == X_SWAP:
            v = R[rb]
            R[ra] = ((v & 0xFF) << 8) | (v >> 8)
        elif sub == X_JR:
            nxt, redirect = R[rb] & M9, True
        elif sub == X_TMO:
            self.tmo = R[rb]
        elif sub == X_STAT:
            R[ra] = (t.tid << 6) | (int(self.dual) << 5) | (int(self.crc_busy()) << 4)
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
            t1 = self.th[1]
            t1.regs = [0] * 8
            t1.z = t1.n = 0
            t1.blk = None
            target = R[rb] & M9
            if not self.dual:
                # dual from c+1: T0 owns c+1 (issues there), T1 owns c+2
                self.dual = True
                self.dual_t0_parity = (c + 1) & 1
                t1.pc, t1.ready_at = target, c + 2
                self._ovr = c + 1
            elif t.tid == 0:
                # T1's slot c+1 carries a stale word: T1 restarts at c+3
                t1.pc, t1.ready_at = target, c + 3
            else:
                nxt = target                # T1 restarts itself at its next slot
        elif sub == X_TSTOP:
            if self.dual:
                self.dual = False
                if t.tid == 0:
                    self._ovr = c + 2       # T0 pays one bubble cycle
                else:
                    self.th[0].ready_at = min(self.th[0].ready_at, c + 1)
        elif sub == X_TID:
            R[ra] = t.tid
        return nxt, redirect

    # ---------------------------------------------------------------- run
    def run(self, cycles):
        for _ in range(cycles):
            self.step()
        return self.trace
