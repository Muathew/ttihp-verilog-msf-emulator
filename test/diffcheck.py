# SPDX-License-Identifier: Apache-2.0
"""RTL-vs-ISA-model differential checker (shared by the cocotb tests).

run_and_compare() loads a program, resets the DUT, drives a per-cycle pad
schedule and records every issue / stall-release event of the RTL together
with the architectural state at the start of that cycle. The independent
isa_model.IsaModel runs the same program on the same inputs; the two event
traces must be identical (cycle, thread, PC, instruction word, state).
"""
from cocotb.triggers import RisingEdge, ReadOnly, ClockCycles

from assembler import assemble, disassemble, bootloader_words
from isa_model import IsaModel


class Probe:
    """Cached handles into the RTL for fast per-cycle sampling."""

    def __init__(self, dut):
        c = dut.user_project.u_ctrl
        self.issue, self.thr, self.pc, self.ir, self.rel = (
            c.dbg_issue, c.dbg_thr, c.dbg_pc, c.ir, c.dbg_rel)
        self.rf = [[rf.rf[i] for i in range(8)] for rf in (c.u_rf, c.u_rf1, c.u_rf2, c.u_rf3)]
        self.zf, self.nf, self.sr, self.tmo, self.run = c.zf, c.nf, c.sr, c.tmo, c.run
        self.fifo = (c.fwp, c.frp, c.fdrop, c.fbase, c.fn)
        self.esel, self.ts = c.esel, c.ts                           # v4: selector byte, TS
        self.slot = (c.at_arm, c.at_bad, c.at_pend, c.at_time, c.p_tgt, c.p_opk, c.p_val)
        p = dut.user_project.u_pio
        self.out, self.oe, self.uo = p.out_r, p.oe_r, p.uo_r
        self.oe_pad = p.uio_oe          # OE at the pads: the late guard applied

    def state(self):
        zf, nf, tmo = int(self.zf.value), int(self.nf.value), int(self.tmo.value)
        wp, rp, drop, base, fn = (int(h.value) for h in self.fifo)
        snap = {"out": int(self.out.value), "oe": int(self.oe.value), "uo": int(self.uo.value),
                "oe_pad": int(self.oe_pad.value),
                "sr": int(self.sr.value), "run": int(self.run.value),
                "fcount": (wp - rp) & 0x1FF, "fdrop": drop, "fcfg": (base, fn)}
        esel, ts = int(self.esel.value), int(self.ts.value)
        arm, bad, pend, atime, ptgt, popk, pval = (int(h.value) for h in self.slot)
        for k in range(4):
            held = (pend >> k) & 1
            snap.update({f"regs{k}": [int(h.value) for h in self.rf[k]],
                         f"z{k}": (zf >> k) & 1, f"n{k}": (nf >> k) & 1,
                         f"tmo{k}": (tmo >> (16 * k)) & 0xFFFF,
                         f"esel{k}": (esel >> (4 * k)) & 0xF, f"ts{k}": (ts >> (16 * k)) & 0xFFFF,
                         # the slot as isa_model.IsaModel._slot gives it
                         f"at{k}": ((arm >> k) & 1, (bad >> k) & 1, held,
                                    (atime >> (16 * k)) & 0xFFFF if ((arm >> k) & 1 or held) else 0,
                                    ((ptgt >> (2 * k)) & 3, (popk >> (2 * k)) & 3,
                                     (pval >> (8 * k)) & 0xFF) if held else (0, 0, 0))})
        return snap


async def run_and_compare(dut, src, cycles, uio=None, ui=None, data=None, probe=None, tag=""):
    """Run `src` (assembled at 128) on RTL and model; raise on any difference.

    uio/ui: per-cycle pad values (lists, default constant 0 / 1).
    data:   {addr: word} preloaded into SRAM (e.g. data RAM 0..127).
    Returns (issues, [issues on T0..T3], releases).
    """
    words = assemble(src, base_addr=128)
    uio = uio or [0] * cycles
    ui = ui or [1] * cycles
    data = data or {}
    probe = probe or Probe(dut)

    mem = {}
    for a in range(2048):
        mem[a] = data.get(a, 0) if a < 128 else (
            words[a - 128] if a - 128 < len(words) else 0x7000)
    for a, w in mem.items():
        dut.user_project.u_sram.mem[a].value = w

    dut.uio_in.value = 0
    dut.ui_in.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)             # the reset synchroniser (isa.md section 1)
    # cycle 0 starts now: the core's first active edge is the next one
    rtl = []
    for c in range(cycles):
        dut.uio_in.value = uio[c]
        dut.ui_in.value = ui[c]
        await ReadOnly()
        if int(probe.rel.value):
            rtl.append(("R", c, int(probe.thr.value)))
        if int(probe.issue.value):
            rtl.append(("I", c, int(probe.thr.value), int(probe.pc.value),
                        int(probe.ir.value), probe.state()))
        await RisingEdge(dut.clk)

    m = IsaModel(bootloader_words(), mem,
                 uio_pad=lambda c: uio[c] if 0 <= c < len(uio) else 0,
                 ui_pad=lambda c: ui[c] if 0 <= c < len(ui) else 1)
    m.run(cycles)
    mod = [(e[0], e[1], e[2]) if e[0] == "R" else e for e in m.trace]

    for i, (x, y) in enumerate(zip(rtl, mod)):
        if x[:5] != y[:5] or (x[0] == "I" and x[5] != y[5]):
            lo = max(0, i - 8)
            ctx = "\n".join(
                f"   {'>>' if j == i else '  '} {e[0]} c={e[1]} t={e[2]}"
                + (f" pc={e[3]} {disassemble(e[4], e[3])}" if e[0] == "I" else "")
                for j, e in enumerate(rtl[lo:i + 1], start=lo))
            diff = ""
            if x[0] == "I" and y[0] == "I" and x[:5] == y[:5]:
                diff = "\n   state diff: " + ", ".join(
                    f"{k}: rtl={x[5][k]} model={y[5][k]}" for k in x[5] if x[5][k] != y[5][k])
            raise AssertionError(
                f"{tag}: event {i} differs\n   rtl   = {x[:5]}\n   model = {y[:5]}{diff}\n"
                f"{ctx}\n--- program ---\n{src}")
    assert len(rtl) == len(mod), f"{tag}: event count rtl={len(rtl)} model={len(mod)}"
    issues = [e for e in rtl if e[0] == "I"]
    per = [sum(1 for e in issues if e[2] == k) for k in range(4)]
    return len(issues), per, sum(1 for e in rtl if e[0] == "R")
