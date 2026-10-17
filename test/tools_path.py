# SPDX-License-Identifier: Apache-2.0
"""Make ../tools importable from the tests (host-side boot frame builder)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

from msf_boot import boot_frame, frame, crc8, ROM_PROTOCOL, CMD_WRITE, CMD_GO, ACK, NAK  # noqa: E402,F401
