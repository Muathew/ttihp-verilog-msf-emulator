#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Mutation score: how many deliberately broken copies of the RTL the test
suite catches.

Yosys (`mutate -list`) samples N single-point mutations evenly over the
logic (an inverted bit, a constant-0 or constant-1 bit, two bits swapped)
of every module but the SRAM macro and its wrapper. The tests are first run
on the unmutated design, and the run stops if any fails (a failing test
would otherwise "kill" every mutant). Each mutant is written back as
Verilog in place of its module, built once with Icarus, and run against
the test modules, fastest first; the first failing module kills it. A mutant Icarus cannot build is reported and left out of
the score. Survivors are listed with their source location, to be read by
hand: some mutate logic that cannot change behaviour (equivalent mutants),
the rest are gaps in the tests.

Usage (from the repository root, project venv active):
    python tools/mutation.py [-n 40] [--seed 1] [--yosys yowasp-yosys]
                             [--tests test_core,test_isa_v2,...] [--out DIR]
                             [--src tt_um_msf_serdes.v:137.45-137.51]
(--src re-runs only the sampled mutants at that location, e.g. a survivor
after a test was added for it; --no-baseline skips the unmutated run.)
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO, "test"))
import run_tests  # noqa: E402  (source list, DLL path fix-up)

# the SRAM macro and its model are not ours; the wrapper is only port wiring
# under `SYNTHESIS` (which Yosys defines), so its written-back mutant would
# be the bare macro instance, not the simulation model the tests need
SKIP = ("RM_IHPSG13", "tt_um_msf_sram")
# the fast modules first, then every other one (a mutant that only one
# protocol's firmware exercises survives the fast list: 10 of 40 did on v2.2)
FAST_TESTS = ["test_core", "test_isa_v2", "test_threads", "test_peripherals",
              "test_firmware", "test_regressions", "test_boot", "test_random_diff"]
DEFAULT_TESTS = FAST_TESTS + [m for m in run_tests.ALL_MODULES if m not in FAST_TESTS]
NOT_BUILT = "(not built)"


def yosys(cmd, workdir, script):
    with open(os.path.join(workdir, "run.ys"), "w") as f:
        f.write(script)
    p = subprocess.run(cmd + ["-q", "-l", "log.txt", "-s", "run.ys"], cwd=workdir,
                       capture_output=True, text=True)
    log = open(os.path.join(workdir, "log.txt")).read() if os.path.exists(
        os.path.join(workdir, "log.txt")) else ""
    if p.returncode:
        raise RuntimeError(f"yosys failed:\n{log[-2000:]}{p.stderr[-2000:]}")
    return log


def read_design(files):
    return "".join(f"read_verilog -sv {os.path.basename(f)}\n" for f in files) + \
        "hierarchy -top tt_um_msf\nproc\nopt_clean\n"


def module_files(files):
    """module name -> the source file that defines it."""
    out = {}
    for f in files:
        for m in re.findall(r"^\s*module\s+(\w+)", open(f).read(), flags=re.M):
            out[m] = f
    return out


def run_mutant(src_dir, tests):
    """Build the RTL in src_dir and run tests in order; returns the first
    failing module, None if all pass, or NOT_BUILT."""
    from cocotb_tools.runner import get_runner
    runner = get_runner("icarus")
    build = os.path.join(REPO, "test", "sim_build", "mutant")
    try:
        runner.build(sources=[os.path.join(src_dir, s) for s in run_tests.project_sources()]
                     + [os.path.join(REPO, "test", "tb.v")],
                     hdl_toplevel="tb", always=True, build_dir=build,
                     includes=[src_dir], build_args=["-g2012"], waves=False)
    except subprocess.CalledProcessError:
        return NOT_BUILT
    for m in tests:
        xml = os.path.join(build, "results.xml")
        if os.path.exists(xml):
            os.remove(xml)
        try:
            runner.test(test_module=m, hdl_toplevel="tb", build_dir=build,
                        test_dir=os.path.join(REPO, "test"), waves=False,
                        results_xml=xml)
        except Exception:             # the simulator crashed or hung up: killed
            return m
        fails = [t.find(tag) for t in ET.parse(xml).iter("testcase")
                 for tag in ("failure", "error") if t.find(tag) is not None]
        # a test that reads an internal signal the mutant's netlist renamed
        # (Yosys turns some arrays into separate registers) cannot judge it:
        # only real failures kill, and that module is skipped otherwise
        real = [f for f in fails if "contains no child object" not in
                (f.get("message", "") + (f.text or ""))]
        if real:
            return m
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-n", type=int, default=40, help="number of mutants")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--yosys", default=os.environ.get("YOSYS", "yowasp-yosys"))
    ap.add_argument("--tests", default=",".join(DEFAULT_TESTS))
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "msf_mutants"))
    ap.add_argument("--src", help="only the sampled mutants at these source locations (comma-separated)")
    ap.add_argument("--no-baseline", action="store_true",
                    help="do not run the tests on the unmutated design first")
    a = ap.parse_args()
    run_tests.fix_dll_path()
    ycmd, tests = a.yosys.split(), a.tests.split(",")
    files = [os.path.join(run_tests.SRC, s) for s in run_tests.project_sources()]
    logic = [f for f in files if not any(k in f for k in SKIP)]
    mods = {m: f for m, f in module_files(logic).items()}
    os.makedirs(a.out, exist_ok=True)
    work = os.path.join(a.out, "yosys")
    os.makedirs(work, exist_ok=True)
    for f in os.listdir(run_tests.SRC):           # (yowasp only sees its cwd)
        if f.endswith(".v"):
            text = open(os.path.join(run_tests.SRC, f)).read()
            # Yosys reads a comment after `include as part of the file name
            text = re.sub(r'(`include\s+"[^"]+")[^\n]*', r"\1", text)
            open(os.path.join(work, f), "w").write(text)

    sel = " ".join(mods)
    yosys(ycmd, work, read_design(files)
          + f"mutate -list {2 * a.n} -seed {a.seed} -o list.txt {sel}\n")
    # ($meminit: a memory's initial contents at constant addresses, which
    # an inverter on the address would make unwritable; not a logic fault)
    muts = [ln.strip() for ln in open(os.path.join(work, "list.txt"))
            if ln.startswith("mutate") and "$meminit" not in ln][:a.n]
    if a.src:
        muts = [m for m in muts if any(f"-src {loc} " in m + " " for loc in a.src.split(","))]
    print(f"{len(muts)} mutants over {len(mods)} modules; tests: {', '.join(tests)}", flush=True)
    if not a.no_baseline:
        t0 = time.time()
        by = run_mutant(run_tests.SRC, tests)
        if by:
            raise SystemExit(f"the unmutated design fails {by}: fix that first")
        print(f"baseline: every test passes on the unmutated design ({time.time() - t0:.0f} s)",
              flush=True)

    killed, survivors, unbuilt, t0 = 0, [], [], time.time()
    for i, mut in enumerate(muts):
        mod = re.search(r"-module (\S+)", mut).group(1).lstrip("\\")
        src = re.search(r"-src (\S+)", mut)
        yosys(ycmd, work, read_design(files) + mut + "\nopt_clean\n"
              + f"select {mod}\nwrite_verilog -noattr -selected mutant.v\n")
        mdir = os.path.join(a.out, "src")
        shutil.rmtree(mdir, ignore_errors=True)
        shutil.copytree(run_tests.SRC, mdir)
        shutil.copy(os.path.join(work, "mutant.v"), os.path.join(mdir, os.path.basename(mods[mod])))
        by = run_mutant(mdir, tests)
        where = src.group(1) if src else mod
        mode = re.search(r"-mode (\S+)", mut).group(1)
        if by == NOT_BUILT:
            unbuilt.append(f"{where} ({mode})")
            verdict = "NOT BUILT"
        elif by:
            killed += 1
            verdict = "killed by " + by
        else:
            survivors.append(f"{where} ({mode})")
            verdict = "SURVIVED"
        print(f"[{i + 1}/{len(muts)}] {mode:6s} {where:40s} {verdict}  "
              f"({time.time() - t0:.0f} s)", flush=True)
    built = len(muts) - len(unbuilt)
    print(f"\nmutation score: {killed}/{built} killed "
          f"({100.0 * killed / max(1, built):.0f} %)"
          + (f"; {len(unbuilt)} not built" if unbuilt else ""))
    for s in survivors:
        print("  survived:", s)
    for s in unbuilt:
        print("  not built:", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
