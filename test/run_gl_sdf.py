# SPDX-License-Identifier: Apache-2.0
"""Gate-level tests with the sign-off delays (SDF), at the clock the layout is signed off for.

    python run_gl_sdf.py --final DIR --corner slow           # or typ, fast; none = zero delay
    python run_gl_sdf.py --final DIR --corner slow --random-sram 1   # SRAM powers up random

DIR is LibreLane's `final/` of a hardening run: in CI, the GDS job's `GDS_logs` artifact
(`gh run download RUN -n GDS_logs`, then `runs/wokwi/final`). It holds the netlist the
`gl_test` job uses (`nl/tt_um_msf.nl.v`) and one SDF per corner (`sdf/<corner>/`).

It runs `test_gl_smoke` and `test_gl_firmware` (boot through the parallel loader, a UART
program, two threads, a SPI flash) at `--clk-ns` (default 20 ns, 50 MHz), with every cell
and wire delay of the corner, the SRAM macros' read time included, and first
`gl_sdf_probe`, which fails if the delays did not reach the cells.

Needs the PDK's cell models (PDK_ROOT, or --pdk-root) and an Icarus Verilog that drives the
`delayed_*` nets of the cells' timing checks (Icarus 14 from OSS CAD Suite does; older builds
leave them undriven, so no flop clocks, and this script stops). Icarus runs no timing checks:
a setup or hold violation shows only as wrong behaviour at the pins; STA signs those off.
Annotation takes about 25 minutes, the five tests about 15 more.

What Icarus needs that LibreLane's files do not give it directly, made in the build directory:
* `-gspecify -ginterconnect`, for cell and wire delays.
* The SRAM instances renamed: Icarus's SDF reader takes the escaped `\\u_sram.sram0` as
  hierarchy (u_sram, then sram0), so they become `u_sram_sram0` in the netlist and the SDF.
* One IOPATH per SRAM read port: the reader rejects a bit-select in an IOPATH port
  (`A_DOUT[0]`) and stops at the first SRAM cell. The 16 bits become one vector path with
  the slowest bit's delays (they differ by about 0.1 ns); the macro's timing checks go.
* The SRAM model (`src/RM_IHPSG13_2P_512x16_c2_bm_bist_sim.v`) with module paths for the
  SDF to annotate, and, with --random-sram, random words at power-up instead of zeros.
* `tb.v` with an `$sdf_annotate` of the design under test.
"""
import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from cocotb_tools.runner import get_runner

HERE = Path(__file__).parent.resolve()
REPO = HERE.parent
SRC = REPO / "src"
SRAM_SIM = SRC / "RM_IHPSG13_2P_512x16_c2_bm_bist_sim.v"
CORNERS = {"slow": "nom_slow_1p08V_125C", "typ": "nom_typ_1p20V_25C", "fast": "nom_fast_1p32V_m40C"}
MODULES = "test_gl_smoke,test_gl_firmware"
BS = chr(92)
# SDF messages that are not errors in the annotation: the header's VOLTAGE and TEMPERATURE
# triples leave the typical value empty, and Icarus runs no timing checks.
BENIGN = ("Chosen value not defined", "TIMINGCHECK not supported",
          "multi-bit module path delays are currently not fully supported",
          "$sdf_annotate currently only uses the first two argument")


def plain(name):
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def netlist_fix(text):
    """\\u_sram.sram0 -> u_sram_sram0 (and their tie cells); other escaped names stay."""
    def fix(m):
        name = m.group(1)
        return (plain(name) if name.startswith("u_sram.") else BS + name) + m.group(2)
    return re.sub(re.escape(BS) + r"(\S+)(\s)", fix, text)


SDF_TOKEN = re.compile(r"(?:[A-Za-z0-9_]|" + re.escape(BS) + r".)+")
IOPATH_BIT = re.compile(r"\(IOPATH (\w+) (\w+)\[\d+\] \(([\d.]+):([\d.]+):([\d.]+)\) "
                        r"\(([\d.]+):([\d.]+):([\d.]+)\)\)")


def sdf_fix(text):
    text = SDF_TOKEN.sub(lambda m: plain(m.group(0).replace(BS, "")) if BS in m.group(0)
                         else m.group(0), text)
    lines, out, i, n = text.split("\n"), [], 0, 0
    while i < len(lines):
        if lines[i].strip() != "(CELL":
            out.append(lines[i]); i += 1
            continue
        depth, j = 0, i
        while True:
            depth += lines[j].count("(") - lines[j].count(")")
            if depth == 0:
                break
            j += 1
        block = lines[i:j + 1]
        if any('CELLTYPE "RM_IHPSG13' in l for l in block):
            worst = {}
            for l in block:
                m = IOPATH_BIT.search(l)
                if m:
                    v = [float(x) for x in m.groups()[2:]]
                    k = (m.group(1), m.group(2))
                    worst[k] = [max(a, b) for a, b in zip(worst.get(k, v), v)]
            cell = next(l for l in block if "CELLTYPE" in l)
            inst = next(l for l in block if "(INSTANCE" in l)
            block = [" (CELL", cell, inst, "  (DELAY", "   (ABSOLUTE"]
            block += ["    (IOPATH %s %s (%.3f:%.3f:%.3f) (%.3f:%.3f:%.3f))" % (k + tuple(v))
                      for k, v in sorted(worst.items())]
            block += ["   )", "  )", " )"]
            n += 1
        out += block
        i = j + 1
    if n != 4:
        raise SystemExit("run_gl_sdf: expected 4 SRAM cells in the SDF, found %d" % n)
    return "\n".join(out)


def sram_model():
    s = SRAM_SIM.read_text()
    init = ("  initial\n    for (init_i = 0; init_i < 512; init_i = init_i + 1)\n"
            "      mem[init_i] = 16'h0;\n")
    rand = ("`ifdef SRAM_RANDOM\n  integer seed = `SRAM_RANDOM;\n"
            "  initial\n    for (init_i = 0; init_i < 512; init_i = init_i + 1)\n"
            "      mem[init_i] = $random(seed);\n`else\n" + init + "`endif\n")
    end = "  assign B_DOUT = b_dout_r;\nendmodule"
    paths = ("  assign B_DOUT = b_dout_r;\n\n  // module paths for the SDF (zero until annotated)\n"
             "  specify\n    (A_CLK *> A_DOUT) = 0;\n    (B_CLK *> B_DOUT) = 0;\n  endspecify\nendmodule")
    if s.count(init) != 1 or s.count(end) != 1:
        raise SystemExit("run_gl_sdf: %s has changed; update sram_model()" % SRAM_SIM.name)
    return s.replace(init, rand).replace(end, paths)


def testbench(sdf):
    tb = (HERE / "tb.v").read_text()
    if sdf:
        i = tb.rindex("endmodule")
        tb = tb[:i] + '  initial $sdf_annotate("%s", user_project);\n' % sdf.as_posix() + tb[i:]
    return tb


def cell_models(a):
    if a.cells:
        d = Path(a.cells)
        return [d / "sg13cmos5l_io.v", d / "sg13cmos5l_udp.v", d / "sg13cmos5l_stdcell.v"]
    root = a.pdk_root or os.environ.get("PDK_ROOT")
    if not root:
        raise SystemExit("run_gl_sdf: set PDK_ROOT, --pdk-root or --cells")
    lib = Path(root) / "ihp-sg13cmos5l" / "libs.ref"
    return [lib / "sg13cmos5l_io/verilog/sg13cmos5l_io.v",
            lib / "sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v",
            lib / "sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v"]


def fix_dll_path(icarus):
    extra = [icarus] if icarus else []
    if os.name == "nt":       # as run_tests.py: vvp must find python3xx.dll from a venv
        base = Path(sys.base_prefix)
        extra += [str(base), str(base / "Library" / "bin"), str(base / "DLLs")]
    os.environ["PATH"] = os.pathsep.join(extra + [os.environ.get("PATH", "")])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--final", required=True, help="LibreLane's final/ directory")
    ap.add_argument("--corner", default="slow", choices=["none"] + list(CORNERS))
    ap.add_argument("--clk-ns", type=int, default=20)
    ap.add_argument("--random-sram", type=int, default=None, metavar="SEED")
    ap.add_argument("--tests", default=None, help="comma list of test names (default: all)")
    ap.add_argument("--pdk-root", default=None)
    ap.add_argument("--cells", default=None, help="a directory with the three cell model files")
    ap.add_argument("--icarus", default=None, help="directory of iverilog and vvp, put first on PATH")
    a = ap.parse_args()
    fix_dll_path(a.icarus)
    final = Path(a.final)
    for f in cell_models(a):
        if not f.exists():
            raise SystemExit("run_gl_sdf: missing %s" % f)

    tag = a.corner + ("_rand%d" % a.random_sram if a.random_sram is not None else "")
    build = HERE / "sim_build" / ("gl_sdf_" + tag)
    build.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    (build / "tt_um_msf.nl.v").write_text(netlist_fix((final / "nl" / "tt_um_msf.nl.v").read_text()))
    sdf = None
    if a.corner != "none":
        c = CORNERS[a.corner]
        sdf = build / ("tt_um_msf__%s.sdf" % c)
        sdf.write_text(sdf_fix((final / "sdf" / c / ("tt_um_msf__%s.sdf" % c)).read_text()))
    (build / "sram_sdf.v").write_text(sram_model())
    (build / "tb_sdf.v").write_text(testbench(sdf))
    print("[gl_sdf] files prepared in %s (%.0f s)" % (build, time.time() - t0), flush=True)

    defines = {"GL_TEST": 1, "SIM": 1, "FUNCTIONAL": 1}
    if a.random_sram is not None:
        defines["SRAM_RANDOM"] = a.random_sram
    runner = get_runner("icarus")
    runner.build(
        sources=[str(f) for f in cell_models(a)] + [str(build / "sram_sdf.v"),
                 str(build / "tt_um_msf.nl.v"), str(build / "tb_sdf.v")],
        hdl_toplevel="tb", always=True, build_dir=str(build), includes=[str(SRC)],
        defines=defines, timescale=("1ns", "1ps"), waves=False,
        build_args=["-g2012"] + (["-gspecify", "-ginterconnect"] if sdf else []),
        log_file=str(build / "build.log"),
    )
    blog = (build / "build.log").read_text(errors="replace")
    if "will not be driven" in blog:
        raise SystemExit("run_gl_sdf: this Icarus leaves the cells' delayed_* nets undriven "
                         "(see %s); use a newer one (--icarus)" % (build / "build.log"))

    modules = ("gl_sdf_probe," if sdf else "") + MODULES
    os.environ["MSF_CLK_NS"] = str(a.clk_ns)
    sys.path[:0] = [str(HERE)]                 # the runner passes sys.path on as PYTHONPATH
    (build / "results.xml").unlink(missing_ok=True)
    print("[gl_sdf] running %s at %d ns, corner %s; log: %s"
          % (modules, a.clk_ns, a.corner, build / "test.log"), flush=True)
    t1 = time.time()
    results = runner.test(
        test_module=modules, hdl_toplevel="tb", build_dir=str(build), test_dir=str(HERE),
        waves=False, testcase=a.tests.split(",") if a.tests else None,
        results_xml=str(build / "results.xml"), log_file=str(build / "test.log"),
    )
    tlog = (build / "test.log").read_text(errors="replace")
    sdf_errors = [l for l in tlog.splitlines() if l.startswith("SDF ")
                  and not any(b in l for b in BENIGN)]
    states = {}
    for t in ET.parse(results).iter("testcase"):
        st = "passed"
        for tag_ in ("failure", "error", "skipped"):
            if t.find(tag_) is not None:
                st = tag_
        states["%s.%s" % (t.get("classname"), t.get("name"))] = st
        print("[gl_sdf] %-45s %-8s sim %s ns" % (t.get("name"), st, t.get("sim_time_ns")))
    passed = sum(1 for s in states.values() if s == "passed")
    print("[gl_sdf] corner %s, %d ns%s: %d/%d passed (%.0f s)"
          % (a.corner, a.clk_ns, ", random SRAM seed %d" % a.random_sram
             if a.random_sram is not None else "", passed, len(states), time.time() - t1))
    for l in sdf_errors[:20]:
        print("[gl_sdf] SDF: " + l)
    if sdf_errors:
        print("[gl_sdf] %d SDF errors: the annotation is incomplete" % len(sdf_errors))
    return 0 if passed == len(states) and passed > 0 and not sdf_errors else 1


if __name__ == "__main__":
    sys.exit(main())
