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
`WAIT_CMP` behaviour (B4) and one bug for each of F8, F9, F12, F13, F14, F15,
F17 (timed pins: three), F18 (registered pads), F19 (engine binding: two), F20 (TS) and
F21 (the cost table). Each
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
        python formal/run_formal.py --block F17 [--depth K] [--solver yices]
            one property block of the controller alone (its `// F17` comment to the
            next `// F<n>`), proved for all time by k-induction: its assertions, helper
            invariants included, are the only ones left, so each may lean on the others.
"""
import argparse
import glob
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
     "if (rel) pcs_n[10:0] = pc0 + 11'd1;", "if (rel) pcs_n[10:0] = pc0;"),
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
     "        own_n = fth; bub_n = 1'b0;", "        own_n = st_c ? cur : fth; bub_n = 1'b0;"),
    # v3: the late guard
    ("F15: the guard ignores which threads it guards", "tt_um_msf_ctrl.v",
     "assign oe_off = (|(late & lguard)) ? gpins : 8'h00;", "assign oe_off = (|late) ? gpins : 8'h00;"),
    # v3: the decode stage
    ("F14: no forwarding into E", "tt_um_msf_ctrl.v",
     "wire        fwd_a = fw_en && fw_thr == cur && fw_a == dra;", "wire        fwd_a = 1'b0;"),
    # v4: timed pins (F17) and the registered pads (F18)
    ("F17: a held event is applied one cycle late", "tt_um_msf_ctrl.v",
     "assign due[tk] = at_pend[tk] && (cnt == at_time[16*tk +: 16]);",
     "assign due[tk] = at_pend[tk] && (cnt == at_time[16*tk +: 16] + 16'd1);"),
    ("F17: a refused write still goes out", "tt_um_msf_ctrl.v",
     "wire        imm_now  = pwr_do && (!armed_c || t_now);",
     "wire        imm_now  = pwr_do && (!armed_c || t_now || t_refuse);"),
    ("F17: a held write forgets its operation", "tt_um_msf_ctrl.v",
     "p_tgt[2*cur +: 2] <= pwr_tgt; p_opk[2*cur +: 2] <= pwr_opk;",
     "p_tgt[2*cur +: 2] <= pwr_tgt; p_opk[2*cur +: 2] <= PK_SET;"),
    ("F18: the pad registers load the old values", "tt_um_msf_pio.v",
     "pad_out <= (out_n & ~tx_mask_n) | (tx_val_n & tx_mask_n);",
     "pad_out <= (out_r & ~tx_mask) | (tx_val & tx_mask);"),
    # v4: engine binding (F19)
    ("F19: an engine write goes to set 0 whatever the thread chose", "tt_um_msf_ctrl.v",
     "assign per_set = set_c;", "assign per_set = 1'b0;"),
    ("F19: a stalled pop is released by the other set's word", "tt_um_msf_ctrl.v",
     "(k == K_RXPOP)  ? (es ? rx_avail1 : rx_avail) :",
     "(k == K_RXPOP)  ? (es ? rx_avail : rx_avail1) :"),
    # v4: TS (F20)
    ("F20: a later event of the same wait overwrites TS", "tt_um_msf_ctrl.v",
     "if (run[ak] && st[ak] && evt[ak] && !whit[ak]) ts[16*ak +: 16] <= cnt;",
     "if (run[ak] && st[ak] && evt[ak]) ts[16*ak +: 16] <= cnt;"),
    # v4: the cost table (F21)
    ("F21: a load is not released in its first stall slot", "tt_um_msf_ctrl.v",
     "(k == K_LD)     ? 1'b1 :", "(k == K_LD)     ? tmo_now[t] :"),
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


def sby_config(top, files, depth, mode, engine, copy, aigsmt="yices", timeout=None, extra=""):
    # `copy`: every file SymbiYosys copies, including `include`d ones such as
    # the boot ROM data. `x` constants become free values (setundef -anyseq),
    # which over-approximates any choice synthesis could make for them.
    # `extra`: Yosys commands right after prep (keep_block: one block's assertions).
    reads = "\n".join(f"read_verilog -formal -sv -DFORMAL -DSYNTHESIS -I. {f}" for f in files)
    # aigsmt: the SMT solver that turns an ABC counterexample into a trace;
    # "none" skips the trace (the verdict is the same). None: no ABC engine.
    return f"""[options]
mode {mode}
depth {depth}
{f"aigsmt {aigsmt}" if aigsmt else ""}
{f"timeout {timeout}" if timeout else ""}

[engines]
{engine}

[script]
{reads}
prep -top {top}
{"chformal -assert -remove tt_um_msf_ctrl_ni" if top == "ni_miter" else ""}
{extra}
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
    shutil.copy(os.path.join(HERE, "ni_miter.v"), d)
    ni_copy(d)
    return d


NI_THREAD = ("{{pcs[{p}+:11], zf[{t}], nf[{t}], st[{t}], sk[{k3}+:3], srd[{k3}+:3], sop[{w}+:16], "
             "wsel[{k4}+:4], wcond[{k3}+:3], wprev[{t}], whit[{t}], sto[{w}+:16], sto_en[{t}], "
             "ldh[{w}+:16], ldq[{t}], tmo[{w}+:16], fge[{t}], wlate[{t}], late[{t}], "
             # v4: the selector byte, the timed-pin slot and TS
             "esel[{k4}+:4], at_arm[{t}], at_bad[{t}], at_pend[{t}], at_time[{w}+:16], "
             "p_tgt[{k2}+:2], p_opk[{k2}+:2], p_val[{k8}+:8], ts[{w}+:16], ni_rf{t}}}")
NI_PORTS = """    output reg  [7:0]  dbg_q,
    // formal/ni_miter.v: the issue stage and all the state, on ports
    output wire [1:0]  ni_dthr,
    output wire [1:0]  ni_cur,
    output wire        ni_exec,
    output wire [10:0] ni_pc,
    output wire [15:0] ni_ir,
    output wire [15:0] ni_qa,
    output wire [15:0] ni_qb,
    output wire [3:0]  ni_run,
    output wire [276:0] ni_t0, ni_t1, ni_t2, ni_t3,
    output wire [123:0] ni_sh,
    output wire        ni_bub,
    output wire [53:0] ni_dst,
    output wire [1:0]  ni_fwthr,
    output wire [19:0] ni_fw
);
  wire [127:0] ni_rf0, ni_rf1, ni_rf2, ni_rf3;"""
NI_ASSIGNS = """
  assign ni_dthr = d_thr;
  assign ni_cur = cur;
  assign ni_exec = exec;
  assign ni_pc = dbg_pc;
  assign ni_ir = ir;
  assign ni_qa = qa;
  assign ni_qb = qb;
  assign ni_run = run;
""" + "".join(f"  assign ni_t{t} = " + NI_THREAD.format(t=t, p=11 * t, k2=2 * t, k3=3 * t, k4=4 * t,
                                                         k8=8 * t, w=16 * t) + ";\n"
              for t in range(4)) + """  assign ni_sh = {run, own, bub, cnt, cnt_hi, sr, sr_po, sr_pi, sr_lsb, waddr, fbase, fn, fwp,
                  frp, fdrop, lguard, gpins};
  assign ni_bub = bub;
  assign ni_dst = {dir, dqa, dqb, dra, drb};
  assign ni_fwthr = fw_thr;
  assign ni_fw = {fw_en, fw_a, fw_d};
endmodule
"""


def ni_copy(d):
    """tt_um_msf_ctrl_ni.v and tt_um_msf_regfile_ni.v for target ni: the core under
    another name with its state on ports (its own assertions are removed in the
    flow), and the register file with its contents on a port."""
    s = open(os.path.join(d, "tt_um_msf_regfile.v")).read()
    for old, new in (("module tt_um_msf_regfile (", "module tt_um_msf_regfile_ni ("),
                     ("    output wire [15:0] qc\n);",
                      "    output wire [15:0] qc,\n    output wire [127:0] f_all\n);"),
                     ("  assign qc = rf[rc];\n",
                      "  assign qc = rf[rc];\n"
                      "  assign f_all = {rf[7], rf[6], rf[5], rf[4], rf[3], rf[2], rf[1], rf[0]};\n")):
        assert s.count(old) == 1, old
        s = s.replace(old, new)
    open(os.path.join(d, "tt_um_msf_regfile_ni.v"), "w").write(s)
    s = open(os.path.join(d, "tt_um_msf_ctrl.v")).read()
    for old, new in (("module tt_um_msf_ctrl (", "module tt_um_msf_ctrl_ni ("),
                     ("    output reg  [7:0]  dbg_q\n);", NI_PORTS)):
        assert s.count(old) == 1, old
        s = s.replace(old, new)
    assert s.count("tt_um_msf_regfile u_rf") == 4
    s = s.replace("tt_um_msf_regfile u_rf", "tt_um_msf_regfile_ni u_rf")
    for t, lo in enumerate((0, 16, 32, 48)):
        old = f".qa(qa_w[{lo + 15}:{lo}])"
        assert s.count(old) == 1, old
        s = s.replace(old, f".f_all(ni_rf{t}), " + old)
    end = s.rindex("endmodule")
    s = s[:end] + NI_ASSIGNS.lstrip("\n") + s[end + len("endmodule"):]
    open(os.path.join(d, "tt_um_msf_ctrl_ni.v"), "w").write(s)


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
# target ni (two copies of the core, formal/ni_miter.v) runs only when named: --top ni
NI_MUTANTS = [
    ("F16: STAT shows any thread's LATE flag", "tt_um_msf_ctrl.v",
     "wire [15:0] stat = {set_c ? err_any1 : err_any, late[cur], cur[1],",
     "wire [15:0] stat = {set_c ? err_any1 : err_any, |late, cur[1],"),
]
DEFAULT_TARGETS = ["ctrl", "cap", "top"]
TARGETS = {"ctrl": ("tt_um_msf_ctrl", CTRL_FILES), "cap": ("tt_um_msf_cap", ["tt_um_msf_cap.v"]),
           "top": ("tt_um_msf", TOP_FILES),
           "ni": ("ni_miter", ["ni_miter.v", "tt_um_msf_ctrl_ni.v", "tt_um_msf_regfile_ni.v",
                               "tt_um_msf_alu.v"])}


# the files a target reads that are made from another (ni_copy)
MADE_FROM = {"tt_um_msf_ctrl_ni.v": "tt_um_msf_ctrl.v", "tt_um_msf_regfile_ni.v": "tt_um_msf_regfile.v"}


def bmc(tools, depth, mutant=None, prove=False):
    results = []
    for top, files in (TARGETS[t] for t in tools.get("targets", DEFAULT_TARGETS)):
        # A target that does not read the mutated file is the unmutated design, which the
        # BMC above has just checked: running it again cannot catch the mutant, and for the
        # controller it costs as long as the whole BMC (27 minutes on the development PC).
        if mutant and mutant[1] not in {MADE_FROM.get(f, f) for f in files}:
            continue
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
    skipped) and none checked before it errored. A mutant no target reads is
    not caught."""
    if any(v == "error" for _, v, _, _ in res):
        return "ERROR"
    return "caught" if any(v == "fail" for _, v, _, _ in res) else "NOT CAUGHT"


def caught_by(res):
    """Which property stopped a caught mutant: the nearest `// F<n>` comment above the
    assertion the solver names (the mutations replace text within a line, so the line
    numbers are the source's), with the target and the step."""
    for top, verdict, out, fails in res:
        if verdict != "fail" or not fails:
            continue
        m = re.match(r"(\S+\.v):([0-9]+)", fails[0])
        if not m:
            return ""
        fname, line = m.group(1), int(m.group(2))
        path = os.path.join(SRC, fname)
        if not os.path.exists(path):
            path = os.path.join(HERE, fname)
        label = ""
        if os.path.exists(path):
            lines = open(path, encoding="utf-8").read().splitlines()
            for i in range(min(line, len(lines)) - 1, -1, -1):
                c = re.search(r"//\s*(F[0-9]+)", lines[i])
                if c:
                    label = c.group(1) + ", "
                    break
        step = re.findall(r"at \S+ step ([0-9]+)", out)
        return f"by {label}{fname}:{line}" + (f", step {step[-1]}" if step else "") + f" ({top})"
    return ""


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


def block_lines(fname, name):
    """First and last line of the property block whose comment starts `// NAME` in
    src/fname: up to the next block comment `// F<n>`."""
    lines = open(os.path.join(SRC, fname), encoding="utf-8").read().splitlines()
    start = next(i for i, s in enumerate(lines, 1)
                 if re.match(rf"\s*//\s*{re.escape(name)}\b", s))
    end = next((i for i, s in enumerate(lines, 1)
                if i > start and re.match(r"\s*//\s*F[0-9]+\b", s)), len(lines) + 1)
    return start, end - 1


def keep_block(fname, first, last):
    """Yosys commands that remove every assertion outside lines first..last of fname,
    list the ones left, and stop if there are none (a proof of nothing would pass)."""
    pats = " ".join(f"a:src={fname}:{n}.*" for n in range(first, last + 1))
    unions = " %u" * (last - first)
    asserts = "t:$check t:$assert %u n:$assert* %i"     # cells only: wires share the names
    return (f"chformal -assert -remove t:$check t:$assert %u {pats}{unions} %d\n"
            f"tee -q -o kept_asserts.txt select -list {asserts}\n"
            f"select -assert-min 1 {asserts}")


def prove_block(tools, name, depth, solver, fname="tt_um_msf_ctrl.v", mutant=None):
    """One property block of the controller, alone, for all time: its assertions are
    the only ones left, and SymbiYosys proves them together by k-induction (smtbmc),
    so each may lean on the others. A block can carry helper assertions that make it
    inductive; they are proved with it. The base case is a BMC to the same depth.
    Verdict: "pass" (proved), "fail" (a counterexample from reset), "unknown" (the
    induction step failed: not proved) or "error" (the tool did not decide)."""
    first, last = block_lines(fname, name)
    top, files = TARGETS["ctrl"]
    d = prepare(mutant)
    cfg = sby_config(top, files, depth, "prove", f"smtbmc {solver}", vfiles(d), aigsmt=None,
                     timeout=tools.get("timeout"), extra=keep_block(fname, first, last))
    verdict, out = run_sby(tools["sby"], d, cfg)
    if verdict == "error" and "DONE (UNKNOWN" in out:
        verdict = "unknown"
    failed = sorted(set(re.findall(r"Assert failed in \S+: (\S+)", out)))
    if mutant:
        shutil.rmtree(d, ignore_errors=True)
        return verdict, failed
    kept = glob.glob(os.path.join(d, "run", "**", "kept_asserts.txt"), recursive=True)
    names = [s for s in open(kept[0]).read().split() if "$assert" in s] if kept else []
    where = sorted({int(m) for m in re.findall(rf"{re.escape(fname)}:([0-9]+)", " ".join(names))})
    print(f"== {name}: {fname} lines {first}-{last}, {len(names)} assertions "
          f"(on lines {', '.join(map(str, where))}), k-induction to depth {depth} ({solver}) ==")
    if verdict == "pass":
        print(f"  PROVED for all time")
    else:
        print("  " + {"fail": "COUNTEREXAMPLE from reset",
                      "unknown": "NOT PROVED: the induction step failed"}.get(
                          verdict, "NOT PROVED: the tool stopped without a verdict"))
        for src in failed:
            print(f"    failed: {src}")
        keep = os.path.join(HERE, "work", f"block_{name}")
        os.makedirs(keep, exist_ok=True)
        for vcd in glob.glob(os.path.join(d, "run", "engine_*", "*.vcd")):
            shutil.copy(vcd, keep)
        print(f"  traces in {keep}")
        print(out[-4000:])
    shutil.rmtree(d, ignore_errors=True)
    return verdict, failed


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
    ap.add_argument("--block", help="prove only this property block of the controller "
                    "(e.g. F17) for all time, by k-induction to --depth, and stop")
    ap.add_argument("--solver", default="yices", help="SMT solver for --block")
    a = ap.parse_args()
    tools = {"engine": a.engine, "yosys": a.yosys.split(), "sby": a.sby.split(),
             "aigsmt": a.aigsmt, "timeout": a.prove_timeout,
             "targets": [a.top] if a.top else DEFAULT_TARGETS}
    mutants = NI_MUTANTS if a.top == "ni" else MUTANTS
    status = 0

    if a.block:
        verdict, _ = prove_block(tools, a.block, a.depth, a.solver)
        status = 0 if verdict == "pass" else 1
        # the block's own planted bugs (MUTANTS named "F17: ..."): the same proof must not
        # pass on any of them; a tool that stops without a verdict does not count
        own = [m for m in MUTANTS if m[0].startswith(a.block + ":")]
        if own and not a.no_mutants:
            print(f"== {a.block}'s planted bugs: the same proof must not pass ==")
            for m in own:
                v, failed = prove_block(tools, a.block, a.depth, a.solver, mutant=m)
                caught = v in ("fail", "unknown")
                how = {"fail": "counterexample from reset", "unknown": "induction step fails",
                       "pass": "PROVED", "error": "tool error"}[v]
                print(f"  {m[0]:46s} {'caught' if caught else 'NOT CAUGHT'} ({how}"
                      + (f": {failed[0]}" if failed else "") + ")", flush=True)
                if not caught:
                    status = 1
        return status

    if a.only_mutant is not None:
        m = mutants[a.only_mutant]
        res = bmc(tools, a.depth, m)
        print(f"  {m[0]:34s} {mutant_verdict(res)} {caught_by(res)}", flush=True)
        return 0 if mutant_verdict(res) == "caught" else 1
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
        for m in mutants:
            res = bmc(tools, a.depth, m)
            v = mutant_verdict(res)
            print(f"  {m[0]:34s} {v} {caught_by(res)}", flush=True)
            if v != "caught":
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
