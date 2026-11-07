#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A public draw of protocols the chip was not built for.

The brief asks for "new protocols after fabrication". Showing that with protocols we
chose would show little, so a public random beacon chooses (the method is MarcosAsh's).
This file is the rule, and it and docs/draw/pool.tsv were committed before the round
named below was drawn. The pool is sigrok's decoders that read logic levels, less what
we already ship and what could not be attempted, each with its reason, so that a
decoder written by other people can judge the result.

Each eligible id ranks by sha256(randomness || id), and the first COUNT are drawn.
drand's quicknet chain gives a round every 3 s, so anyone can check it.

    python tools/protocol_draw.py pool          the pool, and why each other is out
    python tools/protocol_draw.py draw          fetch the round and print the draw
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DRAW = os.path.join(HERE, "..", "docs", "draw")
POOL = os.path.join(DRAW, "pool.tsv")
RECORD = os.path.join(DRAW, "draw.json")
CHAIN = "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"
RELAYS = ["https://api.drand.sh", "https://api2.drand.sh", "https://drand.cloudflare.com"]
COUNT = 6
ROUND = 33759424      # set before the commit: in the future when this was committed


def pool():
    with open(POOL, encoding="utf-8") as f:
        rows = [ln.rstrip("\n").split("\t") for ln in f if ln.strip() and not ln.startswith("#")]
    return rows


def beacon(round_):
    answers = {}
    for relay in RELAYS:
        try:
            req = urllib.request.Request(f"{relay}/{CHAIN}/public/{round_}",
                                         headers={"User-Agent": "msfcore-protocol-draw"})
            with urllib.request.urlopen(req, timeout=20) as r:
                answers[relay] = json.load(r)
        except Exception as e:
            answers[relay] = {"error": str(e)[:120]}
    rands = {a["randomness"] for a in answers.values() if "randomness" in a}
    if len(rands) != 1:
        raise SystemExit(f"the relays do not agree on a value: {answers}")
    return rands.pop(), answers


def draw(randomness, ids):
    return sorted(ids, key=lambda i: hashlib.sha256((randomness + i).encode()).hexdigest())[:COUNT]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("pool", "draw"))
    a = ap.parse_args()
    rows = pool()
    eligible = [r[0] for r in rows if r[1] == "in"]
    if a.cmd == "pool":
        for r in rows:
            print("\t".join(r))
        print(f"{len(eligible)} eligible, {len(rows) - len(eligible)} out", file=sys.stderr)
        return 0
    randomness, answers = beacon(ROUND)
    picks = draw(randomness, eligible)
    with open(RECORD, "w", encoding="utf-8") as f:
        json.dump({"round": ROUND, "randomness": randomness, "drawn": picks,
                   "answers": answers}, f, indent=1)
    print(f"round {ROUND}: {randomness}")
    print("drawn:", ", ".join(picks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
