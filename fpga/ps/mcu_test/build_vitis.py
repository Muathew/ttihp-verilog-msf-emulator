#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create or rebuild the MSPM0 protocol-test Vitis application."""

from pathlib import Path
import shutil
import subprocess
import sys

import vitis


ROOT = Path(__file__).resolve().parents[3]
APP_NAME = "mcu_test"
WORKSPACE = ROOT / "fpga" / "build" / "vitis" / "workspace_mcu_test"
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
IMAGE_HEADER = ROOT / "fpga" / "build" / "mcu_test" / "firmware_image.h"


def main():
    if not PLATFORM.is_file():
        raise FileNotFoundError(f"Vitis platform not found: {PLATFORM}")

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "fpga" / "ps" / "loader" / "generate_image.py"),
            str(ROOT / "firmware" / "mcu_protocol_target.s"),
            "--output",
            str(IMAGE_HEADER),
        ],
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
                from_loc=(ROOT / "fpga" / "ps" / "mcu_test" / "src").as_posix(),
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
            ROOT / "fpga" / "ps" / "mcu_test" / "src" / "main.c",
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
