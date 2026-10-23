#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail if a hardening run does not meet sign-off.

Tiny Tapeout's GDS action stays green with timing violations (on 21 Oct the
four-thread design passed it while missing 50 MHz by 3.35 ns at the slow
corner). This reads LibreLane's metrics from the run and exits 1 unless:
setup and hold slack are >= 0 in every corner, there are no setup or hold
violations, no routing DRC errors, no antenna violations and no LVS errors.
Max-slew and max-cap violations are listed as warnings. (Magic's DRC count
is not used: on IHP the sign-off DRC is KLayout's, in TT's precheck.)

Usage:  python tools/check_signoff.py PATH   (metrics.csv, or a directory
        holding stats/metrics.csv or final/metrics.csv)
"""
import csv
import os
import sys


def find(path):
    if os.path.isfile(path):
        return path
    for sub in ("stats/metrics.csv", "final/metrics.csv", "tt_submission/stats/metrics.csv",
                "runs/wokwi/final/metrics.csv"):
        p = os.path.join(path, sub)
        if os.path.isfile(p):
            return p
    raise SystemExit(f"check_signoff: no metrics.csv under {path}")


def main():
    path = find(sys.argv[1] if len(sys.argv) > 1 else ".")
    m = {}
    with open(path, newline="") as f:
        for row in csv.reader(f):
            if len(row) == 2:
                try:
                    m[row[0]] = float(row[1])
                except ValueError:
                    pass
    fails, warns = [], []
    for k in sorted(m):
        if k.startswith("timing__setup__ws__corner:") or k.startswith("timing__hold__ws__corner:"):
            kind = "setup" if "setup" in k else "hold"
            corner = k.split(":", 1)[1]
            line = f"{kind:5s} slack {m[k]:+8.3f} ns  {corner}"
            print(line)
            if m[k] < 0:
                fails.append(line)
    for k, what in (("timing__setup_vio__count", "setup violations"),
                    ("timing__hold_vio__count", "hold violations"),
                    ("route__drc_errors", "routing DRC errors"),
                    ("antenna__violating__nets", "antenna violations"),
                    ("design__lvs_error__count", "LVS errors")):
        v = int(m.get(k, 0))
        print(f"{what:20s} {v}")
        if v:
            fails.append(f"{v} {what}")
    for k, what in (("design__max_slew_violation__count", "max-slew violations"),
                    ("design__max_cap_violation__count", "max-cap violations")):
        v = int(m.get(k, 0))
        if v:
            warns.append(f"{v} {what}")
    for w in warns:
        print(f"warning: {w}")
    if fails:
        print("NOT signed off: " + "; ".join(fails))
        return 1
    print("signed off")
    return 0


if __name__ == "__main__":
    sys.exit(main())
