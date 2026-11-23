#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create or rebuild the v3 board-test Vitis application (fpga/ps/mcu_v3).

The peer is assembled for the bitstream's core clock: 12 MHz unless the environment
says MSF_CORE_MHZ=48 (`set MSF_CORE_MHZ=48` in the Vitis command prompt), for a
bitstream built with `build.tcl -tclargs --mhz 48`. The app prints the clock it was
built for when it loads the peer."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import vitis


ROOT = Path(__file__).resolve().parents[3]
APP_NAME = "mcu_v3"
WORKSPACE = ROOT / "fpga" / "build" / "vitis" / "workspace_mcu_v3"
PLATFORM = (
    ROOT
    / "fpga"
    / "build"
    / "vitis"
    / "workspace"
    / "msf_aup_zu3_platform"
    / "export"
    / "msf_aup_zu3_platform"
    / "msf_aup_zu3_platform.xpfm"
)
APP_DIR = WORKSPACE / APP_NAME
APP_SRC = APP_DIR / "src"
IMAGE_HEADER = ROOT / "fpga" / "build" / "mcu_v3" / "firmware_image.h"
CLK_SHIFT = {12: 0, 48: 2}      # firmware/mcu_v3_peer.s: the core at 12 MHz << CLK_SHIFT


def main():
    if not PLATFORM.is_file():
        raise FileNotFoundError(f"Vitis platform not found: {PLATFORM}")
    mhz = int(os.environ.get("MSF_CORE_MHZ", "12"))
    if mhz not in CLK_SHIFT:
        raise ValueError(f"MSF_CORE_MHZ={mhz}: the peer is built for 12 or 48 MHz")
    print(f"Building the v3 board peer for a {mhz} MHz core clock")

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "fpga" / "ps" / "loader" / "generate_image.py"),
            str(ROOT / "firmware" / "mcu_v3_peer.s"),
            "--output",
            str(IMAGE_HEADER),
        ]
        + (["-D", f"CLK_SHIFT={CLK_SHIFT[mhz]}"] if CLK_SHIFT[mhz] else []),
        check=True,
    )

    client = vitis.create_client()
    try:
        client.set_workspace(path=WORKSPACE.as_posix())
        if APP_DIR.exists():
            component = client.get_component(name=APP_NAME)
        else:
            component = client.create_app_component(
                name=APP_NAME,
                platform=PLATFORM.as_posix(),
                domain="a53_standalone",
                template="empty_application",
            )
            component.import_files(
                from_loc=(ROOT / "fpga" / "ps" / "mcu_v3" / "src").as_posix(),
                files=["main.c"],
                dest_dir_in_cmp="src",
            )
            component.import_files(
                from_loc=(ROOT / "fpga" / "ps" / "common").as_posix(),
                files=[
                    "msf_link.c",
                    "msf_link.h",
                    "msf_emio.h",
                    "msf_emio_xgpiops.c",
                    "msf_log.h",
                ],
                dest_dir_in_cmp="src",
            )

        APP_SRC.mkdir(parents=True, exist_ok=True)
        for source in (
            ROOT / "fpga" / "ps" / "mcu_v3" / "src" / "main.c",
            ROOT / "fpga" / "ps" / "common" / "msf_link.c",
            ROOT / "fpga" / "ps" / "common" / "msf_link.h",
            ROOT / "fpga" / "ps" / "common" / "msf_emio.h",
            ROOT / "fpga" / "ps" / "common" / "msf_emio_xgpiops.c",
            ROOT / "fpga" / "ps" / "common" / "msf_log.h",
            IMAGE_HEADER,
        ):
            shutil.copy2(source, APP_SRC / source.name)

        component.build(target="hw")
    finally:
        vitis.dispose()

    elf = APP_DIR / "build" / f"{APP_NAME}.elf"
    if not elf.is_file():
        raise FileNotFoundError(f"Vitis build completed without producing {elf}")
    print(f"Built deployment ELF: {elf}")


if __name__ == "__main__":
    main()
