#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Plant timing bugs in the firmware and see whether tools/msf_timing.py notices.

A static checker is only worth something if it refuses what it should. This
plants, one at a time, the bugs a firmware author makes around a deadline:

  nop     k NOPs before a WAIT_CMP (1, 2, 3 cycles, half the slack, one short of
          the slack, the slack, and 1 and 3 past it)
  noadv   the ADDS that moves the deadline register on, deleted
  nowait  a WAIT_CMP deleted
  equ     a timing constant cut to a quarter
  branch  a conditional branch inverted

Each mutant gets the checker's verdict, then runs on the RTL against the peers
of its firmware's own tests, with DeadlineWatch (test/fw_tb.py) writing down
every WAIT_CMP issue and whether a LATE flag rose. A test may well fail on a
mutant; only the timing counts here.

  CERTIFIED, LATE    the checker passed a program that ran late: a checker bug
  certified          no lateness seen, and the mutated deadline was reached
  certified, unseen  the run never reached the mutated deadline: no evidence
  refused, late      the checker refused it, and it did run late
  refused            the checker refused it; not seen late in this run, so it
                     may be caution or a path the test's input does not take
  not plantable      it no longer assembles or fits (too many NOPs)
  (pinfind.s has one deadline, in its report routine, and a long analysis: not here)

Mutants come from a hash of the source, so a run can be repeated; editing a
firmware changes its mutants.

Usage (from the repository root, project venv active):
    python tools/fw_mutants.py                     every mutant (hours)
    python tools/fw_mutants.py --sample 2          the first 2 per firmware (CI)
    python tools/fw_mutants.py --firmware uart_tx.s --jobs 4 --json out.json
Exit status 1 if any mutant is CERTIFIED, LATE, or the unmutated firmware is
not clean.
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
TEST = os.path.join(ROOT, "test")
FW = os.path.join(ROOT, "firmware")
sys.path.insert(0, TEST)
sys.path.insert(0, HERE)
import msf_timing  # noqa: E402

BUILD = os.path.join(TEST, "sim_build", "mutants")
MAX_WORDS = 896
QUICK = "--quick" in sys.argv

# every firmware with a WAIT_CMP, and the tests that drive it with its peers
TESTS = {
    "uart_tx.s": ("test_firmware", ["test_fw_uart_tx_drift_free"]),
    "ws2812.s": ("test_firmware", ["test_fw_ws2812"]),
    "usb_ls_tx.s": ("test_firmware", ["test_fw_usb_ls_tx_packet"]),
    "eth_10baset_tx.s": ("test_firmware", ["test_fw_10baset_frame_fcs_tpidl_nlp"]),
    "thread_demo.s": ("test_firmware", ["test_fw_threads_blink_and_echo"]),
    "can.s": ("test_buses", ["test_can_transmit", "test_can_receive_and_ack",
                             "test_can_arbitration_and_no_ack"]),
    "onewire.s": ("test_buses", ["test_onewire_reset_read_rom_scratchpad",
                                 "test_onewire_search_rom"]),
    "ps2_device.s": ("test_buses", ["test_ps2_device_types_scancodes",
                                    "test_ps2_device_answers_commands",
                                    "test_ps2_device_host_inhibit"]),
    "replay.s": ("test_capture_tools", ["test_replay_i2c_edge_for_edge"]),
    "margin_uart.s": ("test_margin", ["test_margin_uart_silent_device",
                                      "test_margin_uart_other_device"]),
    "margin_i2c.s": ("test_margin", ["test_margin_i2c_wrong_address", "test_margin_i2c_low"]),
    "margin_spi.s": ("test_margin", ["test_margin_spi"]),
}

RE_CODE = re.compile(r"^(\s*)((?:[A-Za-z_]\w*:\s*)*)(.*?)\s*(;.*)?$")


def split_line(line):
    """(indent, labels, instruction, comment)"""
    m = RE_CODE.match(line)
    return m.group(1), m.group(2), m.group(3), m.group(4) or ""


def wait_cmp_lines(lines):
    """Line numbers of the file's WAIT_CMPs, in address order."""
    return [i for i, ln in enumerate(lines)
            if re.match(r"WAIT_CMP\b", split_line(ln)[2], re.I)]


def insert_before(lines, i, new):
    """new lines just before the instruction at line i, after its labels, so
    that every path into the instruction goes through them."""
    ind, labels, ins, com = split_line(lines[i])
    head = [f"{ind}{labels}".rstrip()] if labels else []
    return lines[:i] + head + new + [f"        {ins}  {com}".rstrip()] + lines[i + 1:]


def delete_at(lines, i):
    ind, labels, _ins, com = split_line(lines[i])
    return lines[:i] + [f"{ind}{labels}{com}".rstrip()] + lines[i + 1:]


def mutants_of(name):
    """Every mutant of one firmware: dicts with id, kind, source, defines, site."""
    path = os.path.join(FW, name)
    text = open(path, encoding="utf-8").read().replace("\r\n", "\n")
    lines = text.split("\n")
    an, _, _ = msf_timing.analyse(path)
    sites = sorted(an.deadlines)
    waits = wait_cmp_lines(lines)
    assert len(waits) == len(sites), (name, len(waits), len(sites))
    n = an.n
    out = []

    def add(kind, detail, source=None, defines=None, site=None):
        out.append({"id": f"{name}:{kind}:{detail}", "kind": kind, "firmware": name,
                    "source": text if source is None else "\n".join(source),
                    "defines": defines or {}, "site": site})

    for ordinal, (i, pc) in enumerate(zip(waits, sites)):
        slack = (an.deadlines[pc]["lo"] or 1) - 1
        cycles = {1, slack // 2, slack, slack + 1} if QUICK else \
            {1, 2, 3, slack // 2, slack - 1, slack, slack + 1, slack + 3}
        for k in sorted({-(-c // n) for c in cycles if c > 0}):
            add("nop", f"{ordinal}x{k}", insert_before(lines, i, ["        NOP"] * k),
                site=ordinal)
        reg = split_line(lines[i])[2].split()[1].rstrip(",").upper()
        for j in range(i - 1, max(i - 30, -1), -1):
            m = re.match(r"ADDS\s+(R\d)\s*,\s*(R\d)\s*,", split_line(lines[j])[2], re.I)
            if m and m.group(1).upper() == reg and m.group(2).upper() == reg:
                add("noadv", str(ordinal), delete_at(lines, j), site=ordinal)
                break
        add("nowait", str(ordinal), delete_at(lines, i))

    for ln in lines:
        m = re.match(r"\s*\.equ\s+(\w+)\s*,\s*(\d+)\s*(;.*)?$", ln, re.I)
        if m and int(m.group(2)) >= 8:
            add("equ", m.group(1), defines={m.group(1): int(m.group(2)) // 4})

    swap = {"JZ": "JNZ", "JNZ": "JZ", "JP": "JN", "JN": "JP"}
    for i, ln in enumerate(lines):
        ind, labels, ins, com = split_line(ln)
        m = re.match(r"(JZ|JNZ|JP|JN)\b(.*)", ins, re.I)
        if m:
            new = f"{ind}{labels}{swap[m.group(1).upper()]}{m.group(2)}  {com}".rstrip()
            add("branch", f"line{i + 1}", lines[:i] + [new] + lines[i + 1:])

    for m in out:
        m["hash"] = hashlib.sha256((text + m["id"]).encode()).hexdigest()
    out.sort(key=lambda m: m["hash"])
    return out


# ------------------------------------------------------------------ one mutant
def worker(job_path):
    """Verdict and RTL run of one mutant, in a process of its own."""
    from run_tests import fix_dll_path, project_sources, SRC
    from cocotb_tools.runner import get_runner

    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    work = os.path.dirname(job_path)
    src = os.path.join(work, job["firmware"])
    with open(src, "w", encoding="utf-8") as f:
        f.write(job["source"])
    res = {"id": job["id"], "kind": job["kind"], "firmware": job["firmware"]}
    try:
        an, words, _ = msf_timing.analyse(src, None, job["defines"] or None)
    except Exception as e:                # it no longer assembles
        res.update(verdict="not plantable", why=f"{type(e).__name__}: {e}"[:120])
        return res
    if len(words) > MAX_WORDS:
        res.update(verdict="not plantable", why=f"{len(words)} words")
        return res
    cert = msf_timing.certified(an)
    site_pc = sorted(an.deadlines)[job["site"]] if job.get("site") is not None and \
        job["site"] < len(an.deadlines) else None

    out = os.path.join(work, "out.json")
    desc = os.path.join(work, "mutant.json")
    with open(desc, "w", encoding="utf-8") as f:
        json.dump({"firmware": job["firmware"], "source": src, "out": out,
                   "defines": job["defines"]}, f)
    fix_dll_path()
    module, tests = TESTS[job["firmware"]]
    runner = get_runner("icarus")
    log = os.path.join(work, "sim.log")
    with open(log, "w", encoding="utf-8") as lf:
        so, se = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = lf
        try:
            # (the runner keeps its state from build(): up to date, this builds nothing)
            runner.build(sources=[str(SRC / s) for s in project_sources()]
                         + [os.path.join(TEST, "tb.v")], hdl_toplevel="tb", always=False,
                         build_dir=job["build"], includes=[str(SRC)], build_args=["-g2012"],
                         waves=False)
            runner.test(test_module=module, hdl_toplevel="tb", build_dir=job["build"],
                        test_dir=TEST, waves=False, testcase=[f"{module}.{t}" for t in tests],
                        extra_env={"MSF_FW_MUTANT": desc},
                        results_xml=os.path.join(work, "results.xml"))
        except Exception as e:             # a failing test is not the point here
            lf.write(f"runner: {e}\n")
            res["runner"] = str(e)[:120]
        finally:
            sys.stdout, sys.stderr = so, se
    seen = {"seen": {}, "below": [], "flag": False}
    if os.path.exists(out):
        with open(out, encoding="utf-8") as f:
            seen = json.load(f)
    late = seen["flag"] or any(m[0] < 1 for m in seen["seen"].values())
    below = bool(seen["below"])
    reached = (str(site_pc) in seen["seen"]) if site_pc is not None else bool(seen["seen"])
    if cert:
        if late or below:
            verdict = "CERTIFIED, LATE"
            res["detail"] = {"below": seen["below"][:4], "flag": seen["flag"],
                             "seen": seen["seen"]}
        else:
            verdict = "certified" if reached else "certified, unseen"
    else:
        verdict = "refused, late" if late else "refused"
    res["verdict"] = verdict
    return res


def run_one(m, build):
    work = tempfile.mkdtemp(prefix="fwmut")
    job = os.path.join(work, "job.json")
    with open(job, "w", encoding="utf-8") as f:
        json.dump({**m, "build": build}, f)
    try:
        p = subprocess.run([sys.executable, os.path.abspath(__file__), "--worker", job],
                           capture_output=True, text=True, timeout=1200)
        try:
            return json.loads(p.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            return {"id": m["id"], "kind": m["kind"], "firmware": m["firmware"],
                    "verdict": "error", "why": (p.stderr or p.stdout)[-300:]}
    except subprocess.TimeoutExpired:
        return {"id": m["id"], "kind": m["kind"], "firmware": m["firmware"],
                "verdict": "error", "why": "timeout"}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def build_sim():
    from run_tests import fix_dll_path, project_sources, SRC
    from cocotb_tools.runner import get_runner
    fix_dll_path()
    get_runner("icarus").build(
        sources=[str(SRC / s) for s in project_sources()] + [os.path.join(TEST, "tb.v")],
        hdl_toplevel="tb", always=True, build_dir=BUILD, includes=[str(SRC)],
        build_args=["-g2012"], waves=False)
    return BUILD


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--firmware", help="comma list (default: all eight)")
    ap.add_argument("--kind", help="comma list of mutant kinds")
    ap.add_argument("--sample", type=int, help="the first N mutants per firmware, by hash")
    ap.add_argument("--quick", action="store_true", help="4 NOP counts per deadline instead of 8")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--json", help="write every mutant's result here")
    ap.add_argument("--list", action="store_true", help="list the mutants and stop")
    ap.add_argument("--worker", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.worker:
        print(json.dumps(worker(a.worker)))
        return 0
    names = a.firmware.split(",") if a.firmware else sorted(TESTS)
    todo = []
    for name in names:
        ms = mutants_of(name)
        if a.kind:
            ms = [m for m in ms if m["kind"] in a.kind.split(",")]
        todo += ms[:a.sample] if a.sample else ms
    print(f"{len(todo)} mutants of {len(names)} firmwares", flush=True)
    if a.list:
        for m in todo:
            print(m["id"])
        return 0
    build = build_sim()
    results = []
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        for i, r in enumerate(ex.map(lambda m: run_one(m, build), todo)):
            results.append(r)
            print(f"[{i + 1}/{len(todo)}] {r['id']}: {r['verdict']}", flush=True)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1)
    order = ["CERTIFIED, LATE", "certified", "certified, unseen", "refused, late",
             "refused", "not plantable", "error"]
    kinds = ["nop", "noadv", "nowait", "equ", "branch"]
    print("\n| Verdict | " + " | ".join(kinds) + " | all |")
    print("|---|" + "---:|" * (len(kinds) + 1))
    for v in order:
        row = [sum(1 for r in results if r["verdict"] == v and r["kind"] == k) for k in kinds]
        print(f"| {v} | " + " | ".join(map(str, row)) + f" | {sum(row)} |")
    bad = [r for r in results if r["verdict"] == "CERTIFIED, LATE"]
    for r in bad:
        print("CHECKER BUG?", r["id"], r.get("detail"))
    for r in results:
        if r["verdict"] == "error":
            print("error:", r["id"], r.get("why", "")[:200])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
