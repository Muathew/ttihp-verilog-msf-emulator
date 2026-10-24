#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Power per workload of the hardened netlist (docs/verification.md §7b).

Step 1 (--vcd): simulate the gate-level netlist (Icarus, the CMOS5L cell
models, test/tb_power.v) running each workload of test/power_workloads.py
and record a VCD of the steady state. Step 2 (--sta): read the netlist, the
parasitics (SPEF) and the typical-corner liberty files into OpenSTA, apply
each VCD's activity and report the power. Activity from a zero-delay
simulation has no glitches, so glitch power is not counted.

Usage:
  python tools/power.py --vcd OUT_DIR --netlist NET.v --cells DIR
         (DIR: sg13cmos5l_io.v, sg13cmos5l_udp.v, sg13cmos5l_stdcell.v)
  python tools/power.py --sta OUT_DIR --netlist NET.v --spef NET.spef --pdk PDK_ROOT
  [--workloads uart_tx,threads,halt] [--period-ns 80]
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
TEST = os.path.join(REPO, "test")
MACRO = os.path.join(REPO, "macro", "RM_IHPSG13_2P_512x16_c2_bm_bist",
                     "RM_IHPSG13_2P_512x16_c2_bm_bist_typ_1p20V_25C.lib")
STDLIB = "ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib"


def vcds(a, workloads):
    sys.path.insert(0, TEST)
    import run_tests
    from cocotb_tools.runner import get_runner
    run_tests.fix_dll_path()
    srcs = [os.path.join(a.cells, f) for f in ("sg13cmos5l_io.v", "sg13cmos5l_udp.v",
                                               "sg13cmos5l_stdcell.v")]
    srcs += [os.path.join(REPO, "src", "RM_IHPSG13_2P_512x16_c2_bm_bist_sim.v"),
             os.path.abspath(a.netlist), os.path.join(TEST, "tb_power.v")]
    out = os.path.abspath(a.vcd)
    r = get_runner("icarus")
    r.build(sources=srcs, hdl_toplevel="tb", build_dir=out, always=True,
            includes=[os.path.join(REPO, "src")],
            defines={"GL_TEST": 1, "FUNCTIONAL": 1, "SIM": 1}, build_args=["-g2012"],
            waves=False)
    # keep VCD dumping on (the runner passes -none without waves)
    cmd = r._test_command
    r._test_command = lambda: [[c for c in x if c != "-none"] for x in cmd()]
    for w in workloads:
        r.test(test_module="power_workloads", test_filter=w, hdl_toplevel="tb",
               build_dir=out, test_dir=out, extra_env={"PYTHONPATH": TEST}, waves=False,
               plusargs=[f"+vcd={w}.vcd"], results_xml=os.path.join(out, f"{w}.xml"))


def sta(a, workloads):
    lib = os.path.join(a.pdk, STDLIB)
    rows = []
    for w in workloads:
        tcl = f"""read_liberty {lib}
read_liberty {MACRO}
read_verilog {os.path.abspath(a.netlist)}
link_design tt_um_msf
read_spef {os.path.abspath(a.spef)}
create_clock -name clk -period {a.period_ns} [get_ports clk]
set_propagated_clock [all_clocks]
read_vcd -scope tb/user_project {os.path.join(os.path.abspath(a.sta), w + '.vcd')}
report_power
exit
"""
        p = os.path.join(a.sta, f"{w}.tcl")
        open(p, "w").write(tcl)
        r = subprocess.run(["sta", "-no_splash", "-exit", p], capture_output=True, text=True)
        groups = {m.group(1): float(m.group(2)) * 1e3 for m in re.finditer(
            r"^(Sequential|Combinational|Clock|Macro|Total)\s+\S+\s+\S+\s+\S+\s+(\S+)",
            r.stdout, re.M)}
        if "Total" not in groups:
            print(r.stdout[-2000:] + r.stderr[-2000:])
            return 1
        rows.append((w, groups))
    print(f"power at {1e3 / a.period_ns:.1f} MHz, typical corner (mW)")
    print(f"{'workload':10s} {'total':>7s} {'flops':>7s} {'clock':>7s} {'SRAM':>7s} {'logic':>7s}")
    for w, g in rows:
        print(f"{w:10s} {g['Total']:7.2f} {g['Sequential']:7.2f} {g['Clock']:7.2f} "
              f"{g['Macro']:7.2f} {g['Combinational']:7.2f}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vcd", help="simulate and write the VCDs into this directory")
    ap.add_argument("--sta", help="report power from the VCDs in this directory")
    ap.add_argument("--netlist", required=True)
    ap.add_argument("--cells", help="directory with the CMOS5L cell Verilog models")
    ap.add_argument("--spef")
    ap.add_argument("--pdk", default=os.environ.get("PDK_ROOT", "/pdk"))
    ap.add_argument("--workloads", default="uart_tx,threads,halt")
    ap.add_argument("--period-ns", type=float, default=80.0)
    a = ap.parse_args()
    w = a.workloads.split(",")
    if a.vcd:
        vcds(a, w)
    if a.sta:
        return sta(a, w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
