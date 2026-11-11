# SPDX-License-Identifier: Apache-2.0
"""v4 step 3: timed pins (docs/isa.md section 6.8). The expected pad cycles are worked
out from the spec by hand, not taken from the RTL or the model; the last tests also run RTL
against the ISA model on a program of their own, since the random programs seldom put two
events in one cycle."""
import cocotb
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge, Timer

from assembler import assemble, AsmError
from diffcheck import run_and_compare
from msf_tb import boot_asm, reg, ctrl, start_clock

# edge cycle (the first cycle with the new pad value) for an event whose time is T: the
# write lands at the end of the cycle with CNT == T, so the pad shows it from T + 1
LAND = 1


async def trace(dut, cycles, sigs=("uio_out",)):
    """{cnt: (sig values)} for every cycle: the pads as they are during that cycle."""
    c, out = ctrl(dut), {}
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if c.cnt.value.is_resolvable:
            out[int(c.cnt.value)] = tuple(int(getattr(dut, s).value) for s in sigs)
    return out


def first_change(tr, bit, to, after=0):
    """The first cycle (counter value) after `after` where uio_out[bit] is `to`."""
    for k in sorted(tr):
        if k > after and ((tr[k][0] >> bit) & 1) == to:
            return k
    return None


OTHERS = """
    t1: INCR R4
        JMP  t1
    t2: LDI  R5, 700
        LD   R6, R5
        INCR R4
        JMP  t2
    t3: WAIT_EDGE 6                 ; stalls for good: uio6 never moves
    t3h: JMP t3h
"""


def start(nthreads):
    """T0 code that starts the other threads (up to three) at t1..t3."""
    lines = []
    for k in range(1, nthreads):
        lines.append(f"LDI R6, t{k}")
        lines.append(f"TSTART #{k}, R6" if k > 1 else "TSTART R6")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_event_lands_on_its_cycle_with_one_to_four_threads(dut):
    """AT time T; a pin write; the pad changes in cycle T + 1, whatever the number of
    threads, whatever they do (one stalled, one loading, one counting) and whenever the
    write itself arrives (here many cycles before T)."""
    for nthreads in (1, 2, 3, 4):
        await boot_asm(dut, f"""
            {start(nthreads)}
            ESEL  #2
            CNT_R R1
            LDI   R2, 150
            ADDS  R1, R1, R2        ; T = now + 150
            AT    R1
            PIN_SET 0x01            ; held until CNT == T
            PIN_SET 0x02            ; the next write is immediate
            AT_STAT R7
        h:  JMP   h
        {OTHERS}
        """)
        tr = await trace(dut, 260)
        T = reg(dut, 1)
        got = first_change(tr, 0, 1)
        assert got == T + LAND, f"{nthreads} threads: pin 0 rose in cycle {got}, T = {T}"
        imm = first_change(tr, 1, 1)
        assert imm is not None and imm < T, f"the untimed write was held (cycle {imm})"
        assert reg(dut, 7) == 0b010, f"AT_STAT {reg(dut, 7):03b}: held, not armed"


@cocotb.test()
async def test_reply_idiom_has_no_jitter(dut):
    """The 1-Wire/I2C pattern: WAIT_EDGE; TS; ADDS; AT; write. The reply comes exactly
    delta cycles after the event, for any phase of the input edge against the thread's
    slots and with 1 to 4 threads. The synchroniser makes a pad change in cycle E visible in
    E + 2, so TS = E + 2 and the pad changes in E + 2 + delta + 1."""
    DELTA = 40
    for nthreads in (1, 2, 3, 4):
        for e in (300, 301, 302, 303):
            await boot_asm(dut, f"""
                {start(nthreads)}
                ESEL  #2
                OE_SET 0x80
                WAIT_EDGE 0, rise
                TS    R1
                LDR   R2, #{DELTA}
                ADDS  R3, R1, R2
                AT    R3
                PIN_SET 0x80
            h:  JMP   h
            {OTHERS}
            """)
            c = ctrl(dut)

            async def drive():
                while True:
                    await RisingEdge(dut.clk)
                    await ReadOnly()
                    t = int(c.cnt.value) if c.cnt.value.is_resolvable else 0
                    await Timer(1, unit="ns")
                    dut.uio_in.value = 1 if t >= e else 0

            d = cocotb.start_soon(drive())
            tr = await trace(dut, e + 120)
            d.cancel()
            await Timer(1, unit="ns")
            dut.uio_in.value = 0
            assert reg(dut, 1) == e + 2, f"TS {reg(dut, 1)} != {e + 2} ({nthreads} threads)"
            got = first_change(tr, 7, 1)
            assert got == e + 2 + DELTA + LAND, \
                f"{nthreads} threads, edge in {e}: reply in cycle {got}, expected {e + 2 + DELTA + LAND}"


@cocotb.test()
async def test_a_time_that_has_passed_is_refused(dut):
    """The time is 5 cycles behind when the write arrives: no pin changes, Z = 1, the
    thread's LATE flag is set (STAT bit 14), the slot is empty. A time exactly now is
    applied at once, a time ahead is held (Z = 0)."""
    await boot_asm(dut, """
        ESEL  #2
        CNT_R R1
        LDR   R2, #5
        SUBS  R1, R1, R2        ; 6 cycles ago when the write arrives
        AT    R1
        PIN_SET 0x01            ; refused
        JZ    ref
        LDR   R5, #0xEE         ; (not reached)
    ref:
        AT_STAT R4              ; 0: nothing armed, nothing held
        ESEL  #0
        STAT  R3                ; bit 14: LATE
        ESEL  #2
        CNT_R R1
        LDR   R2, #4
        ADDS  R1, R1, R2        ; arrives exactly at T: 3 instructions after CNT_R + the AT
        AT    R1
        PIN_SET 0x02            ; applied now or held, never refused
        JZ    bad
        LDR   R6, #0x66
    bad:
    h:  JMP   h
    """)
    await ClockCycles(dut.clk, 80)
    assert int(dut.uio_out.value) & 1 == 0, "the refused write changed the pin"
    assert reg(dut, 4) == 0 and reg(dut, 5) == 0
    assert reg(dut, 3) & 0x4000, f"STAT {reg(dut, 3):#06x}: LATE not set"
    assert int(dut.uio_out.value) & 2, "the write that was on time never landed"
    assert reg(dut, 6) == 0x66, "the write that was on time reported Z = 1"


@cocotb.test()
async def test_events_in_one_cycle_lower_thread_wins(dut):
    """Four threads arm an event for the same cycle T: T0 clears bit 0, T1 sets it, T2 and T3
    both toggle bit 1. All are applied, T3's first and T0's last: bit 0 ends 0 (the lowest
    thread wins), bit 1 unchanged (toggles stack). RTL and model agree on the program."""
    src = """
        ESEL  #2
        CNT_R R1
        LDI   R2, 300
        ADDS  R1, R1, R2        ; the common time T
        LDI   R3, 900
        ST    R3, R1            ; in a mailbox, before the others start
        PIN_WI 0x01             ; bit 0 high, bit 1 low (immediate: no AT yet)
        LDI   R6, t1
        TSTART R6
        LDI   R6, t2
        TSTART #2, R6
        LDI   R6, t3
        TSTART #3, R6
        AT    R1
        PIN_CLR 0x01            ; T0: held
    h0: JMP   h0
    t1: ESEL  #2
        LDI   R3, 900
        LD    R1, R3
        AT    R1
        PIN_SET 0x01            ; T1
    h1: JMP   h1
    t2: ESEL  #2
        LDI   R3, 900
        LD    R1, R3
        AT    R1
        PIN_TGL 0x02            ; T2
    h2: JMP   h2
    t3: ESEL  #2
        LDI   R3, 900
        LD    R1, R3
        AT    R1
        PIN_TGL 0x02            ; T3
    h3: JMP   h3
    """
    start_clock(dut)
    await run_and_compare(dut, src, 700, tag="four events in one cycle")
    await boot_asm(dut, src)
    tr = await trace(dut, 400)
    T = reg(dut, 1)
    assert 0 < T < 390
    assert (tr[T][0] & 3) == 0b01, f"cycle T: {tr[T][0] & 3:02b} (nothing applied before T)"
    assert (tr[T + LAND][0] & 3) == 0b00, \
        f"cycle T+1: {tr[T + LAND][0] & 3:02b}: expected 00 (T0's clear is last, the toggles stack)"
    assert tr[max(tr)][0] & 3 == 0b00


@cocotb.test()
async def test_a_second_timed_write_finds_the_slot_occupied(dut):
    """AT t1; write A (held); AT t2; write B: the slot holds one event, so B is refused (Z = 1,
    LATE) and A still lands on time."""
    await boot_asm(dut, """
        ESEL  #2
        CNT_R R1
        LDI   R2, 100
        ADDS  R1, R1, R2        ; t1
        AT    R1
        PIN_SET 0x01            ; A: held
        LDR   R2, #50
        ADDS  R3, R1, R2        ; t2 (later: any time)
        AT    R3
        AT_STAT R5              ; armed + refuse-next
        PIN_SET 0x02            ; B: refused
        JZ    ok
        LDR   R7, #0xEE
    ok: AT_STAT R6
        ESEL  #0
        STAT  R4
    h:  JMP   h
    """)
    tr = await trace(dut, 200)
    assert reg(dut, 5) == 0b111, f"AT_STAT before B: {reg(dut, 5):03b} (armed, held, refuse-next)"
    assert reg(dut, 6) == 0b010, f"AT_STAT after B: {reg(dut, 6):03b} (only A held)"
    assert reg(dut, 7) == 0 and reg(dut, 4) & 0x4000, "B was not refused with LATE"
    assert first_change(tr, 1, 1) is None, "B reached the pin"
    t1 = reg(dut, 1)
    assert first_change(tr, 0, 1) == t1 + LAND, f"A did not land on its cycle: {first_change(tr, 0, 1)} vs {t1 + 1}"


@cocotb.test()
async def test_tstop_cancels_a_held_event_and_tstart_clears_the_page(dut):
    """T1 holds an event far ahead; T0 stops it: the pin never changes. T0 then restarts T1: it
    is back on port page 0, where PW #0 is FIFO_PUT, not AT (the FIFO count is 1)."""
    await boot_asm(dut, """
        LDI    R6, t1
        TSTART R6
        LDR    R0, #40
    d:  DECR   R0
        JNZ    d
        TSTOP                   ; T1's held event is cancelled
        LDI    R6, t1b
        TSTART R6               ; restarted: page 0 again
        LDR    R0, #60
    e:  DECR   R0
        JNZ    e
        FIFO_STAT R3
    h0: JMP    h0
    t1: ESEL   #2
        CNT_R  R1
        LDI    R2, 400
        ADDS   R1, R1, R2
        AT     R1
        PIN_SET 0x04            ; held for 400 cycles
    h1: JMP    h1
    t1b: LDR   R1, #0x5A
        FIFO_PUT R1             ; PW #0 on page 0
    h1b: JMP   h1b
    """)
    tr = await trace(dut, 700)
    assert first_change(tr, 2, 1) is None, "a stopped thread's event still landed"
    assert reg(dut, 3) & 0x1FF == 1, f"FIFO count {reg(dut, 3) & 0x1FF}: the restarted thread was still on page 1"


@cocotb.test()
async def test_a_timed_write_is_the_operation_it_was(dut):
    """A timed PIN_W lands as a write (OUT <- 0xC3), not as a set, even though an untimed
    PIN_SET changed OUT in between; the untimed OE and UO writes are not delayed."""
    await boot_asm(dut, """
        ESEL  #2
        LDI   R5, 0xC3
        CNT_R R1
        LDI   R2, 100
        ADDS  R1, R1, R2        ; T
        AT    R1
        PIN_W R5                ; OUT <- 0xC3 at T
        UO_WI 0x11
        OE_WI 0x0F
        PIN_SET 0x10            ; immediate: OUT = 0x10 now, 0xC3 replaces it at T
    h:  JMP   h
    """)
    tr = await trace(dut, 200, ("uio_out", "uo_out", "uio_oe"))
    T = reg(dut, 1)
    assert tr[T][0] == 0x10, f"before T: uio_out {tr[T][0]:02x}"
    assert tr[T + LAND][0] == 0xC3, f"after T: uio_out {tr[T + LAND][0]:02x} (a write, not a set)"
    assert tr[T][1] == 0x11 and tr[T][2] == 0x0F, "the untimed writes were delayed"


@cocotb.test()
async def test_assembler_names_need_the_page(dut):
    """AT/TS/AT_STAT without any ESEL #2 in the program are refused (they would be the FIFO's
    ports); ESEL's reserved bits are refused too."""
    for bad in ("AT R1", "TS R2", "AT_STAT R3", "ESEL #16"):
        try:
            assemble(bad)
        except AsmError:
            continue
        raise AssertionError(f"{bad!r} assembled")
    assemble("ESEL #2\nAT R1\nTS R2\nAT_STAT R3\nESEL #0")


# ---------------------------------------------------------------------------
def _analyse(src):
    """tools/msf_timing.py on a source: (certified, the timed-write deadlines, problems)."""
    import os
    import sys
    import tempfile
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
    import msf_timing
    with tempfile.NamedTemporaryFile("w", suffix=".s", delete=False) as f:
        f.write(src)
        path = f.name
    try:
        a, _, _ = msf_timing.analyse(path)
        return msf_timing.certified(a), {pc: dl for pc, dl in a.deadlines.items() if dl.get("kind") == "at"}, \
            [m for _, m in a.problems]
    finally:
        os.unlink(path)


@cocotb.test()
async def test_checker_certifies_a_timed_write_that_arrives_early(dut):
    """The checker follows ESEL, TS and AT: the reply idiom with room to spare is certified
    (slack = the cycles to spare when the write issues), and the same code with too little
    room, or an AT time it cannot derive, is refused with the reason."""
    start4 = "LDI R6, t1\nTSTART #1, R6\nTSTART #2, R6\nTSTART #3, R6\n"   # makes it a threaded program
    def reply(delta, extra=""):
        return f"""
            {start4}
            ESEL #2
            WAIT_EDGE 0, rise
            TS R1
            LDR R2, #{delta}
            ADDS R3, R1, R2
            AT R3
            {extra}
            PIN_SET 0x80
        h:  JMP h
        t1: JMP t1
        """
    ok, dls, problems = _analyse(reply(60))
    assert ok and not problems, problems
    (dl,) = dls.values()
    assert dl["lo"] >= 20 and not dl["late"], dl                   # room to spare
    ok, dls, problems = _analyse(reply(2))
    assert not ok and any("already passed" in m for m in problems), problems
    ok, dls, problems = _analyse(reply(60, extra="LDR R3, #9\n AT R3"))    # the second AT: a constant
    assert not ok and any("not derived" in m for m in problems), problems
    # without ESEL the same PW/PR are the FIFO's ports: PR #0 is a 2-slot load, not TS
    ok, dls, _ = _analyse("LDR R1, #0\nFIFO_GET R2\nh: JMP h\n")
    assert ok and not dls
