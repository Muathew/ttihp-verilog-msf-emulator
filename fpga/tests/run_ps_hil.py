#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the PS-side C protocol tests against the FPGA RTL (cocotb + Icarus).

    python fpga/tests/run_ps_hil.py                       # every C test
    python fpga/tests/run_ps_hil.py -t uart_tx_drift_free # one test

Generates the firmware images, builds fpga/ps/common + fpga/ps/hil with the
simulation HAL (fpga/ps/sim) into a shared library, then simulates
fpga/rtl/sources.f + the BUFGCE model with tb_ps_hil as the top. Needs gcc,
iverilog and the test/ requirements (cocotb). Builds into fpga/build/ps_hil.
Exit code 0 = all passed.
"""
import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from cocotb_tools.runner import get_runner

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PS = REPO / "fpga" / "ps"
BUILD = REPO / "fpga" / "build" / "ps_hil"
C_SOURCES = [PS / "common" / "msf_link.c", PS / "hil" / "msf_proto.c",
             PS / "hil" / "msf_tests.c", PS / "sim" / "msf_emio_sim.c"]


def build_library():
    BUILD.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(PS / "hil" / "gen_images.py"),
                    "-o", str(BUILD / "msf_images.h")], check=True)
    lib = BUILD / ("msf_ps_sim.dll" if os.name == "nt" else "msf_ps_sim.so")
    cmd = ["gcc", "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-shared",
           "-DMSF_HOST", f"-I{PS / 'common'}", f"-I{PS / 'hil'}", f"-I{BUILD}",
           *map(str, C_SOURCES), "-o", str(lib), "-lpthread"]
    cmd += ["-static"] if os.name == "nt" else ["-fPIC"]
    subprocess.run(cmd, check=True)
    print(f"[run_ps_hil] built {lib}")
    return lib


def fix_dll_path():
    """Same as test/run_tests.py: let vvp find python3xx.dll from a conda venv."""
    if os.name == "nt":
        base = Path(sys.base_prefix)
        extra = [str(base), str(base / "Library" / "bin"), str(base / "DLLs")]
        os.environ["PATH"] = os.pathsep.join(extra + [os.environ.get("PATH", "")])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-t", "--test", help="one C test, e.g. uart_tx_drift_free")
    ap.add_argument("--access-cycles", type=int, default=2,
                    help="PL clocks per PS GPIO access (default 2)")
    args = ap.parse_args()

    fix_dll_path()
    lib = build_library()
    rtl = [REPO / line.strip() for line in (REPO / "fpga" / "rtl" / "sources.f").read_text().splitlines()
           if line.strip()]
    sim_dir = BUILD / "sim"
    runner = get_runner("icarus")
    runner.build(
        sources=[str(p) for p in rtl] + [str(HERE / "sim_models" / "BUFGCE.v"),
                                         str(HERE / "tb_ps_hil.v")],
        hdl_toplevel="tb_ps_hil",
        always=True,
        build_dir=str(sim_dir),
        includes=[str(REPO / "fpga" / "rtl")],
        build_args=["-g2012"],
        waves=False,
    )
    results_xml = sim_dir / "results.xml"
    results_xml.unlink(missing_ok=True)
    results = runner.test(
        test_module="test_ps_hil",
        hdl_toplevel="tb_ps_hil",
        build_dir=str(sim_dir),
        test_dir=str(HERE),
        waves=False,
        testcase=[f"test_ps_hil.test_{args.test}"] if args.test else None,
        extra_env={"MSF_PS_SIM_LIB": str(lib),
                   "MSF_PS_ACCESS_CYCLES": str(args.access_cycles)},
        results_xml=str(results_xml),
    )

    def status(t):
        for tag in ("failure", "error", "skipped"):
            if t.find(tag) is not None:
                return tag
        return "passed"

    states = {t.get("name"): status(t) for t in ET.parse(results).iter("testcase")}
    states = {n: s for n, s in states.items() if s != "skipped"}
    passed = sum(s == "passed" for s in states.values())
    print(f"[run_ps_hil] {passed}/{len(states)} C tests passed")
    for name, s in states.items():
        if s != "passed":
            print(f"[run_ps_hil]   {s.upper()}: {name}")
    return 0 if states and passed == len(states) else 1


if __name__ == "__main__":
    sys.exit(main())
