# SPDX-License-Identifier: Apache-2.0
"""Constrained-random msfcore program generator (for test_random_diff.py).

Programs are structured so they keep making progress: bounded DECR/JNZ loops,
forward conditional skips, CALL/RET to leaf subroutines (R7 is reserved for
CALL), JR through a computed address, WAITs bounded by a small TMO, data-RAM
traffic confined to SRAM 0..127 (never code), CRC bursts bracketed by blocking
CRC_RDL so no CRC register is touched while the unit is busy. In dual mode T0
starts/stops/restarts T1, both threads hammer shared pins/SR/data RAM.
"""
import random

ALU3 = ["ADDS", "SUBS", "AND", "OR", "XOR"]
MASKS = ["OE_SET", "PIN_SET", "PIN_TGL", "UO_SET", "UO_TGL", "OE_TGL",
         "OE_CLR", "PIN_CLR", "PIN_WI", "UO_CLR", "UO_WI", "OE_WI"]
CONDS = ["any", "rise", "fall", "low", "high"]


class Gen:
    def __init__(self, seed, dual=False):
        self.r = random.Random(seed)
        self.dual = dual
        self.lab = 0
        self.lines = []
        self.subs = []          # leaf subroutine bodies
        self.no_crc = False     # T1 code never touches the (shared) CRC unit

    def L(self, s):
        self.lines.append(s)

    def label(self, p="l"):
        self.lab += 1
        return f"{p}{self.lab}"

    def reg(self, avoid7=True):
        return f"R{self.r.randrange(7 if avoid7 else 8)}"

    # ------------------------------------------------------------------ pieces
    def simple(self, crc_ok=True):
        r = self.r
        k = r.randrange(26)
        a, b, c = self.reg(), self.reg(), self.reg()
        if k < 4:
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
            addr = r.randrange(128)
            self.L(f"LDR {a}, #{addr}")
            self.L(r.choice([f"LD {b}, {a}", f"ST {a}, {b}"]))
        elif k == 20:
            n = r.randrange(1, 4)
            self.L(f"LDR {a}, #{r.randrange(128 - n)}")
            self.L(f"MEM_A {a}")
            for _ in range(n):
                self.L(f"MEM_W {self.reg()}")
        elif k == 21:
            self.L(f"SWAP {a}, {b}")
        elif k == 22:
            self.L(f"{r.choice(['STAT', 'TID'])} {a}")
        elif k == 23:
            self.L("NOP")
        elif k == 24 and crc_ok and not self.no_crc:
            self.crc_burst()
        else:
            self.L(f"ADDS {a}, {b}, {c}")

    def crc_burst(self):
        r = self.r
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
                self.simple(crc_ok=False)
        self.L(f"CRC_RDL {b}")
        self.L(f"CRC_RDH {a}")

    def wait(self):
        r = self.r
        a, b = self.reg(), self.reg()
        if r.random() < 0.5:
            self.L(f"CNT_R {a}")
            self.L(f"LDR {b}, #{r.randrange(2, 60) if r.random() < 0.85 else 255}")
            self.L(f"ADDS {a}, {a}, {b}")
            self.L(f"WAIT_CMP {a}")
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
        self.block(0, n)
        if self.dual and r.random() < 0.5:
            self.L("TSTOP")
            self.block(0, 4)
            self.L("LDI R6, t1_entry")
            self.L("TSTART R6")
        self.L("JMP start")
        subs = self.subs
        self.subs = None
        for s in subs:
            self.L(f"{s}:")
            self.block(2, r.randrange(1, 4))
            self.L("RET")
        if self.dual:
            self.subs = []
            self.no_crc = True
            self.L("t1_entry:")
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
