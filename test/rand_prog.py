# SPDX-License-Identifier: Apache-2.0
"""Constrained-random msfcore program generator (for test_random_diff.py).

Programs are structured so they keep making progress: bounded DECR/JNZ loops,
forward conditional skips, CALL/RET to leaf subroutines (R7 is reserved for
CALL), JR through a computed address, WAITs bounded by a small TMO, data-RAM
traffic confined to SRAM 0..127, 960..1023, 1344..1407 and 1984..2047 (never code, except the "patch":
a store of a fresh instruction into the word exactly three or more instructions
ahead, isa.md section 1), FIFO pushes /
pops / status reads and re-configurations (block kept inside those regions),
code in both SRAM banks (T1 at 500, across the 511/512 boundary; T0's
subroutines at 505 or 700, so CALL / RET / JR cross banks), CRC bursts bracketed
by blocking CRC_RDL so no CRC register is touched while the unit is busy. In
dual mode T0 starts/stops/restarts T1, both threads hammer shared pins/SR/
data RAM/FIFO, and each thread may set its own TMO.
"""
import random

from assembler import assemble

ALU3 = ["ADDS", "SUBS", "AND", "OR", "XOR"]
MASKS = ["OE_SET", "PIN_SET", "PIN_TGL", "UO_SET", "UO_TGL", "OE_TGL",
         "OE_CLR", "PIN_CLR", "PIN_WI", "UO_CLR", "UO_WI", "OE_WI"]
CONDS = ["any", "rise", "fall", "low", "high"]


class Gen:
    def __init__(self, seed, dual=False, threads=None):
        self.r = random.Random(seed)
        self.threads = threads or (2 if dual else 1)
        self.dual = self.threads >= 2
        self.lab = 0
        self.lines = []
        self.subs = []          # leaf subroutine bodies
        # the CRC unit this code's thread may use: 0 = set 0 (T0), 1 = set 1 (T1: ESEL #1
        # around each burst), None = none (T2, T3), so no two threads share a unit
        self.crc_set = 0
        self.in_patch = False

    def L(self, s):
        self.lines.append(s)

    def data_addr(self, n):
        """Start of n data words: 64-word regions at 0, 960, 1344 and 1984 (code never
        goes there; the last two are banks 2 and 3, v4)."""
        return self.r.choice([0, 960, 1344, 1984]) + self.r.randrange(64 if n == 1 else 64 - n)

    def label(self, p="l"):
        self.lab += 1
        return f"{p}{self.lab}"

    def reg(self, avoid7=True):
        return f"R{self.r.randrange(7 if avoid7 else 8)}"

    # ------------------------------------------------------------------ pieces
    def patch(self):
        """Self-modifying code at the edge of what isa.md section 1 allows: store a
        fresh instruction into a word that is the third instruction after the store (or
        later), then run it. The word is one of the thread's own registers' simple
        ops, so it changes state visibly but not the control flow."""
        r = self.r
        a, b = self.reg(), self.reg()
        while b == a:
            b = self.reg()
        x, y, z = self.reg(), self.reg(), self.reg()
        word = assemble(r.choice([
            f"LDR {x}, #{r.randrange(256)}", f"INCR {x}", f"DECR {x}", f"ADDS {x}, {y}, {z}",
            f"XOR {x}, {y}, {z}", f"MOV {x}, {y}", f"SHL {x}, {y}, #{r.randrange(8)}",
            f"PIN_W {x}", f"{r.choice(MASKS)} {r.randrange(256)}", f"CNT_R {x}", "NOP"]))[0]
        lab = self.label("p")
        self.L(f"LDI {a}, {lab}")
        self.L(f"LDI {b}, {word}")
        self.L(f"ST {a}, {b}")
        self.in_patch = True
        for _ in range(r.randrange(2, 4)):      # two or more instructions in between
            self.simple(crc_ok=False)
        self.in_patch = False
        self.L(f"{lab}:")
        self.L("NOP")                           # the word that is replaced

    def fill(self):
        """One instruction that is neither a pin write nor a port access: it can sit between
        AT and the write it times, and inside a window on port page 1."""
        r = self.r
        a, b, c = self.reg(), self.reg(), self.reg()
        return r.choice([f"{r.choice(ALU3)} {a}, {b}, {c}", f"LDR {a}, #{r.randrange(256)}",
                         f"MOV {a}, {b}", "NOP", f"INCR {a}", f"CNT_R {a}",
                         f"SHL {a}, {b}, #{r.randrange(8)}"])

    def pin_write(self):
        r = self.r
        return r.choice([f"PIN_W {self.reg()}", f"{r.choice(MASKS)} {r.randrange(256)}",
                         f"{r.choice(MASKS)} {r.randrange(256)}", f"OUT_W {self.reg()}",
                         f"SHIFT_OUT {self.reg()}", "XFER"])

    def timed(self):
        """A timed-pin window (isa.md section 6.8) on port page 1, balanced by ESEL #0:
        either AT with a time near CNT (the write arrives early, exactly on time, or late,
        and is held, applied or refused) or the reply idiom, WAIT_EDGE; TS; ADDS; AT; write.
        Sometimes two windows share a slot, so that AT finds it occupied."""
        r = self.r
        self.L("ESEL #2")
        for _ in range(r.choice([1, 1, 1, 2])):
            ta, tb = self.reg(), self.reg()
            while tb == ta:
                tb = self.reg()
            if r.random() < 0.3:                      # reply to an edge: time = TS + delta
                self.L(f"WAIT_EDGE {r.randrange(8)}, {r.choice(CONDS)}, {r.choice(['uio', 'ui'])}")
                self.L(f"TS {tb}")
                self.L(f"LDR {ta}, #{r.choice([1, 2, 3, 5, 8, 12, 20, 40, 100, 250])}")
                self.L(f"ADDS {ta}, {tb}, {ta}")
            else:                                     # a time around now
                d = r.choice([0, 0, 1, 2, 3, 4, 5, 6, 8, 11, 15, 25, 60, 200, 255, 256, 600,
                              65000, 65300, 65535])
                self.L(f"CNT_R {ta}")
                if d < 256:
                    self.L(f"LDR {tb}, #{d}")
                else:
                    self.L(f"LDI {tb}, {d}")
                self.L(f"ADDS {ta}, {ta}, {tb}")
            self.L(f"AT {ta}")
            for _ in range(r.choice([0, 0, 1, 2, 3])):
                self.L(self.fill())
            self.L(self.pin_write())
            for _ in range(r.choice([0, 0, 1, 2])):
                self.L(self.fill())
            if r.random() < 0.5:
                self.L(f"{r.choice(['TS', 'AT_STAT'])} {self.reg()}")
        self.L("ESEL #0")

    def simple(self, crc_ok=True):
        r = self.r
        k = r.randrange(32)
        a, b, c = self.reg(), self.reg(), self.reg()
        if k in (30, 31) and not self.in_patch:
            self.timed()
        elif k == 25 and not self.in_patch:
            self.patch()
        elif k < 4:
            self.L(f"LDR {a}, #{r.randrange(256)}")
        elif k < 6:
            self.L(f"LDH {a}, #{r.randrange(256)}")
        elif k < 10:
            self.L(f"{r.choice(ALU3)} {a}, {b}, {c}")
        elif k == 10:
            self.L(f"{r.choice(['SHL', 'SHR'])} {a}, {b}, #{r.randrange(8)}")
        elif k == 11:
            self.L(f"MOV {a}, {b}")
        elif k == 12:
            self.L(f"{r.choice(['INCR', 'DECR'])} {a}")
        elif k == 13:
            self.L(f"PIN_W {a}")
        elif k == 14:
            self.L(f"{r.choice(MASKS)} {r.randrange(256)}")
        elif k == 15:
            self.L(f"{r.choice(['PIN_R', 'IN_RD', 'CNT_R'])} {a}")
        elif k == 16:
            self.L(f"OUT_W {a}")
        elif k == 17:
            self.L(r.choice([f"SR_LD {a}", f"SR_RD {a}", "XFER", "SHIFT_IN", f"SHIFT_OUT {a}"]))
        elif k == 18:
            self.L(f"LDR {a}, #{r.randrange(128)}")
            self.L(f"SER_CFG {a}")
        elif k == 19:
            self.L(f"LDI {a}, {self.data_addr(1)}")
            self.L(r.choice([f"LD {b}, {a}", f"ST {a}, {b}"]))
        elif k == 20:
            n = r.randrange(1, 4)
            self.L(f"LDI {a}, {self.data_addr(n)}")
            self.L(f"MEM_A {a}")
            for _ in range(n):
                self.L(f"MEM_W {self.reg()}")
        elif k == 21:
            self.L(f"SWAP {a}, {b}")
        elif k == 22:
            self.L(f"{r.choice(['STAT', 'TID'])} {a}")
        elif k == 23:
            self.L("NOP")
        elif k == 24 and crc_ok and self.crc_set is not None:
            self.crc_burst()
        elif k in (26, 27):
            self.L(f"FIFO_PUT {a}")
        elif k == 28:
            self.L(f"FIFO_GET {a}")
        elif k == 29:
            if r.random() < 0.8:
                self.L(f"FIFO_STAT {a}")
            else:                       # size 2..64, block inside a data region
                base = self.data_addr(1)
                # FIFO_CFG: base[9:0] | n << 10 | base[10] << 13 (isa.md section 6.5)
                self.L(f"LDI {a}, {(base & 1023) | r.randrange(6) << 10 | (base >> 10) << 13}")
                self.L(f"FIFO_CFG {a}")
        else:
            self.L(f"ADDS {a}, {b}, {c}")

    def crc_burst(self):
        r = self.r
        if self.crc_set == 1:
            self.L("ESEL #1")                         # engine set 1, port page 0
        a, b = self.reg(), self.reg()
        self.L(f"CRC_RDL {a}")                        # wait until idle
        self.L(f"LDR {a}, #{r.randrange(32)}")
        self.L(f"CRC_CFG {a}")
        for ins in ("CRC_SETL", "CRC_SETH", "CRC_POLL", "CRC_POLH"):
            if r.random() < 0.5:
                self.L(f"LDR {a}, #{r.randrange(256)}")
                self.L(f"LDH {a}, #{r.randrange(256)}")
                self.L(f"{ins} {a}")
        for _ in range(r.randrange(1, 4)):
            self.L(f"CRC_W {self.reg()}")
            if r.random() < 0.5:
                self.in_patch = True                  # no patch or timed window inside a burst
                self.simple(crc_ok=False)
                self.in_patch = False
        self.L(f"CRC_RDL {b}")
        self.L(f"CRC_RDH {a}")
        if self.crc_set == 1:
            self.L("ESEL #0")

    def wait(self):
        r = self.r
        a, b = self.reg(), self.reg()
        if r.random() < 0.5:
            if r.random() < 0.25:       # the late guard (v3): some threads, some pins
                self.L(f"LDI {b}, {0x8000 | r.randrange(16) << 8 | r.randrange(256)}")
                self.L(f"LATE_CLR {b}")
            self.L(f"CNT_R {a}")
            self.L(f"LDR {b}, #{r.randrange(2, 60) if r.random() < 0.85 else 255}")
            # a target behind CNT: late (released at once, LATE set) or, if far
            # enough behind, a long wait ending by timeout
            self.L(f"{'SUBS' if r.random() < 0.15 else 'ADDS'} {a}, {a}, {b}")
            self.L(f"WAIT_CMP {a}")
            if r.random() < 0.3:
                self.L(f"{r.choice(['LATE_RD', 'STAT'])} {b}")
            if r.random() < 0.2:
                self.L(f"LATE_CLR {b}")
        else:
            self.L(f"WAIT_EDGE {r.randrange(8)}, {r.choice(CONDS)}, {r.choice(['uio', 'ui'])}")
        if r.random() < 0.5:
            sk = self.label("w")
            self.L(f"JZ {sk}")
            self.simple(crc_ok=False)
            self.L(f"{sk}:")

    def block(self, depth, n):
        r = self.r
        for _ in range(n):
            if self.threads == 4 and r.random() < 0.04:
                self.thread_ctl()
                continue
            k = r.random()
            if k < 0.55 or depth > 1:
                self.simple()
            elif k < 0.68:
                self.wait()
            elif k < 0.78:
                # bounded loop (own counter register, not touched in the body)
                cnt = f"R{r.randrange(7)}"
                top = self.label("loop")
                self.L(f"LDR {cnt}, #{r.randrange(1, 5)}")
                self.L(f"{top}:")
                saved = self.reg
                self.reg = lambda avoid7=True, c=cnt: r.choice([f"R{i}" for i in range(7) if f"R{i}" != c])
                self.block(depth + 1, r.randrange(1, 5))
                self.reg = saved
                self.L(f"DECR {cnt}")
                self.L(f"JNZ {top}")
            elif k < 0.88:
                sk = self.label("s")
                a, b = self.reg(), self.reg()
                self.L(f"SUBS {a}, {a}, {b}")
                self.L(f"{r.choice(['JZ', 'JNZ', 'JP', 'JN'])} {sk}")
                self.block(depth + 1, r.randrange(1, 4))
                self.L(f"{sk}:")
            elif k < 0.94 and self.subs is not None:
                name = self.label("sub")
                self.subs.append(name)
                self.L(f"CALL {name}")
            else:
                tgt = self.label("jr")
                a = self.reg()
                self.L(f"LDI {a}, {tgt}")
                self.L(f"JR {a}")
                self.simple(crc_ok=False)       # skipped
                self.L(f"{tgt}:")

    def thread_ctl(self):
        """Four-thread programs: stop or (re)start one of T1-T3."""
        r = self.r
        k = r.randrange(1, 4)
        if r.random() < 0.4:
            self.L(f"TSTOP #{k}")
        else:
            self.L(f"LDI R6, t{k}_entry")
            self.L(f"TSTART #{k}, R6")

    def thread_code(self, k, org):
        """Entry code of thread k (1-3): its own TMO sometimes, a block, and
        it may stop or restart itself."""
        r = self.r
        self.subs = []
        self.crc_set = 1 if k == 1 else None
        self.L(f".org {org}")
        self.L(f"t{k}_entry:")
        if r.random() < 0.5:        # its own timeout (else the starter's)
            b = self.reg()
            self.L(f"LDR {b}, #{r.choice([0, r.randrange(10, 80)])}")
            self.L(f"TMO {b}")
        self.block(1, r.randrange(5, 15))
        if r.random() < 0.3:
            self.L("TSTOP" if k == 1 else f"TSTOP #{k}")
        if r.random() < 0.3:
            self.L(f"LDI R5, t{k}_entry")
            self.L("TSTART R5" if k == 1 else f"TSTART #{k}, R5")
        self.L(f"JMP t{k}_entry")
        subs, self.subs = self.subs, None
        for name in subs:
            self.L(f"{name}:")
            self.block(2, r.randrange(1, 3))
            self.L("RET")

    # ------------------------------------------------------------------ programs
    def program(self, n=40):
        r = self.r
        a = self.reg()
        self.L("start:")
        self.L(f"LDR {a}, #{r.choice([0, r.randrange(20, 120)])}")   # 0 = no timeout
        if r.random() < 0.8:
            self.L(f"TMO {a}")
        if self.dual:
            self.L("LDI R6, t1_entry")
            self.L("TSTART R6")
            if self.threads == 4:
                for k in (2, 3):
                    self.L(f"LDI R6, t{k}_entry")
                    self.L(f"TSTART #{k}, R6")
            if r.random() < 0.5:        # T0's timeout changes; T1 keeps its own
                b = self.reg()
                self.L(f"LDR {b}, #{r.choice([0, r.randrange(20, 120)])}")
                self.L(f"TMO {b}")
        self.block(0, n)
        if self.dual and r.random() < 0.5:
            self.L("TSTOP")
            self.block(0, 4)
            self.L("LDI R6, t1_entry")
            self.L("TSTART R6")
        self.L("JMP start")
        t0_subs = self.subs
        if self.threads == 4:
            for k, org in ((1, 500), (2, 1020), (3, 1530)):   # across 511/512, 1023/1024, 1535/1536
                self.thread_code(k, org)
        elif self.dual:
            self.subs = []
            self.crc_set = 1
            self.L(".org 500")
            self.L("t1_entry:")
            if r.random() < 0.5:        # T1's own timeout (else the one T0 had)
                b = self.reg()
                self.L(f"LDR {b}, #{r.choice([0, r.randrange(10, 80)])}")
                self.L(f"TMO {b}")
            self.block(1, r.randrange(5, 15))
            if r.random() < 0.3:
                self.L("TSTOP")
            if r.random() < 0.3:
                self.L("LDI R5, t1_entry")
                self.L("TSTART R5")
            self.L("JMP t1_entry")
            subs, self.subs = self.subs, None
            for s in subs:
                self.L(f"{s}:")
                self.block(2, r.randrange(1, 3))
                self.L("RET")
        self.subs, self.crc_set = None, 0
        self.L(f".org {1800 if self.threads == 4 else 700 if self.dual else 505}")
        for s in t0_subs:
            self.L(f"{s}:")
            self.block(2, r.randrange(1, 4))
            self.L("RET")
        return "\n".join(self.lines)


def input_schedule(seed, cycles, ui_boot=1):
    """Per-cycle pad values: uio and ui toggle at random intervals."""
    r = random.Random(seed ^ 0x5A5A)
    uio, ui = [], []
    u, v = 0, ui_boot
    for c in range(cycles):
        if c > 8 and r.random() < 0.08:
            u ^= 1 << r.randrange(8)
        if c > 8 and r.random() < 0.05:
            v ^= 1 << r.randrange(8)
        uio.append(u)
        ui.append(v)
    return uio, ui
