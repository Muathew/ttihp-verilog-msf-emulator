# SPDX-License-Identifier: Apache-2.0
"""msfcore test runner (cocotb 2.x, Icarus).

Canonical local runner that bypasses `make`/`sh` entirely (`cd test && make`
also works on Linux/macOS). The RTL source list is read from ../info.yaml so
there is a single source of truth.

Usage (from the test/ directory, project venv active):
    python run_tests.py                    # test_core only (fast smoke)
    python run_tests.py --all              # every RTL test module
    python run_tests.py -m test_protocols  # one module (comma list allowed)
    python run_tests.py -m test_core -t wait_edge   # one test
    python run_tests.py --all --seed 1234  # reproducible random seed

Builds into `sim_build/rtl/` (gitignored). Exit code 0 = all pass.
"""
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from cocotb_tools.runner import get_runner

HERE = Path(__file__).parent.resolve()
REPO = HERE.parent
SRC = REPO / "src"
BUILD_DIR = HERE / "sim_build" / os.environ.get("MSF_BUILD", "rtl")   # another name: a second run at once

# Every RTL test module. GL-only modules (test_gl_smoke, test_gl_firmware)
# are run by the Makefile with GATES=yes.
ALL_MODULES = [
    "test_core",
    "test_regressions",
    "test_isa_v2",
    "test_peripherals",
    "test_threads",
    "test_boot",
    "test_random_diff",
    "test_protocols",
    "test_extensions",
    "test_firmware",
    "test_reprogrammable",
    "test_crv_invariants",
    "test_golden_model",
    "test_targets",
    "test_monitors",
    "test_bridge",
    "test_faults",
    "test_debug",
    "test_capture",
    "test_capture_tools",
    "test_flight",
    "test_margin",
    "test_pinfind",
    "test_learn",
    "test_interpose",
    "test_debugports",
    "test_buses",
    "test_draw_sda2506",
    "test_draw_spdif",
    "test_draw_em4100",
    "test_draw_microwire",
    "test_draw_cec",
    "test_draw_iec",
    "test_wiegand",
    "test_ir_nec",
    "test_ir_rc5",
    "test_ir_rc6",
    "test_morse",
    "test_dmx512",
    "test_midi_out",
    "test_lin_master",
    "test_servo",
    "test_quadrature",
    "test_dcf77",
    "test_max7219",
    "test_tlc5620",
    "test_sent",
    "test_dshot",
    "test_sbus",
    "test_hd44780",
    "test_dali",
    "test_mdio",
    "test_am2302",
    "test_stepper",
    "test_pt2262",
    "test_i2s_tx",
    "test_seg7",
    "test_adf4351",
    "test_ws2801",
    "test_lm75",
    "test_ds1307",
    "test_eeprom24",
    "test_nec_rx",
    "test_nes_pad",
    "test_st7735",
    "test_ssi_enc",
    "test_nrf24",
    "test_ds18b20",
    "test_sdspi",
]


def project_sources():
    """RTL sources = the uncommented `source_files` entries of info.yaml."""
    text = (REPO / "info.yaml").read_text()
    block = text.split("source_files:", 1)[1].split("\n\n", 1)[0]
    files = re.findall(r'^\s*-\s*"([^"]+)"', block, flags=re.M)
    if not files:
        raise SystemExit("run_tests: no source_files found in info.yaml")
    return files


def fix_dll_path():
    """A venv built from a conda base can't find python3xx.dll from inside
    vvp unless the base interpreter directory is on PATH (Windows only)."""
    if os.name == "nt":
        base = Path(sys.base_prefix)
        extra = [str(base), str(base / "Library" / "bin"), str(base / "DLLs")]
        os.environ["PATH"] = os.pathsep.join(extra + [os.environ.get("PATH", "")])


def arg(flag):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None


def main() -> int:
    fix_dll_path()
    if "--all" in sys.argv:
        modules = [m for m in ALL_MODULES if (HERE / f"{m}.py").exists()]
    else:
        modules = (arg("-m") or "test_core").split(",")
    testcase = None
    if arg("-t"):
        name = arg("-t")
        if not name.startswith("test_"):
            name = f"test_{name}"
        testcase = [f"{m}.{name}" for m in modules]
    seed = arg("--seed")

    runner = get_runner("icarus")
    runner.build(
        sources=[str(SRC / s) for s in project_sources()] + [str(HERE / "tb.v")],
        hdl_toplevel="tb",
        always=True,
        build_dir=str(BUILD_DIR),
        includes=[str(SRC)],
        build_args=["-g2012"],
        waves=False,
    )
    print(f"[run_tests] build OK ({BUILD_DIR})")

    # results.xml from a previous run would be stale -- remove it first.
    (BUILD_DIR / "results.xml").unlink(missing_ok=True)
    results = runner.test(
        test_module=",".join(modules),
        hdl_toplevel="tb",
        build_dir=str(BUILD_DIR),
        test_dir=str(HERE),
        waves=False,
        testcase=testcase,
        seed=int(seed) if seed else None,
        results_xml=str(BUILD_DIR / "results.xml"),
    )

    # A passing <testcase> has no status child; failures carry
    # <failure>/<error>; filtered-out tests appear as <skipped>.
    root = ET.parse(results)

    def status(t):
        for tag in ("failure", "error", "skipped"):
            if t.find(tag) is not None:
                return tag
        return "passed"

    states = {f'{t.get("classname")}.{t.get("name")}': status(t)
              for t in root.iter("testcase")}
    if testcase is not None:
        states = {n: s for n, s in states.items() if s != "skipped"}
    passed = sum(1 for s in states.values() if s == "passed")
    failed = {n: s for n, s in states.items() if s != "passed"}
    print(f"[run_tests] {passed}/{len(states)} tests passed ({', '.join(modules)})")
    for name, s in failed.items():
        print(f"[run_tests]   {s.upper()}: {name}")
    return 0 if (not failed and passed > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
