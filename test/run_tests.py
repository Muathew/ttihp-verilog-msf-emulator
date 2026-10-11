# SPDX-License-Identifier: Apache-2.0
"""msfcore test runner (cocotb 2.x, Icarus).

Canonical local runner that bypasses `make`/`sh` entirely (Linux-style
`cd test && make -B` also works; this script exists because
Git-for-Windows `sh` fork limits break `make` on some dev machines).

Usage (from the test/ directory, project venv active):
    python run_tests.py                # full suite
    python run_tests.py -t wait_edge   # single test (name without test_ prefix)

Builds into `sim_build/rtl/` (gitignored), writes results.xml into it.
Exit code 0 = all pass.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from cocotb_tools.runner import get_runner

HERE = Path(__file__).parent
SRC = (HERE.parent / "src").resolve()

PROJECT_SOURCES = [
    "project.v", "tt_um_msf_ctrl.v", "tt_um_msf_regfile.v", "tt_um_msf_alu.v",
    "tt_um_msf_sram.v", "tt_um_msf_bootrom.v", "tt_um_msf_pio.v",
]

BUILD_DIR = HERE / "sim_build" / "rtl"


def main() -> int:
    testcase = None
    module = "test_core"
    if "-m" in sys.argv:
        module = sys.argv[sys.argv.index("-m") + 1]
    elif "--all" in sys.argv:
        module = "test_core,test_protocols,test_extensions,test_reprogrammable,test_crv_formal"

    if "-t" in sys.argv:
        name = sys.argv[sys.argv.index("-t") + 1]
        if not name.startswith("test_"):
            name = f"test_{name}"
        # cocotb 2.x filter uses the fully-qualified module.function name
        testcase = [f"{module}.{name}"]

    runner = get_runner("icarus")
    runner.build(
        sources=[str(SRC / s) for s in PROJECT_SOURCES] + [str(HERE / "tb.v")],
        hdl_toplevel="tb",
        always=True,
        build_dir=str(BUILD_DIR),
        includes=[str(SRC)],
        build_args=["-g2012"],
        waves=False,
    )
    print(f"[run_tests] build OK ({BUILD_DIR})")

    # cocotb reuses build_dir; results.xml from a previous (full) run
    # would be stale when running a single test -- remove it first.
    (BUILD_DIR / "results.xml").unlink(missing_ok=True)

    runner.test(
        test_module=module,
        hdl_toplevel="tb",
        build_dir=str(BUILD_DIR),
        waves=False,
        testcase=testcase,  # None = run the whole module
    )

    # cocotb 2.x writes results.xml into build_dir. A passing <testcase>
    # carries no status child; failures have <failure>/<error>, and the
    # rest of the module appears as <skipped> when -t filters a subset.
    # When a subset was requested, only the non-skipped cases count.
    root = ET.parse(BUILD_DIR / "results.xml")

    def status(t):
        for tag in ("failure", "error", "skipped"):
            if t.find(tag) is not None:
                return tag
        return "passed"

    states = {t.get("name"): status(t) for t in root.iter("testcase")}
    if testcase is not None:
        states = {n: s for n, s in states.items() if s != "skipped"}
    total = len(states)
    passed = sum(1 for s in states.values() if s == "passed")
    failed = {n: s for n, s in states.items() if s != "passed"}
    print(f"[run_tests] {passed}/{total} tests passed")
    for name, s in failed.items():
        print(f"[run_tests]   {s.upper()}: {name}")
    return 0 if (not failed and passed > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
