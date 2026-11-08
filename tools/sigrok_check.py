#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the firmware's waveforms with sigrok's protocol decoders.

Our peer models were written with the same AI as the firmware, so a shared
misreading of a protocol would pass every cocotb test. sigrok's decoders were
written by other people, for real captures. The firmware tests save their pins
(save_trace in test/fw_tb.py) with the lines the decoder must print; this runs
sigrok-cli on each trace and compares.

Every trace also has a tooth: the same waveform with one edge held back, which
the decoder must read differently. A check that cannot fail shows nothing. An edge
in an idle gap changes nothing a decoder cares about, so it tries the edges from
the middle of the activity outwards, and fails if none of the first eight bites.

Usage:
    python tools/sigrok_check.py                  every test/traces/*.json
    python tools/sigrok_check.py uart i2c --show  print what the decoder says
    SIGROK_CLI="wsl -d Ubuntu -- /path/to/sigrok-cli" python tools/sigrok_check.py
Paths are converted to /mnt/... when SIGROK_CLI starts with wsl.
Exit status 1 on a mismatch, a tooth that did not bite, or no traces.
"""
import argparse
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import msf_capture  # noqa: E402

TRACES = os.path.join(HERE, "..", "test", "traces")
CLI = shlex.split(os.environ.get("SIGROK_CLI", "sigrok-cli"), posix=os.name != "nt")


def native(path):
    path = os.path.abspath(path)
    if CLI[0].lower().startswith("wsl"):
        return "/mnt/" + path[0].lower() + path[2:].replace("\\", "/")
    return path


def decode(path, spec):
    """The decoder's annotation lines for one file, without its prefix: a sigrok
    session, or one byte a sample."""
    fmt = [] if path.endswith(".sr") else ["-I", f"binary:numchannels=8:samplerate={spec['rate']}"]
    cmd = CLI + fmt + ["-i", native(path), "-P", spec["decoders"], "-A", spec["annotations"]]
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600, env=env)      # (sigrok-cli writes UTF-8: degree signs)
    if p.returncode:
        raise RuntimeError(f"sigrok-cli failed: {p.stderr.strip()[:300]}")
    lines = []
    for ln in p.stdout.splitlines():
        m = re.match(r"\S+-\d+:\s*(.*)", ln)
        if m:
            lines.append(m.group(1).replace("\r", "").strip())
    return lines


def edges_from_middle(data, pin, n=8):
    edges = [i for i in range(1, len(data)) if (data[i] ^ data[i - 1]) >> pin & 1]
    if not edges:
        raise ValueError(f"no edge on pin {pin}")
    mid = len(edges) // 2
    return sorted(edges, key=lambda e: abs(edges.index(e) - mid))[:n]


def held_back(data, pin, at, hold):
    """The edge on `pin` at sample `at` held back by `hold` samples: the pin keeps
    its old level for that long."""
    out = bytearray(data)
    old = data[at - 1] >> pin & 1
    for i in range(at, min(at + hold, len(out))):
        out[i] = (out[i] & ~(1 << pin)) | (old << pin)
    return bytes(out)


def check(path, show):
    name = os.path.basename(path)[:-5]
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    binf = path[:-5] + ".bin"
    src = os.path.join(TRACES, spec["sr_file"]) if spec.get("sr_file") else binf
    got = decode(src, spec)
    if show:
        print(f"--- {name} ({spec['decoders']}; {spec['annotations']})")
        print("\n".join(got))
    if got != spec["expect"]:
        print(f"{name}: FAIL, the decoder does not read what the test sent")
        for i, (a, b) in enumerate(zip(got, spec["expect"])):
            if a != b:
                print(f"  line {i}: decoder {a!r}, expected {b!r}")
                break
        print(f"  {len(got)} lines from the decoder, {len(spec['expect'])} expected")
        return False
    if spec.get("sr_file"):
        ev, _rate, _names = msf_capture.read_sr(src)
        end = ev[-1][0] + 2000
        raw = bytearray(end)
        for (t, p), nxt in zip(ev, ev[1:] + [(end, 0)]):
            raw[t:nxt[0]] = bytes([p]) * (nxt[0] - t)
    else:
        with open(binf, "rb") as f:
            raw = f.read()
    pin, hold = spec["tooth"]["pin"], spec["tooth"]["hold"]
    raw = bytes(raw)
    with tempfile.TemporaryDirectory() as tmp:
        badf = os.path.join(tmp, "tooth.bin")
        for at in edges_from_middle(raw, pin):
            with open(badf, "wb") as f:
                f.write(held_back(raw, pin, at, hold))
            if decode(badf, spec) != spec["expect"]:
                print(f"{name}: ok, {len(got)} lines match, the tooth bites "
                      f"({spec['decoders'].split(':')[0]})")
                return True
    print(f"{name}: FAIL, the tooth did not bite: eight changed waveforms read the same")
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("names", nargs="*", help="traces to check (default: all)")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    paths = sorted(glob.glob(os.path.join(TRACES, "*.json")))
    if a.names:
        paths = [p for p in paths if os.path.basename(p)[:-5] in a.names]
    if not paths:
        print("no traces: run the firmware tests first", file=sys.stderr)
        return 1
    results = [check(p, a.show) for p in paths]
    print(f"sigrok: {sum(results)} of {len(results)} traces pass")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
