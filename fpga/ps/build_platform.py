#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Make the Vitis platform the PS apps build against, again, from a fresh XSA.

From a Vitis command prompt at the repository root:

    vitis -s fpga/ps/build_platform.py
        the 12 MHz build: fpga/build/vivado/msf_aup_zu3.xsa
    set MSF_CORE_MHZ=48
    vitis -s fpga/ps/build_platform.py
        the 48 MHz build: fpga/build/vivado_48mhz/msf_aup_zu3.xsa
    set MSF_XSA=C:/some/other.xsa       (any other XSA)

The apps' scripts (mcu_test/build_vitis.py, mcu_v3/build_vitis.py) build against
whatever platform is in fpga/build/vitis/workspace, and the bitstream Vitis programs
is the one inside that platform. On 14 Nov a platform made from an older XSA
programmed an older bitstream, and the board ran a core without the hardware under
test. This deletes the platform and makes it again from the XSA given, so the
bitstream it carries is the one just built. Then delete the app's workspace
(fpga/build/vitis/workspace_mcu_v3) and run its build_vitis.py, so the app is made
against the new platform too.

Written without a Vitis installation to run it on: the calls are those of the
Vitis 2024.1 Python API that the apps' scripts already use, plus
create_platform_component. If it fails, make the platform by hand (docs/board-run-v4.md).
"""

import os
from pathlib import Path
import shutil

import vitis


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "fpga" / "build" / "vitis" / "workspace"
NAME = "msf_aup_zu3_platform"
DOMAIN = "a53_standalone"           # the apps' scripts ask for this domain


def main():
    mhz = int(os.environ.get("MSF_CORE_MHZ", "12"))
    build_dir = "vivado" if mhz == 12 else f"vivado_{mhz}mhz"
    xsa = Path(os.environ.get("MSF_XSA", ROOT / "fpga" / "build" / build_dir / "msf_aup_zu3.xsa"))
    if not xsa.is_file():
        raise FileNotFoundError(f"no XSA at {xsa}: run fpga/vivado/build.tcl first")
    print(f"Making {NAME} from {xsa}")

    client = vitis.create_client()
    try:
        client.set_workspace(path=WORKSPACE.as_posix())
        try:
            client.delete_component(name=NAME)
        except Exception as e:          # not in the workspace yet, or already gone
            print(f"(no old {NAME} to delete: {e})")
        shutil.rmtree(WORKSPACE / NAME, ignore_errors=True)
        platform = client.create_platform_component(
            name=NAME,
            hw_design=xsa.as_posix(),
            os="standalone",
            cpu="psu_cortexa53_0",
            domain_name=DOMAIN,
        )
        platform.build()
    finally:
        vitis.dispose()

    xpfm = WORKSPACE / NAME / "export" / NAME / f"{NAME}.xpfm"
    if not xpfm.is_file():
        raise FileNotFoundError(f"the platform build finished without {xpfm}")
    print(f"Built {xpfm} from {xsa}")


if __name__ == "__main__":
    main()
