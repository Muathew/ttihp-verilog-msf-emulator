# SPDX-License-Identifier: Apache-2.0
"""The playground's Python, without a browser: a stranger gets the same verdict the
repository's tools give. pytest test/test_site.py"""
import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path[:0] = [os.path.join(ROOT, "site"), os.path.join(ROOT, "tools")]
import playground as pg  # noqa: E402
import site_build  # noqa: E402


def firmware(name):
    with open(os.path.join(ROOT, "firmware", name), encoding="utf-8") as f:
        return f.read()


def vcd(tmp_path, make):
    path = tmp_path / "x.vcd"
    events, end = site_build._events(make())
    site_build.msf_capture.write_vcd(str(path), events, end, site_build.RATE)
    return path.read_text()


def test_a_shipped_program_is_certified():
    r = pg.check(firmware("uart_tx.s"))
    assert r["verdict"] == "certified" and r["deadlines"]
    assert all(not d["late"] for d in r["deadlines"])


def test_too_fast_is_refused():
    r = pg.check(firmware("uart_tx.s"), "BIT=9")
    assert r["verdict"] == "refused"
    assert any(d["late"] for d in r["deadlines"]), r["report"]


def test_a_wrong_program_says_why():
    r = pg.check("    FROB R1, R2\n")
    assert r["verdict"] == "error" and "FROB" in r["message"]


def test_no_deadlines_is_not_called_certified():
    assert pg.check("loop: JMP loop\n")["verdict"] == "no deadlines"


def test_a_jump_it_cannot_follow_is_not_passed_off():
    r = pg.check(firmware("spi_flash.s"))
    assert r["verdict"] == "not followed" and "cannot follow" in r["report"], r


def test_too_long_does_not_assemble():
    """v4: 2,048 words, the program starting at 128."""
    r = pg.check("    NOP\n" * 1921)
    assert r["verdict"] == "error" and "too long" in r["message"], r
    assert pg.check("    NOP\n" * 1919 + "h: JMP h\n")["verdict"] == "no deadlines"


def test_a_deadline_marked_late_on_purpose_is_certified_only_if_it_is_always_late():
    """`; @late` on a WAIT_CMP (firmware/mcu_v3_peer.s shows the late guard by missing two):
    the checker takes it only where the target has passed on every path."""
    late = "start: CNT_R R5\n       WAIT_CMP R5    ; @late on purpose\nh:     JMP h\n"
    r = pg.check(late)
    assert r["verdict"] == "certified" and r["deadlines"][0]["on_purpose"], r
    assert "LATE ON PURPOSE" in r["report"]
    # the same program without the annotation is refused: the miss is real
    assert pg.check(late.replace("; @late on purpose", ""))["verdict"] == "refused"
    # an annotation on a deadline that is met is an error, not a pass
    met = ("start: CNT_R R5\n       LDR R1, #50\n       ADDS R5, R5, R1\n"
           "       WAIT_CMP R5    ; @late (wrong: this one is met)\nh:     JMP h\n")
    r = pg.check(met)
    assert r["verdict"] == "refused" and "@late: this deadline may be met" in r["report"], r
    # and so is one on an instruction that is no deadline
    r = pg.check("    NOP    ; @late\nh: JMP h\n")
    assert r["verdict"] != "certified" and "@late" in r["report"], r


def test_the_peer_firmware_that_misses_deadlines_on_purpose_says_so():
    r = pg.check(firmware("mcu_v3_peer.s"))
    assert r["verdict"] == "certified", r["report"]
    assert sum(1 for d in r["deadlines"] if d["on_purpose"]) == 2 and r["report"].count("LATE ON PURPOSE") == 2


def test_the_model_shows_the_uart_bits():
    r = pg.run(firmware("uart_tx.s"), 8000)
    assert r["verdict"] == "ran" and r["stopped"] is None
    assert len(r["events"]) > 8


def test_the_model_says_when_it_cannot_go_on():
    r = pg.run(firmware("usb_ls_tx.s"), 8000)
    assert r["verdict"] == "ran" and r["stopped"] and "model has no" in r["stopped"]


def test_nec_is_learned_and_a_new_command_certified(tmp_path):
    r = pg.learn(vcd(tmp_path, site_build.nec_runs), 0, "9966ef10")
    assert r["verdict"] == "learned" and r["model"] == "distance", r
    assert r["worst_slack"] >= 0 and "WAIT_CMP" in r["firmware"]
    assert "0xEF10, 0x9966" in r["firmware"], r["firmware"][-200:]


@pytest.mark.parametrize("make, model", [(site_build.manchester_runs, "manchester"),
                                         (site_build.serial_runs, "serial")])
def test_the_other_examples_are_learned(tmp_path, make, model):
    r = pg.learn(vcd(tmp_path, make))
    assert r["verdict"] == "learned" and r["model"] == model, r


def test_noise_is_refused(tmp_path):
    assert pg.learn(vcd(tmp_path, site_build.noise_runs))["verdict"] == "refused"


def test_a_stored_recording_that_is_not_a_vcd_says_so():
    assert pg.learn("this is not a recording")["verdict"] == "error"


def test_call_is_json():
    r = json.loads(pg.call("check", json.dumps(["loop: JMP loop\n", ""])))
    assert r["verdict"] == "no deadlines"


def test_the_page_states_what_the_docs_state():
    """site/results.json is typed by hand: it has to agree with the sign-off table."""
    with open(os.path.join(ROOT, "site", "results.json"), encoding="utf-8") as f:
        l = json.load(f)["layout"]
    with open(os.path.join(ROOT, "docs", "verification.md"), encoding="utf-8") as f:
        doc = f.read()
    row = [ln for ln in doc.splitlines() if l["run"] in ln and ln.startswith("|")]
    assert row, f"run {l['run']} is not in verification.md"
    r = row[0].replace(" ", "")
    assert f"{l['cells']:,}".replace(",", "") in r
    assert f"{l['utilisation_percent']}%" in r
    assert f"+{l['slack_ns']['slow']:.2f}/+{l['slack_ns']['typical']:.2f}ns" in r


def test_every_shipped_program_gets_a_row():
    rows = site_build.firmware_table()
    names = {r["name"] for r in rows}
    assert "uart_tx.s" in names and "pinfind.s" in names
    bad = [r["name"] for r in rows if r["deadlines"] and not r["certified"]]
    assert not bad, bad
    assert re.match(r"\d+", str(site_build.test_counts()["cocotb_tests"]))
