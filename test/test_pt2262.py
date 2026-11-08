# SPDX-License-Identifier: Apache-2.0
"""PT2262-style encoder (firmware/pt2262.s) against a decoder written from the PT2262
data sheet: four pulses a code bit, short 4 alpha and long 12 alpha (0 = SLSL, 1 =
LSLS, floating = SLLS, high first), 12 bits, then a sync bit of high 4 alpha and low
124 alpha; the code word repeated. Run with alpha = 2.5 us (the short pulse 120 cycles):
the decoders judge pulses by their ratios, so nothing else changes."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, edges, samples

Q = 120
TRITS = [0, 1, 2, 0, 0, 1, 2, 2, 0, 1, 2, 0]
PATTERN = {(1, 3, 1, 3): 0, (3, 1, 3, 1): 1, (1, 3, 3, 1): 2}


@cocotb.test()
async def test_pt2262_four_words(dut):
    """Four identical code words, every pulse an exact short or long, the sync 4a + 124a."""
    await fw_boot(dut, "pt2262.s", defines={"Q": Q})
    word = 12 * 8 * Q + 32 * Q
    ch = await watch_pins(dut, 4 * word + 2000, pull=0)
    e = edges(ch, 0)
    widths = [b[0] - a[0] for a, b in zip(e, e[1:])] + [None]
    words, k = [], 0
    for _ in range(4):
        trits = []
        for _ in range(12):
            units = tuple(w // Q for w in widths[k:k + 4])
            assert all(w % Q == 0 for w in widths[k:k + 4]), widths[k:k + 4]
            assert units in PATTERN, f"pulses {units}"
            trits.append(PATTERN[units])
            k += 4
        assert widths[k] == Q, "sync high"
        if widths[k + 1] is not None:
            assert widths[k + 1] == 31 * Q, f"sync low {widths[k + 1] / Q} short pulses"
        k += 2
        words.append(trits)
    assert words == [TRITS] * 4, words
    t1 = e[-1][0] + 32 * Q
    names = "01f"
    lines = []
    for _ in range(4):
        lines += [names[t] for t in TRITS] + ["Sync"]
    save_trace("pt2262", samples(ch, 0, t1, 4), "rc_encode:data=0", "rc_encode=bits", lines,
               {"pin": 0, "hold": 60}, rate=3_000_000)
