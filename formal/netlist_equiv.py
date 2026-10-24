#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prove the hardened netlist equivalent to the RTL (docs/plan.md 3.4).

Builds one sequential miter of the RTL (src/, the files info.yaml lists)
and a gate-level netlist (the GDS action's tt_submission/tt_um_msf.v), with
the cells' logic read from the CMOS5L liberty, and gives it to ABC's
`dprove`. Flops need not match by name (87 of the netlist's lose their RTL
names in synthesis): dprove finds which signals correspond itself.

Both SRAM macros are cut out as black boxes (`expose -evert`): each macro
input pin becomes an output of both designs, which must match on every
cycle, and each macro output pin becomes an input shared by both, so the
result holds whatever the SRAM returns.

Both designs start in their reset state: Yosys simulates one cycle with
rst_n low and writes the result back as the flops' initial values (storage
without a reset, the register file and the input synchronisers, starts at
0 in both). Starting both at all-zero instead does not work: the netlist's
only flop resets to 0, so it keeps the reset-to-1 bits inverted; before a
reset the two disagree and dprove cannot pair those flops. So "equivalent"
means: from reset, the two have the same outputs and drive the SRAM pins
the same on every cycle, for every input sequence (including further
resets).

Usage:  python formal/netlist_equiv.py NETLIST [--liberty LIB]
               [--yosys yosys] [--abc yosys-abc] [--pdr-timeout 3600] [--keep DIR]
Exit status: 0 proved equivalent, 1 a counterexample, 2 undecided or error.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
SRC = os.path.join(REPO, "src")
LIB = "libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib"
SRAM = "RM_IHPSG13_2P_512x16_c2_bm_bist"

def rtl_sources():
    text = open(os.path.join(REPO, "info.yaml")).read()
    block = text.split("source_files:", 1)[1].split("\n\n", 1)[0]
    return [ln.strip().strip("- ").strip('"') for ln in block.splitlines()
            if ln.strip().startswith("- ")]


def script(work, netlist, liberty):
    rtl = " ".join(os.path.join(SRC, f) for f in rtl_sources())
    sram = os.path.join(SRC, SRAM + ".v")
    return f"""
read_verilog -sv -DSYNTHESIS -I{SRC} {rtl}
read_verilog -lib {sram}
prep -top tt_um_msf
memory -nomap
memory_map
flatten
expose -evert t:{SRAM}
rename tt_um_msf gold
design -stash gold
read_liberty -ignore_miss_func {liberty}
read_verilog -lib {sram}
read_verilog {netlist}
prep -top tt_um_msf
flatten
expose -evert t:{SRAM}
rename tt_um_msf gate
design -stash gate
design -copy-from gold -as gold gold
design -copy-from gate -as gate gate
miter -equiv -flatten gold gate miter
hierarchy -top miter
proc
flatten
sim -clock in_clk -resetn in_rst_n -rstlen 1 -n 1 -zinit -w
async2sync
delete t:$scopeinfo
techmap
dfflegalize -cell $_DFF_P_ 0
techmap
opt -fast -nodffe -nosdff
setundef -zero
aigmap
opt_clean
stat
zinit -all
write_aiger {os.path.join(work, 'miter.aig')}
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("netlist")
    ap.add_argument("--liberty", default=os.path.join(os.environ.get("PDK_ROOT", "/pdk"),
                                                      "ihp-sg13cmos5l", LIB))
    ap.add_argument("--yosys", default="yosys")
    ap.add_argument("--abc", default="yosys-abc")
    ap.add_argument("--pdr-timeout", type=int, default=3600)
    ap.add_argument("--keep", help="keep the miter and logs in this directory")
    a = ap.parse_args()
    work = a.keep or tempfile.mkdtemp(prefix="msf_equiv_")
    os.makedirs(work, exist_ok=True)
    open(os.path.join(work, "miter.ys"), "w").write(
        script(work, os.path.abspath(a.netlist), a.liberty))
    y = subprocess.run([a.yosys, "-q", "-l", os.path.join(work, "yosys.log"),
                        os.path.join(work, "miter.ys")], capture_output=True, text=True)
    if y.returncode:
        print(f"yosys failed:\n{y.stdout[-2000:]}{y.stderr[-2000:]}")
        return 2
    head = open(os.path.join(work, "miter.aig"), "rb").readline().split()
    # an AIGER header: aig M I L O A; a miter with no gates was folded to a constant
    print(f"miter: {head[2].decode()} inputs, {head[3].decode()} flops, "
          f"{head[5].decode()} AND gates")
    if int(head[5]) == 0:
        print("the miter has no logic left: it cannot fail, so it proves nothing")
        return 2
    r = subprocess.run([a.abc, "-c", f"read_aiger {os.path.join(work, 'miter.aig')}; "
                        f"dprove -v -F 8 -T {a.pdr_timeout}"], capture_output=True, text=True)
    open(os.path.join(work, "dprove.log"), "w").write(r.stdout + r.stderr)
    out = r.stdout.lower()
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    if "networks are equivalent" in out:
        print("EQUIVALENT: the netlist behaves as the RTL after any reset")
        return 0
    if "not equivalent" in out:
        print("NOT EQUIVALENT: dprove found a counterexample")
        return 1
    print("UNDECIDED: dprove neither proved nor refuted it within its limits")
    return 2


if __name__ == "__main__":
    sys.exit(main())
