#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check that the FPGA build uses the same RTL as the chip.

`fpga/rtl` is a copy of `src/` for the AUP-ZU3 build. Every file present in
both must be identical (ignoring line endings), except the ones the board
replaces on purpose: the SRAM wrapper (block RAM instead of the IHP macro)
and the boot ROM. Exits 1 and lists the files that differ.

Usage:  python tools/check_fpga_sync.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = os.path.join(ROOT, "src")
FPGA = os.path.join(ROOT, "fpga", "rtl")
BOARD_SPECIFIC = {"tt_um_msf_sram.v", "tt_um_msf_bootrom.v"}


def text(path):
    with open(path, "rb") as f:
        return f.read().replace(b"\r\n", b"\n")


def main():
    shared = sorted(set(os.listdir(SRC)) & set(os.listdir(FPGA)))
    shared = [f for f in shared if f.endswith(".v")]
    differ = [f for f in shared
              if f not in BOARD_SPECIFIC
              and text(os.path.join(SRC, f)) != text(os.path.join(FPGA, f))]
    missing = sorted(f for f in os.listdir(SRC)
                     if f.startswith("tt_um_msf_") and f.endswith(".v")
                     and f not in os.listdir(FPGA))
    for f in differ:
        print(f"differs: fpga/rtl/{f} (copy src/{f} over it)")
    for f in missing:
        print(f"missing: fpga/rtl/{f}")
    if not differ and not missing:
        print(f"fpga/rtl matches src: {len(shared) - len(BOARD_SPECIFIC & set(shared))} shared files "
              f"identical; board-specific: {', '.join(sorted(BOARD_SPECIFIC))}")
    return 1 if differ or missing else 0


if __name__ == "__main__":
    sys.exit(main())
