# SPDX-License-Identifier: Apache-2.0
"""Morse keyer (firmware/morse.s) against a listener written from ITU-R M.1677-1:
a dot one unit, a dash three, one unit between the elements of a letter, three
between letters, seven between words; the letters and figures from part I §1.1.
The test runs it at 2400 words a minute (a unit of 6,000 cycles, one piece) so the
whole message fits a short simulation; the program's 20 wpm is the same loop with
twelve pieces of 60,000 cycles a unit, and is certified as well."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, edges, samples

UNIT = 6000
TEXT = "CQ DE MSF 60"
ITU = {"A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.",
       "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.",
       "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-", "U": "..-",
       "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..", "0": "-----",
       "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....", "6": "-....",
       "7": "--...", "8": "---..", "9": "----."}


@cocotb.test()
async def test_morse_message(dut):
    """CQ DE MSF 60, every element and space a whole number of units, exactly."""
    await fw_boot(dut, "morse.s", defines={"CHUNK": UNIT, "UNITC": 1})
    units = 1 + sum(sum(1 if x == "." else 3 for x in ITU[c]) + len(ITU[c]) - 1 + 3
                    for c in TEXT if c != " ") + 4 * TEXT.count(" ")
    ch = await watch_pins(dut, (units + 10) * UNIT, pull=0)
    e = edges(ch, 0)
    assert e and e[0][1] == 1 and len(e) % 2 == 0, e[:3]
    runs = [(b[0] - a[0], a[1]) for a, b in zip(e, e[1:])]
    for n, lvl in runs:
        assert n % UNIT == 0, f"a {'mark' if lvl else 'space'} of {n} cycles, not whole units"
    text, letter = "", ""
    for n, lvl in runs + [(7 * UNIT, 0)]:
        u = n // UNIT
        if lvl:
            assert u in (1, 3), f"a mark of {u} units"
            letter += "." if u == 1 else "-"
        else:
            assert u in (1, 3, 7), f"a space of {u} units"
            if u >= 3:
                text += next(k for k, v in ITU.items() if v == letter)
                letter = ""
            if u == 7:
                text += " "
    assert text.strip() == TEXT, text
    t1 = e[-1][0] + 8 * UNIT
    save_trace("morse", samples(ch, 0, t1, 60), "morse:data=0:timeunit=0.0005", "morse=word",
               TEXT.lower().split(), {"pin": 0, "hold": 200}, rate=12_000_000 // 60)
