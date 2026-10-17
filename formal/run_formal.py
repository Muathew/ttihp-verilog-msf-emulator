#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Formal verification of msfcore with Yosys' built-in SAT engine.

Properties live in the RTL under `ifdef FORMAL` (F1-F6 in tt_um_msf_ctrl.v,
F7 in project.v). Every input -- including the instruction memory read data,
data-RAM read data, pins and peripheral status -- is unconstrained, so a
proof covers every program in every environment.

For each target this script runs
  * BMC  : `sat -seq N -prove-asserts` from reset (no counterexample of length <= N)
  * cover: every cover() statement is reachable (properties are not vacuous)
and a *mutation check*: the same properties are run against copies of the
RTL with the review's bugs re-introduced (B1 pc not advanced on release,
B2 WAIT kinds not separated, B3 combinational bank select). Each mutant must
FAIL -- this shows the properties actually detect those bugs.

Usage:  python formal/run_formal.py [--depth N] [--no-mutants] [--yosys PATH]
Needs Yosys (e.g. `pip install yowasp-yosys`, then --yosys yowasp-yosys).
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")

CTRL_FILES = ["tt_um_msf_ctrl.v", "tt_um_msf_regfile.v", "tt_um_msf_alu.v"]
TOP_FILES = ["project.v", "tt_um_msf_ctrl.v", "tt_um_msf_regfile.v", "tt_um_msf_alu.v",
             "tt_um_msf_bootrom.v", "tt_um_msf_pio.v", "tt_um_msf_sram.v",
             "tt_um_msf_serdes.v", "tt_um_msf_crc.v", "sram_stub.v"]

# (name, file, old, new): re-introduce a review bug
MUTANTS = [
    ("B1 pc not advanced on release", "tt_um_msf_ctrl.v",
     "if (rel) pc0 <= pc0 + 9'd1;", "if (rel) pc0 <= pc0;"),
    ("B2 WAIT kinds not separated", "tt_um_msf_ctrl.v",
     "(k == K_EDGE || k == K_CMP) ? (evt[t] || whit[t]) :",
     "(k == K_EDGE || k == K_CMP) ? (e || cmp || whit[t]) :"),
    ("B3 combinational bank select", "project.v",
     "wire [15:0] imem_rd = imem_sel_sram ? sram_rd : rom_rd;",
     "wire [15:0] imem_rd = (imem_raddr[8:7] != 2'b00) ? sram_rd : rom_rd;"),
]


def script(top, files, depth, mode):
    reads = "\n".join(f"read_verilog -formal -sv -DFORMAL -DSYNTHESIS -I. {f}" for f in files)
    head = f"""{reads}
prep -top {top}
memory -nomap
memory_map
opt_clean
async2sync
dffunmap
flatten
setundef -undriven -expose
delete t:$cover
opt_clean
"""
    if mode == "bmc":
        return head + f"sat -seq {depth} -prove-asserts -set-assumes -set-init-def -set-def-inputs -verify -show-ports\n"
    return head + "\n".join(
        f"sat -seq {depth} -set-assumes -set-init-def -set-def-inputs "
        f"-prove {c} 0 -show-ports" for c in ["COVERS"]) + "\n"


def run(yosys, workdir, ys):
    path = os.path.join(workdir, "run.ys")
    with open(path, "w") as f:
        f.write(ys)
    p = subprocess.run(yosys + ["-s", "run.ys"], cwd=workdir,
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def prepare(mutant=None):
    d = tempfile.mkdtemp(prefix="msf_formal_")
    for f in os.listdir(SRC):
        if f.endswith(".v"):
            shutil.copy(os.path.join(SRC, f), d)
    shutil.copy(os.path.join(HERE, "sram_stub.v"), d)
    if mutant:
        _, fname, old, new = mutant
        p = os.path.join(d, fname)
        s = open(p).read()
        assert old in s, f"mutation anchor not found: {old}"
        open(p, "w").write(s.replace(old, new))
    return d


def bmc(yosys, depth, mutant=None):
    results = []
    for top, files in (("tt_um_msf_ctrl", CTRL_FILES), ("tt_um_msf", TOP_FILES)):
        d = prepare(mutant)
        rc, out = run(yosys, d, script(top, files, depth, "bmc"))
        ok = rc == 0 and "SAT proof finished - no model found: SUCCESS" in out
        fail_at = re.findall(r"Assert failed in \S+: (.*)", out)
        results.append((top, ok, out if not ok else "", fail_at))
        shutil.rmtree(d, ignore_errors=True)
    return results


def covers(yosys, depth):
    """Each cover() must be reachable: ask SAT for a trace satisfying it."""
    d = prepare()
    src = open(os.path.join(d, "tt_um_msf_ctrl.v")).read()
    n = len(re.findall(r"\bcover\(", src))
    reached = []
    for k in range(n):
        # keep only cover k, turn it into an assertion of its negation
        idx = [m.start() for m in re.finditer(r"\bcover\(", src)]
        s = src
        for j in reversed(range(n)):
            start = idx[j]
            end = s.index(");", start) + 2
            body = s[start + 6:end - 2]
            s = s[:start] + (f"assert(!({body}));" if j == k else "") + s[end:]
        # drop the other assertions: only the cover target remains
        s = re.sub(r"assert\((?!!\()", "assume(1 || ", s)
        open(os.path.join(d, "tt_um_msf_ctrl.v"), "w").write(s)
        rc, out = run(yosys, d, script("tt_um_msf_ctrl", CTRL_FILES, depth, "bmc"))
        reached.append("SAT proof finished - model found" in out or "FAIL" in out)
    shutil.rmtree(d, ignore_errors=True)
    return reached


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=20)
    ap.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    ap.add_argument("--no-mutants", action="store_true")
    ap.add_argument("--no-cover", action="store_true")
    ap.add_argument("--only-mutant", type=int, help="run just mutant N (0-based)")
    a = ap.parse_args()
    yosys = a.yosys.split()
    status = 0

    if a.only_mutant is not None:
        m = MUTANTS[a.only_mutant]
        res = bmc(yosys, a.depth, m)
        print(f"  {m[0]:34s} {'caught' if any(not ok for _, ok, _, _ in res) else 'NOT CAUGHT'}")
        return 0
    print(f"== BMC depth {a.depth} (all inputs free) ==")
    for top, ok, out, fails in bmc(yosys, a.depth):
        print(f"  {top:16s} {'PASS' if ok else 'FAIL'}")
        if not ok:
            print(out[-3000:])
            status = 1

    if not a.no_cover:
        print(f"== cover reachability (depth {a.depth}) ==")
        r = covers(yosys, a.depth)
        print(f"  {sum(r)}/{len(r)} cover points reached")
        if not all(r):
            print(f"  unreached: {[i for i, x in enumerate(r) if not x]}")
            status = 1

    if not a.no_mutants:
        print("== mutation check (each must FAIL) ==")
        for m in MUTANTS:
            res = bmc(yosys, a.depth, m)
            caught = any(not ok for _, ok, _, _ in res)
            print(f"  {m[0]:34s} {'caught' if caught else 'NOT CAUGHT'}")
            if not caught:
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
