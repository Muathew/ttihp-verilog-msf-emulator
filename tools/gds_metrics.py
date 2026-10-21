#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Results of the GDS (hardening) runs in CI, one row per run.

Downloads each successful run's `tt_submission` artifact with the GitHub
CLI (`gh`, logged in), reads LibreLane's stats/metrics.csv and prints a
markdown table: standard cells, utilisation, worst setup slack (slow
corner) and hold slack (fast corner), timing violations, routing DRC
errors, antenna violations and max-slew / max-fanout warnings.

Usage:
    python tools/gds_metrics.py [--runs 10] [--cache DIR]
Artifacts are kept in DIR (default: the system temp directory) so a second
run only lists. GitHub keeps artifacts for 90 days.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile

COLS = [
    ("cells", "design__instance__count__stdcell", "{:.0f}"),
    ("util", "design__instance__utilization", "{:.1%}"),
    ("setup slack ns (slow)", "timing__setup__ws__corner:nom_slow_1p08V_125C", "{:.2f}"),
    ("hold slack ns (fast)", "timing__hold__ws__corner:nom_fast_1p32V_m40C", "{:.2f}"),
    ("setup / hold viol.", None, None),
    ("route DRC", "route__drc_errors", "{:.0f}"),
    ("antenna", "antenna__violating__nets", "{:.0f}"),
    ("slew / fanout warn.", None, None),
]


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def metrics(run, cache):
    d = os.path.join(cache, str(run))
    path = os.path.join(d, "tt_submission", "stats", "metrics.csv")
    if not os.path.exists(path):
        gh("run", "download", str(run), "-n", "tt_submission", "-D", d)
    with open(path, newline="") as f:
        return {k: float(v) for k, v in csv.reader(f) if k != "Metric" and _num(v)}


def _num(v):
    try:
        float(v)
        return True
    except ValueError:
        return False


def row(m):
    out = []
    for name, key, fmt in COLS:
        if name.startswith("setup / hold"):
            out.append("{:.0f} / {:.0f}".format(m.get("timing__setup_vio__count", 0),
                                               m.get("timing__hold_vio__count", 0)))
        elif name.startswith("slew"):
            out.append("{:.0f} / {:.0f}".format(m.get("design__max_slew_violation__count", 0),
                                               m.get("design__max_fanout_violation__count", 0)))
        else:
            out.append(fmt.format(m[key]) if key in m else "-")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=10, help="how many recent GDS runs")
    ap.add_argument("--cache", default=os.path.join(tempfile.gettempdir(), "msf_gds_runs"))
    a = ap.parse_args()
    runs = json.loads(gh("run", "list", "--workflow", "gds", "--limit", str(a.runs), "--json",
                         "databaseId,conclusion,headBranch,headSha,createdAt"))
    print("| run | date | branch | commit | " + " | ".join(c[0] for c in COLS) + " |")
    print("|---" * (4 + len(COLS)) + "|")
    for r in runs:
        head = [str(r["databaseId"]), r["createdAt"][:10], r["headBranch"], r["headSha"][:7]]
        if r["conclusion"] != "success":
            print("| " + " | ".join(head) + f" | {r['conclusion']} |" + " |" * (len(COLS) - 1))
            continue
        try:
            print("| " + " | ".join(head + row(metrics(r["databaseId"], a.cache))) + " |")
        except subprocess.CalledProcessError as e:
            print("| " + " | ".join(head) + " | (no artifact: " + e.stderr.strip()[:40] + ") |"
                  + " |" * (len(COLS) - 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
