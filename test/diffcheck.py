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
        self.rf = [[c.u_rf.rf[i] for i in range(8)], [c.u_rf1.rf[i] for i in range(8)]]
        self.zf, self.nf, self.sr = c.zf, c.nf, c.sr
        p = dut.user_project.u_pio
        self.out, self.oe, self.uo = p.out_r, p.oe_r, p.uo_r

    def state(self):
        zf, nf = int(self.zf.value), int(self.nf.value)
        return {
            "regs0": [int(h.value) for h in self.rf[0]],
            "regs1": [int(h.value) for h in self.rf[1]],
            "z0": zf & 1, "n0": nf & 1, "z1": zf >> 1, "n1": nf >> 1,
            "out": int(self.out.value), "oe": int(self.oe.value), "uo": int(self.uo.value),
            "sr": int(self.sr.value),
        }


async def run_and_compare(dut, src, cycles, uio=None, ui=None, data=None, probe=None, tag=""):
    """Run `src` (assembled at 128) on RTL and model; raise on any difference.

    uio/ui: per-cycle pad values (lists, default constant 0 / 1).
    data:   {addr: word} preloaded into SRAM (e.g. data RAM 0..127).
    Returns (issues, issues_on_T1, releases).
    """
    words = assemble(src, base_addr=128)
    uio = uio or [0] * cycles
    ui = ui or [1] * cycles
    data = data or {}
    probe = probe or Probe(dut)

    mem = {}
    for a in range(512):
        mem[a] = data.get(a, 0) if a < 128 else (
            words[a - 128] if a - 128 < len(words) else 0x7000)
    for a, w in mem.items():
        dut.user_project.u_sram.mem[a].value = w

    dut.uio_in.value = 0
    dut.ui_in.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1                       # cycle 0 starts now
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
    return len(issues), sum(1 for e in issues if e[2] == 1), sum(1 for e in rtl if e[0] == "R")
