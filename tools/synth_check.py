#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pre-hardening area / timing sanity check against the IHP cells.

Maps the RTL onto a sg13cmos5l_stdcell (or sg13g2_stdcell) liberty with
Yosys + ABC and reports
cell count, standard-cell area and ABC's longest register-to-register
combinational delay. The real sign-off is the LibreLane GDS flow in CI;
this catches area/timing problems before pushing.

Usage:
    python tools/synth_check.py --liberty PATH/sg13cmos5l_stdcell_slow_1p08V_125C.lib \
        [--yosys yowasp-yosys] [--period-ns 20]
The liberty is in the IHP Open PDK: ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/.
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
TILE_UM2 = 202.08 * 154.98      # one IHP Tiny Tapeout tile
SRAM_UM2 = 402.61 * 219.77      # one RM_IHPSG13_2P_512x16 macro outline


def sources():
    text = open(os.path.join(HERE, "..", "info.yaml")).read()
    block = text.split("source_files:", 1)[1].split("\n\n", 1)[0]
    return re.findall(r'^\s*-\s*"([^"]+)"', block, flags=re.M)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--liberty", required=True)
    ap.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    ap.add_argument("--period-ns", type=float, default=20.0)
    ap.add_argument("--tiles", type=int, default=24)
    a = ap.parse_args()
    d_ps = int(a.period_ns * 1000)
    rtl = ["RM_IHPSG13_2P_512x16_c2_bm_bist.v"] + sources()
    files = rtl + ["tt_um_msf_bootrom_data.v"]
    lib = "cells.lib"
    reads = "\n".join(f"read_verilog -DSYNTHESIS -I. {f}" for f in rtl)
    ys = f"""{reads}
hierarchy -check -top tt_um_msf
synth -top tt_um_msf -flatten
ltp -noff * t:RM_IHPSG13_2P_512x16_c2_bm_bist %d
dfflibmap -liberty {lib}
abc -liberty {lib} -D {d_ps}
opt_clean
stat -liberty {lib}
"""
    # (relative paths only: the WebAssembly yosys build sees just its cwd)
    with tempfile.TemporaryDirectory() as td:
        for f in files:
            shutil.copy(os.path.join(SRC, f), td)
        shutil.copy(a.liberty, os.path.join(td, lib))
        open(os.path.join(td, "s.ys"), "w").write(ys)
        p = subprocess.run(a.yosys.split() + ["-q", "-l", "synth.log", "-s", "s.ys"],
                           capture_output=True, text=True, cwd=td)
        logf = os.path.join(td, "synth.log")
        out = (open(logf).read() if os.path.exists(logf) else "") + p.stdout + p.stderr
    if os.environ.get("SYNTH_LOG"):
        open(os.environ["SYNTH_LOG"], "w").write(out)
    if p.returncode:
        print(out[-4000:])
        return 1
    cells = re.findall(r"^\s+(\d+)\s+(?:\S+\s+)?cells\s*$", out, flags=re.M)
    area = re.findall(r"Chip area for (?:top )?module '\\?tt_um_msf':\s+([\d.]+)", out)
    ffs = sum(int(n) for n in re.findall(
        r"^\s+(\d+)\s+\S+\s+sg13(?:g2|cmos5l)_(?:dfrbp|sdfbbp|dllr)\w*\s*$", out, flags=re.M))
    macros = re.findall(r"^\s+(\d+)\s+(?:\S+\s+)?RM_IHPSG13_2P_512x16_c2_bm_bist\s*$", out,
                        flags=re.M)
    sram_um2 = (int(macros[-1]) if macros else 1) * SRAM_UM2
    ltp = re.findall(r"Longest topological path in .*?\(length=(\d+)\)", out)
    area_um2 = float(area[-1]) if area else float("nan")
    budget = a.tiles * TILE_UM2
    print(f"standard cells : {cells[-1] if cells else '?'} ({ffs} flip-flops)")
    print(f"cell area      : {area_um2:,.0f} um2  (+ SRAM macros {sram_um2:,.0f} um2)")
    print(f"budget         : {a.tiles} tiles = {budget:,.0f} um2 -> "
          f"{100 * (area_um2 + sram_um2) / budget:.1f} % used before routing")
    if ltp:
        n = int(ltp[-1])
        # generic 2-input gates (deeper than the mapped complex cells);
        # rough 0.15-0.30 ns per gate at the slow corner incl. wiring
        print(f"logic depth    : {n} generic gates on the longest path between registers/IO "
              f"(~{0.15 * n:.1f}-{0.30 * n:.1f} ns estimated vs {a.period_ns} ns; "
              f"SRAM clk->q adds a few ns; sign-off = LibreLane STA in CI)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
