# SPDX-License-Identifier: Apache-2.0
"""DCF77 time code (firmware/dcf77.s) against a receiver written from PTB's "DCF77
time code": a marker at the start of every second, 0.1 s for a 0 and 0.2 s for a 1,
none in second 59; bit 0 = 0, Z1/Z2 = 01 for CET, bit 20 = 1; minute, hour, day,
day of the week, month and year in BCD, LSB first, with even parity over the minute,
the hour and the date. Run a thousand times fast (a second is 12,000 cycles); the
trace given to sigrok is declared at the matching rate, so it reads real seconds."""
import cocotb

from fw_tb import fw_boot, save_trace
from pins import watch_pins, edges, samples

SEC = 12000                    # cycles a second here (PIECE = 60)
# Sunday 8 November 2026, 14:37 CET
T = {"minute": 0x37, "hour": 0x14, "day": 0x08, "dow": 7, "month": 0x11, "year": 0x26}


def bcd(bits):
    w = [1, 2, 4, 8, 10, 20, 40, 80]
    return sum(b * w[i] for i, b in enumerate(bits))


@cocotb.test()
async def test_dcf77_minute(dut):
    """Lead-in, minute mark, 14:37 on 8 Nov 2026 (CET), the next minute's second 0."""
    data = {0: T["minute"], 1: T["hour"], 2: T["day"], 3: T["dow"], 4: T["month"],
            5: T["year"], 6: 0}
    await fw_boot(dut, "dcf77.s", defines={"PIECE": 60}, data=data)
    ch = await watch_pins(dut, 63 * SEC + 2000, pull=0)
    e = edges(ch, 0)
    rises = [t for t, v in e if v == 1]
    falls = [t for t, v in e if v == 0]
    assert len(rises) == len(falls) == 61, (len(rises), len(falls))
    gaps = [b - a for a, b in zip(rises, rises[1:])]
    assert gaps[0] == 2 * SEC and gaps[-1] == 2 * SEC, (gaps[0], gaps[-1])
    assert all(g == SEC for g in gaps[1:-1]), gaps
    width = [f - r for r, f in zip(rises, falls)]
    assert all(w in (SEC // 10, SEC // 5) for w in width), width
    b = [1 if w == SEC // 5 else 0 for w in width[1:60]]
    assert width[0] == width[60] == SEC // 10
    assert b[0] == 0 and b[20] == 1 and b[17:19] == [0, 1], (b[0], b[17:21])
    assert b[1:17] == [0] * 16 and b[19] == 0
    got = {"minute": bcd(b[21:28]), "hour": bcd(b[29:35]), "day": bcd(b[36:42]),
           "dow": bcd(b[42:45]), "month": bcd(b[45:50]), "year": bcd(b[50:58])}
    want = {k: int(f"{v:x}") for k, v in T.items()}
    assert got == want, got
    for lo, hi in ((21, 29), (29, 36), (36, 59)):
        assert sum(b[lo:hi]) % 2 == 0, f"parity over bits {lo}-{hi - 1}"
    t1 = falls[-1] + SEC
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    months = ["January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December"]
    lines = ["Start of minute (always 0)", "Special bits: " + "0" * 14, "Call bit: not set",
             "Summer time announcement: not active", "CEST: not in effect", "CET: in effect",
             "Leap second announcement: not active", "Start of encoded time (always 1)",
             f"Minutes: {want['minute']}", "Minute parity: OK", f"Hours: {want['hour']}",
             "Hour parity: OK", f"Day: {want['day']}", f"Day of week: {want['dow']} ({days[want['dow'] - 1]})",
             f"Month: {want['month']} ({months[want['month'] - 1]})", f"Year: {want['year']}",
             "Date parity: OK", "Start of minute (always 0)"]
    save_trace("dcf77", samples(ch, 0, t1, 12), "dcf77:data=0", "dcf77=fields", lines,
               {"pin": 0, "hold": 60}, rate=1000)
