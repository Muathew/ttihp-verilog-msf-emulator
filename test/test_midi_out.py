# SPDX-License-Identifier: Apache-2.0
"""MIDI OUT (firmware/midi_out.s) against a receiver written from the MIDI 1.0
Detailed Specification (4.2): 31.25 kbaud, a start bit, 8 data bits LSB first, a stop
bit; status bytes 0x80-0xFF and data bytes 0x00-0x7F; running status for channel
messages; system real-time bytes (0xF8-0xFF) stand alone wherever they appear;
Note On with velocity 0 is a Note Off."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, edges, level, samples

BIT = 384
MS = 12000
DATA_LEN = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}


def uart_bytes(ch):
    """[(start cycle, byte)] from the TX line, each frame checked."""
    e = edges(ch, 0)
    out, t = [], 0
    for c, v in e:
        if v != 0 or c < t:
            continue
        bits = [level(ch, c + BIT // 2 + k * BIT) & 1 for k in range(10)]
        assert bits[0] == 0 and bits[9] == 1, f"frame at {c}: {bits}"
        out.append((c, sum(b << k for k, b in enumerate(bits[1:9]))))
        t = c + 9 * BIT + BIT // 2
    return out


def messages(bs):
    """MIDI messages, running status applied: [(status, data bytes)]."""
    out, run, cur = [], None, None
    for _, b in bs:
        if b >= 0xF8:
            out.append((b, []))
        elif b >= 0x80:
            run, cur = b, [b, []]
        else:
            assert run is not None, "a data byte with no status before it"
            if cur is None:
                cur = [run, []]
            cur[1].append(b)
        if cur and len(cur[1]) == DATA_LEN[cur[0] & 0xF0]:
            out.append((cur[0], cur[1]))
            cur = None
    return out


@cocotb.test()
async def test_midi_phrase(dut):
    """Program change, volume, C on, C off by running status, E on, E off, bend."""
    await fw_boot(dut, "midi_out.s")
    ch = await watch_pins(dut, 25 * 10 * BIT + 10 * MS + 4000)
    bs = uart_bytes(ch)
    msgs = messages(bs)
    assert msgs == [(0xC0, [0]), (0xB0, [7, 100]), (0x90, [60, 100]), (0x90, [60, 0]),
                    (0x90, [64, 100]), (0xFE, []), (0x80, [64, 64]), (0xE0, [0x00, 0x40]),
                    (0xFE, [])], msgs
    # the running status really was used: no status byte before "60, 0"
    raw = [b for _, b in bs]
    assert raw[8:12] == [60, 0, 64, 100], raw
    # 5 ms from the end of a stop bit to the next start bit, twice
    gaps = [(b[0] - a[0] - 10 * BIT) for a, b in zip(bs, bs[1:])]
    assert sorted(gaps)[-2:] == [5 * MS, 5 * MS], gaps
    assert all(g == 0 for g in sorted(gaps)[:-2]), gaps
    t1 = ch[-1][0] + 20 * BIT
    # sigrok counts programs from 1 and calls a Note On of velocity 0 a note off
    lines = ["Channel 1: program change to instrument 1 (assuming Acoustic Grand Piano)",
             "Channel 1: control change 'channel volume MSB (formerly main volume)' (param = 0x64)",
             "Channel 1: note on (note = 60 'C4', velocity = 100)",
             "Channel 1: note off (note = 60 'C4', velocity = 0)",
             "Channel 1: note on (note = 64 'E4', velocity = 100)",
             "System Realtime: active sensing",
             "Channel 1: note off (note = 64 'E4', velocity = 64)",
             "Channel 1: pitch bend change 0x00 0x40 (8192)",
             "System Realtime: active sensing"]
    save_trace("midi_out", samples(ch, 0, t1, 48), "uart:rx=0:baudrate=31250,midi", "midi",
               lines, {"pin": 0, "hold": 12}, rate=250_000)
