#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Formal verification of msfcore with Yosys: SymbiYosys + ABC, or `sat`.

Properties live in the RTL under `ifdef FORMAL` (F1-F6, F8 and F10-F13 in
tt_um_msf_ctrl.v, F7 and F9 in project.v, F9 also in tt_um_msf_cap.v, where
the core's use of port B is a free input). Every input -- including the instruction memory read data,
data-RAM read data, pins and peripheral status -- is unconstrained, so a
proof covers every program in every environment.

For each target this script runs
  * BMC  : no counterexample of length <= N from reset
  * cover: every cover() statement is reachable (properties are not vacuous)
  * prove (--prove): the properties hold for all time (ABC `pdr`). PDR
    proves each assertion separately; with --prove-timeout it stops after
    that many seconds per target and lists which assertions it proved.
and a *mutation check*: the same properties are run against copies of the
RTL with the review's bugs re-introduced (B1 pc not advanced on release,
B2 WAIT kinds not separated, B3 combinational bank select), the v2.1 late
`WAIT_CMP` behaviour (B4) and one bug for each of F8, F9, F12 and F13. Each
mutant must FAIL -- this shows the properties actually detect those bugs.

Engines (--engine):
  * abc (default): SymbiYosys with ABC `bmc3` (and `pdr` for --prove).
    Needs a native Yosys with `sby` and `yosys-abc` (OSS CAD Suite).
  * sat: Yosys' built-in `sat -seq N -prove-asserts`. Works with YoWASP
    (`pip install yowasp-yosys`, --yosys yowasp-yosys), but since the four
    threads the top-level BMC needs more memory than 32-bit WASM has.

Usage:  python formal/run_formal.py [--depth N] [--engine abc|sat] [--prove]
                                    [--prove-timeout S] [--top ctrl|top]
                                    [--no-mutants] [--yosys PATH] [--sby PATH]
"""
import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")

CTRL_FILES = ["tt_um_msf_ctrl.v", "tt_um_msf_regfile.v", "tt_um_msf_alu.v"]
TOP_FILES = ["project.v", "tt_um_msf_ctrl.v", "tt_um_msf_regfile.v", "tt_um_msf_alu.v",
             "tt_um_msf_bootrom.v", "tt_um_msf_pio.v", "tt_um_msf_sram.v",
             "tt_um_msf_serdes.v", "tt_um_msf_crc.v", "tt_um_msf_cap.v", "tt_um_msf_filt.v", "sram_stub.v"]

# (name, file, old, new): re-introduce a review bug
MUTANTS = [
    ("B1 pc not advanced on release", "tt_um_msf_ctrl.v",
     "if (rel) pcs[9:0] <= pc0 + 10'd1;", "if (rel) pcs[9:0] <= pc0;"),
    ("B2 WAIT kinds not separated", "tt_um_msf_ctrl.v",
     "(k == K_EDGE || k == K_CMP) ? (evt[t] || whit[t]) :",
     "(k == K_EDGE || k == K_CMP) ? (e || cmp || whit[t]) :"),
    ("B3 combinational bank select", "project.v",
     "wire [15:0] imem_rd = imem_sel_sram ? sram_rd : rom_rd;",
     "wire [15:0] imem_rd = (imem_raddr[9:7] != 3'b000) ? sram_rd : rom_rd;"),
    ("B4 late WAIT_CMP waits for the wrap", "tt_um_msf_ctrl.v",
     "wire rel    = st_c && (cond_now[cur] || tmo_now[cur] || wlate_c);",
     "wire rel    = st_c && (cond_now[cur] || tmo_now[cur]);"),
    # the v2.1 properties, each with a bug it must catch
    ("F8: a push into a full FIFO", "tt_um_msf_ctrl.v",
     "if (!ffull) fwp   <= fwp + 9'd1;", "if (1'b1) fwp   <= fwp + 9'd1;"),
    ("F9: capture ignores the core on port B", "tt_um_msf_cap.v",
     "wire       pop   = (qn != 2'd0) && !core_b;", "wire       pop   = (qn != 2'd0);"),
    ("F12: the rotation skips the next thread", "tt_um_msf_ctrl.v",
     "for (k = 3; k >= 1; k = k - 1) begin", "for (k = 3; k >= 2; k = k - 1) begin"),
    ("F13: a stalled thread holds on to the next slot", "tt_um_msf_ctrl.v",
     "          own <= fth; bub <= 1'b0;", "          own <= st_c ? cur : fth; bub <= 1'b0;"),
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


def vfiles(d):
    return sorted(f for f in os.listdir(d) if f.endswith(".v"))


def sby_config(top, files, depth, mode, engine, copy, aigsmt="yices", timeout=None):
    # `copy`: every file SymbiYosys copies, including `include`d ones such as
    # the boot ROM data. `x` constants become free values (setundef -anyseq),
    # which over-approximates any choice synthesis could make for them.
    reads = "\n".join(f"read_verilog -formal -sv -DFORMAL -DSYNTHESIS -I. {f}" for f in files)
    # aigsmt: the SMT solver that turns an ABC counterexample into a trace;
    # "none" skips the trace (the verdict is the same).
    return f"""[options]
mode {mode}
depth {depth}
aigsmt {aigsmt}
{f"timeout {timeout}" if timeout else ""}

[engines]
{engine}

[script]
{reads}
prep -top {top}
memory -nomap
memory_map
opt_clean
flatten
setundef -undriven -expose
techmap
opt -fast
setundef -anyseq
delete t:$cover
opt_clean

[files]
""" + "\n".join(copy) + "\n"


def run_sby(sby, workdir, cfg):
    """Run SymbiYosys; verdict "pass", "fail" (counterexample) or "error"."""
    with open(os.path.join(workdir, "run.sby"), "w") as f:
        f.write(cfg)
    p = subprocess.run(sby + ["-f", "run.sby"], cwd=workdir, capture_output=True, text=True)
    out = p.stdout + p.stderr
    verdict = "pass" if "DONE (PASS" in out else "fail" if "DONE (FAIL" in out else "error"
    return verdict, out


def proved_asserts(workdir):
    """(proved, total, open source locations) from SymbiYosys' status
    database, or None if this SymbiYosys keeps none."""
    db = os.path.join(workdir, "run", "status.sqlite")
    if not os.path.exists(db):
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    passed = {r[0] for r in con.execute(
        "select task_property from task_property_status where status = 'PASS'")}
    props = con.execute("select id, src from task_property where kind = 'ASSERT'").fetchall()
    con.close()
    return (sum(i in passed for i, _ in props), len(props),
            [src.split(".")[0] + ".v:" + src.split(":")[1].split(".")[0]
             for i, src in props if i not in passed])


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


def check(tools, d, top, files, depth, prove=False):
    """Verdict "pass" (holds to `depth`, or for all time if `prove`), "fail"
    (a counterexample) or "error" (the tool did not decide: never a pass, and
    never counted as a caught mutant or a reached cover)."""
    if tools["engine"] == "abc":
        mode, engine = ("prove", "abc pdr") if prove else ("bmc", "abc bmc3")
        return run_sby(tools["sby"], d, sby_config(top, files, depth, mode, engine, vfiles(d),
                                                    tools["aigsmt"],
                                                    tools.get("timeout") if prove else None))
    assert not prove, "--prove needs --engine abc"
    rc, out = run(tools["yosys"], d, script(top, files, depth, "bmc"))
    if rc == 0 and "SAT proof finished - no model found: SUCCESS" in out:
        return "pass", out
    if "SAT proof finished - model found" in out or "Assert failed" in out:
        return "fail", out
    return "error", out


# cheapest first: a mutant stops at its first counterexample
TARGETS = {"ctrl": ("tt_um_msf_ctrl", CTRL_FILES), "cap": ("tt_um_msf_cap", ["tt_um_msf_cap.v"]),
           "top": ("tt_um_msf", TOP_FILES)}


def bmc(tools, depth, mutant=None, prove=False):
    results = []
    for top, files in (TARGETS[t] for t in tools.get("targets", TARGETS)):
        d = prepare(mutant)
        verdict, out = check(tools, d, top, files, depth, prove)
        fail_at = re.findall(r"Assert failed in \S+: (.*)", out)
        if prove:
            fail_at = proved_asserts(d)
        results.append((top, verdict, out if verdict != "pass" else "", fail_at))
        shutil.rmtree(d, ignore_errors=True)
        if mutant and verdict == "fail":
            break                    # caught: the other targets cannot change that
    return results


def mutant_verdict(res):
    """caught: a target fails with a counterexample (the later ones are then
    skipped) and none checked before it errored."""
    if any(v == "error" for _, v, _, _ in res):
        return "ERROR"
    return "caught" if any(v == "fail" for _, v, _, _ in res) else "NOT CAUGHT"


def covers(tools, depth):
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
        verdict, out = check(tools, d, "tt_um_msf_ctrl", CTRL_FILES, depth)
        reached.append(verdict)
    shutil.rmtree(d, ignore_errors=True)
    return reached


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=20)
    ap.add_argument("--engine", choices=("abc", "sat"), default="abc")
    ap.add_argument("--prove", action="store_true", help="also prove for all time (ABC pdr)")
    ap.add_argument("--prove-timeout", type=int,
                    help="stop each unbounded proof after this many seconds")
    ap.add_argument("--top", choices=sorted(TARGETS), help="check only this target")
    ap.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    ap.add_argument("--sby", default=os.environ.get("SBY", "sby"))
    ap.add_argument("--aigsmt", default="yices",
                    help="SMT solver for ABC counterexample traces, or none")
    ap.add_argument("--no-mutants", action="store_true")
    ap.add_argument("--no-cover", action="store_true")
    ap.add_argument("--only-mutant", type=int, help="run just mutant N (0-based)")
    a = ap.parse_args()
    tools = {"engine": a.engine, "yosys": a.yosys.split(), "sby": a.sby.split(),
             "aigsmt": a.aigsmt, "timeout": a.prove_timeout,
             "targets": [a.top] if a.top else list(TARGETS)}
    status = 0

    if a.only_mutant is not None:
        m = MUTANTS[a.only_mutant]
        res = bmc(tools, a.depth, m)
        print(f"  {m[0]:34s} {mutant_verdict(res)}")
        return 0
    print(f"== BMC depth {a.depth} (all inputs free, engine {a.engine}) ==")
    for top, verdict, out, fails in bmc(tools, a.depth):
        print(f"  {top:16s} {verdict.upper()}")
        if verdict != "pass":
            print(out[-3000:])
            status = 1

    if a.prove:
        print("== unbounded proof (ABC pdr) ==")
        for top, verdict, out, counts in bmc(tools, a.depth, prove=True):
            print(f"  {top:16s} {'PROVED' if verdict == 'pass' else 'COUNTEREXAMPLE' if verdict == 'fail' else 'NOT PROVED'}")
            if counts:
                print(f"    {counts[0]}/{counts[1]} assertions proved for all time"
                      + (f"; open: {', '.join(counts[2])}" if counts[2] else ""))
            if verdict != "pass":
                print(out[-3000:])
                status = 1

    if not a.no_cover:
        print(f"== cover reachability (depth {a.depth}) ==")
        r = covers(tools, a.depth)
        print(f"  {r.count('fail')}/{len(r)} cover points reached")
        if r.count("fail") != len(r):
            print(f"  unreached: {[i for i, v in enumerate(r) if v == 'pass']}"
                  f"  undecided (tool error): {[i for i, v in enumerate(r) if v == 'error']}")
            status = 1

    if not a.no_mutants:
        print("== mutation check (each must FAIL) ==")
        for m in MUTANTS:
            v = mutant_verdict(bmc(tools, a.depth, m))
            print(f"  {m[0]:34s} {v}")
            if v != "caught":
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
