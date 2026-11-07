# SPDX-License-Identifier: Apache-2.0
"""What the page runs in the browser, through Pyodide: the repository's own assembler,
deadline checker, ISA model and learner, unchanged. `call` takes and returns JSON text,
so the page and test/test_site.py use it the same way."""
import contextlib
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path[:0] = [os.path.join(ROOT, "tools"), os.path.join(ROOT, "test")]

import assembler as asm  # noqa: E402
import msf_capture  # noqa: E402
import msf_gen  # noqa: E402
import msf_learn  # noqa: E402
import msf_timing  # noqa: E402
from isa_model import IsaModel  # noqa: E402

WORK = tempfile.mkdtemp()
FIRMWARE_WORDS = 896
MAX_RUN = 400_000
MAX_EVENTS = 20_000
EXAMPLE_DATA = [ord(c) for c in "Hi!"] + [0]     # what the examples that send a string read


def _source(text):
    path = os.path.join(WORK, "program.s")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def _defines(text):
    """'BIT=9, N=2' -> {'BIT': 9, 'N': 2}"""
    out = {}
    for item in (text or "").replace(";", ",").split(","):
        if item.strip():
            k, _, v = item.partition("=")
            out[k.strip()] = int(v.strip(), 0)
    return out or None


def check(text, defines=""):
    """Assemble and certify. verdict: error; refused (a deadline not proved); certified;
    no deadlines; or not followed (none seen, on the paths the checker could follow)."""
    try:
        an, words, _ = msf_timing.analyse(_source(text), None, _defines(defines))
    except Exception as e:     # a pasted program can be wrong in any way: say how
        return {"verdict": "error", "message": f"{type(e).__name__}: {e}"}
    report = io.StringIO()
    with contextlib.redirect_stdout(report):
        msf_timing.report("program.s", an)
    rows = [{"pc": pc, "source": an.src.get(pc, ""), "lo": d["lo"], "hi": d["hi"],
             "late": bool(d["late"]), "unchecked": bool(d["unchecked"])}
            for pc, d in sorted(an.deadlines.items())]
    if msf_timing.certified(an):
        verdict = "certified" if rows else "no deadlines"
    else:
        verdict = "refused" if rows else "not followed"
    return {"verdict": verdict, "words": len(words), "capacity": FIRMWARE_WORDS,
            "threads": an.n, "deadlines": rows,
            "report": report.getvalue().partition("\n")[2]}   # less the line with the file's name


def run(text, cycles=20_000, defines=""):
    """Run on the ISA model. Every change of the pins, as [cycle, uo, out, oe]. The model has
    no SERDES and no capture engine: a program that uses them stops there, and says so."""
    try:
        words = asm.assemble_file(_source(text), base_addr=128, defines=_defines(defines))
    except Exception as e:
        return {"verdict": "error", "message": f"{type(e).__name__}: {e}"}
    cycles = min(int(cycles), MAX_RUN)
    mem = {128 + i: w for i, w in enumerate(words)}
    mem.update(enumerate(EXAMPLE_DATA))
    m = IsaModel(asm.bootloader_words(), mem, uio_pad=lambda c: 0xFF, ui_pad=lambda c: 1)
    events, last, stopped = [], None, None
    try:
        for _ in range(cycles):
            m.step()
            m.trace.clear()
            now = (m.uo, m.out, m.oe)
            if now != last:
                events.append([m.cycle, *now])
                last = now
                if len(events) >= MAX_EVENTS:
                    stopped = f"stopped after {MAX_EVENTS} changes of the pins"
                    break
    except NotImplementedError as e:
        stopped = f"the model has no {e}: run the RTL simulation for this one"
    return {"verdict": "ran", "cycles": m.cycle, "events": events, "stopped": stopped}


def learn(recording, pin=0, send="", clock_hz=12_000_000):
    """A VCD's text -> what it says, and firmware that sends it or the given words."""
    path = os.path.join(WORK, "recording.vcd")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(recording)
    try:
        events, rate, _ = msf_capture.read_trace(path)
        m = msf_learn.learn_runs(msf_learn.runs_of(events, int(pin)), rate)
    except msf_learn.Refused as e:
        return {"verdict": "refused", "message": str(e)}
    except Exception as e:
        return {"verdict": "error", "message": f"{type(e).__name__}: {e}"}
    out = {"verdict": "learned", "description": msf_learn.describe(m), "model": m["model"]}
    try:
        words = [int(w, 16) for w in send.replace(" ", ",").split(",") if w] or None
        table = msf_learn.to_table(m, words, 0, int(clock_hz))
        text = msf_gen.generate(table)
        _, code, worst = msf_gen.certify(text)
    except (msf_gen.Refused, msf_learn.Refused, ValueError) as e:
        out["firmware_refused"] = str(e)
        return out
    out.update(firmware=text, words=len(code), worst_slack=worst)
    return out


def call(name, args_json="[]"):
    fn = {"check": check, "run": run, "learn": learn}[name]
    return json.dumps(fn(*json.loads(args_json)))
