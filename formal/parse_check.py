#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Parse and elaborate the formal targets with Yosys, without running a solver.

`run_formal.py` needs a working `sat` pass or SymbiYosys with `yosys-abc`; where neither
exists (the YoWASP install on the development PC aborts inside `sat`) this still catches
syntax errors, width mismatches and unresolved names in the `ifdef FORMAL code
(F1-F18), which is where a typo hides until CI runs the real thing.

Usage:  python formal/parse_check.py [--yosys PATH] [ctrl cap top ni]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_formal as rf  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    ap.add_argument("targets", nargs="*", default=["ctrl", "cap", "top", "ni"])
    a = ap.parse_args()
    bad = 0
    for t in a.targets:
        top, files = rf.TARGETS[t]
        d = rf.prepare()
        reads = "; ".join(f"read_verilog -formal -sv -DFORMAL -DSYNTHESIS -I. {f}" for f in files)
        p = subprocess.run(a.yosys.split() + ["-q", "-l", "log.txt", "-p",
                                              f"{reads}; prep -top {top}; stat -width; "
                                              f"flatten; check"],
                           cwd=d, capture_output=True, text=True)
        log = os.path.join(d, "log.txt")
        text = p.stdout + p.stderr + (open(log, errors="replace").read() if os.path.exists(log) else "")
        asserts = sum(1 for l in text.splitlines() if "$assert" in l and "_EN" in l)
        errors = [l for l in text.splitlines() if "ERROR" in l]
        # An input nobody drives becomes a free value in the solver's flow. That is meant for
        # the SRAM stub's read data (formal/sram_stub.v) and nothing else: on 12 Nov the two-copy miter
        # had left v4's new controller inputs open, so each copy would have had its own.
        # A port connected at the wrong width is the same kind of slip.
        loose = sorted({l.strip() for l in text.splitlines()
                        if ("has no driver" in l and not re.search(r"[ab]_free|[AB]_DOUT", l))
                        or "Resizing cell port" in l})
        ok = p.returncode == 0 and not loose
        print(f"{t:5s} {'ok' if ok else 'FAILED'}  ({asserts} assertion cells)")
        for l in errors[:5] + loose[:8]:
            print("     ", l.strip()[:200])
        bad += not ok
        shutil.rmtree(d, ignore_errors=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
