#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the playground and results page into a folder, ready for GitHub Pages.

    python tools/site_build.py                     build into _site
    python tools/site_build.py --serve 8765        and serve it, to try it locally
    python tools/site_build.py --gds DIR           add the layout from a GDS run's artifacts

The page runs the repository's own tools in the browser, through Pyodide, so a copy of
them goes in. The table of firmware comes from the checker here and now, and the test
counts from the test modules; the layout's figures are in site/results.json, and
test/test_site.py holds them against docs/verification.md.
"""
import argparse
import glob
import http.server
import json
import os
import random
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import msf_capture  # noqa: E402
import msf_timing  # noqa: E402

TOOLS = ["test/assembler.py", "test/isa_model.py", "test/bootloader.s", "tools/msf_timing.py",
         "tools/msf_capture.py", "tools/msf_learn.py", "tools/msf_gen.py", "site/playground.py"]
PAGE = ["index.html", "worker.js"]
RATE = 1_000_000       # the examples' sample rate: one sample is 1 us


def firmware_table():
    rows = []
    for path in sorted(glob.glob(os.path.join(ROOT, "firmware", "*.s"))):
        an, words, _ = msf_timing.analyse(path)
        lows = [d["lo"] for pc, d in an.deadlines.items() if not an.on_purpose(pc)]   # not the `@late` ones
        with open(path, encoding="utf-8") as f:
            first = f.readline().lstrip("; ").strip()
        rows.append({"name": os.path.basename(path), "words": len(words), "threads": an.n,
                     "deadlines": len(lows), "slack": min(lows) - 1 if lows else None,
                     "certified": bool(msf_timing.certified(an)), "what": first})
    return rows


def test_counts():
    tests = modules = 0
    for path in glob.glob(os.path.join(ROOT, "test", "test_*.py")):
        with open(path, encoding="utf-8") as f:
            n = len(re.findall(r"^@cocotb\.test\(", f.read(), flags=re.M))
        if n:
            tests += n
            modules += 1
    return {"cocotb_tests": tests, "modules": modules}


def _events(runs):
    t, out = 0, []
    for level, d in runs:
        out.append((t, level))
        t += d
    return out, t


def nec_runs():
    """A NEC remote, from the specification, pressing four buttons of address 0x10."""
    runs = [(1, 50_000)]
    for cmd in (0x12, 0x34, 0x77, 0xA9):
        word = 0x10 | (0x10 ^ 0xFF) << 8 | cmd << 16 | (cmd ^ 0xFF) << 24
        runs += [(0, 9000), (1, 4500)]
        for i in range(32):
            runs += [(0, 562), (1, 1687 if word >> i & 1 else 562)]
        runs += [(0, 562), (1, 40_000)]
    return runs


def manchester_runs():
    """12 bits a frame, each a transition in the middle of 400 us, idle high."""
    runs = [(1, 20_000)]
    for word in (0xA5C, 0x3F1, 0x70E, 0x9B2, 0x164):
        levels = []
        for i in range(12):
            levels += [1, 0] if word >> i & 1 else [0, 1]
        for v in levels:
            if runs[-1][0] == v:
                runs[-1] = (v, runs[-1][1] + 400)
            else:
                runs.append((v, 400))
        runs.append((1, 20_000)) if runs[-1][0] != 1 else runs.__setitem__(-1, (1, runs[-1][1] + 20_000))
    return runs


def serial_runs():
    """9600 baud, 8N1."""
    bit = round(RATE / 9600)
    levels = [(1, 5000)]
    for ch in b"Hello, chip! 0123456789":
        frame = [0] + [ch >> i & 1 for i in range(8)] + [1]
        for v in frame:
            if levels[-1][0] == v:
                levels[-1] = (v, levels[-1][1] + bit)
            else:
                levels.append((v, bit))
    return levels


def noise_runs():
    """Random widths, with no code in them: this one must be refused."""
    rng = random.Random(7)
    return [(i & 1, rng.randint(40, 2000)) for i in range(600)]


EXAMPLES = {"nec.vcd": nec_runs, "manchester.vcd": manchester_runs, "serial.vcd": serial_runs,
            "noise.vcd": noise_runs}


def write_examples(out):
    os.makedirs(out, exist_ok=True)
    for name, make in EXAMPLES.items():
        events, end = _events(make())
        msf_capture.write_vcd(os.path.join(out, name), events, end, RATE, ["line"] + [f"unused{i}" for i in range(1, 8)])


def build(out, gds=None):
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    for name in PAGE:
        shutil.copy(os.path.join(ROOT, "site", name), out)
    files = list(TOOLS)
    for path in sorted(glob.glob(os.path.join(ROOT, "firmware", "*"))):
        if path.endswith((".s", ".inc")):
            files.append("firmware/" + os.path.basename(path))
    for name in files:
        dst = os.path.join(out, "repo", name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            text = f.read()
        with open(dst, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(files, f)
    write_examples(os.path.join(out, "examples"))

    with open(os.path.join(ROOT, "site", "results.json"), encoding="utf-8") as f:
        results = json.load(f)
    results["firmware"] = firmware_table()
    results["tests"] = test_counts()
    draw = os.path.join(ROOT, "docs", "draw", "draw.json")
    if os.path.exists(draw):
        with open(draw, encoding="utf-8") as f:
            d = json.load(f)
        results["draw_record"] = {k: d[k] for k in ("round", "randomness", "drawn")}
    if gds:
        with open(os.path.join(gds, "tt_submission", "pdk.json"), encoding="utf-8") as f:
            results["layout"]["pdk"] = json.load(f)["PDK"]
        shutil.copy(glob.glob(os.path.join(gds, "tt_submission", "*.oas"))[0], os.path.join(out, "tinytapeout.oas"))
        shutil.copy(os.path.join(gds, "gds_render", "gds_render.png"), out)
    with open(os.path.join(out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "_site"))
    ap.add_argument("--serve", type=int, metavar="PORT")
    ap.add_argument("--gds", metavar="DIR", help="holding tt_submission/ and gds_render/ from a GDS run")
    a = ap.parse_args()
    results = build(a.out, a.gds)
    n = results["firmware"]
    print(f"{a.out}: {len(n)} firmware, {sum(r['deadlines'] for r in n)} deadlines, "
          f"{sum(1 for r in n if r['deadlines'] and not r['certified'])} not certified, "
          f"{results['tests']['cocotb_tests']} tests in {results['tests']['modules']} modules")
    if a.serve:
        os.chdir(a.out)
        print(f"http://localhost:{a.serve}/")
        http.server.ThreadingHTTPServer(("localhost", a.serve), http.server.SimpleHTTPRequestHandler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
