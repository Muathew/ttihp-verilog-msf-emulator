# SPDX-License-Identifier: Apache-2.0
"""v4 step 1: the fixes from the RTL review (docs/plan-v4.md section 5, B1-B5, B11).

B1  a store into code: a word is safe to issue three cycles after the store lands.
    The ISA model refuses less (StoreAheadError); tb.v's detector sees it on the RTL;
    at the boundary RTL and model agree, in single and multi-thread mode.
B2  the reset is released through two flops: the same start at any phase of the release,
    and still asserted at once, with no clock edge.
B3  the debug-view strap is loaded by a reset pulse of two clock edges (v3 missed it).
B4  the pads come straight from flops and equal the old combinational expressions in every
    cycle, whatever the engines, OE and the late guard do (formal F18 proves it for all
    programs; this runs it on the engines' real behaviour).
B11 the assembler refuses `TSTART #4` .. `#7`, which the hardware would alias to 0-3.
"""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Edge, FallingEdge, ReadOnly, RisingEdge, Timer

from assembler import assemble, AsmError
from diffcheck import run_and_compare
from isa_model import IsaModel, StoreAheadError
from msf_tb import boot_asm, reg, ctrl, start_clock, reset

CYCLES = 200


# ---------------------------------------------------------------------------- B1
def model_run(src, cycles):
    """The program on the ISA model alone: a ROM of `JMP 128`, the program at 128."""
    m = IsaModel([assemble("JMP 128")[0]],
                 {128 + i: w for i, w in enumerate(assemble(src, base_addr=128))},
                 ui_pad=lambda c: 1)
    m.run(cycles)
    return m


def patch_program(ahead):
    """A NOP at `ahead` instructions after the store is replaced by INCR R1: with
    ahead >= 3 it runs once per pass."""
    inc = assemble("INCR R1")[0]
    pad = "\n".join("NOP" for _ in range(ahead - 1))
    return f"""
        LDR  R1, #0
    top:
        LDI  R2, target
        LDI  R3, {inc}
        ST   R2, R3
{pad}
    target:
        NOP
        JMP  top
    """


@cocotb.test()
async def test_model_refuses_a_store_closer_than_three(dut):
    """The ISA model raises StoreAheadError at W+1 and W+2 and accepts W+3."""
    for ahead in (1, 2):
        try:
            model_run(patch_program(ahead), 100)
        except StoreAheadError:
            continue
        raise AssertionError(f"the model accepted a store {ahead} instruction(s) ahead")
    m = model_run(patch_program(3), 100)
    assert m.th[0].regs[1] > 2, m.th[0].regs[1]


@cocotb.test()
async def test_store_three_ahead_runs_the_new_word(dut):
    """Single thread, exactly the boundary: the new word runs, RTL == model on every
    issue, and the detector (start_clock's watcher) stays quiet."""
    start_clock(dut)
    await run_and_compare(dut, patch_program(3), CYCLES, tag="W+3, one thread")
    # the model alone: R1 counted the passes
    m = model_run(patch_program(3), CYCLES)
    assert m.th[0].regs[1] > 5, m.th[0].regs[1]


@cocotb.test()
async def test_store_ahead_two_threads(dut):
    """With T1 running a thread owns every other cycle, so W+2 is already far enough:
    T0 patches its own word two instructions ahead (the same program, one NOP less)."""
    start_clock(dut)
    src = patch_program(2).replace("LDR  R1, #0", """LDR R1, #0
        LDI R5, t1
        TSTART R5""") + "\n    t1: NOP\n        JMP t1\n"
    await run_and_compare(dut, src, CYCLES * 2, tag="W+2, two threads")


@cocotb.test()
async def test_detector_sees_the_old_word_run(dut):
    """What the rule is for, on the RTL: a store two instructions ahead lands too late,
    the old word runs, and tb.v's detector toggles. (Not start_clock: its watcher would
    fail this test on purpose.)"""
    cocotb.start_soon(Clock(dut.clk, 80, unit="ns").start())
    dut.ena.value = 1
    words = assemble(patch_program(2), base_addr=128)
    for i, w in enumerate(words):
        dut.user_project.u_sram.mem[128 + i].value = w
    await reset(dut, ui_in=1)
    first = int(dut.sa_evt.value)
    for _ in range(80):
        await RisingEdge(dut.clk)
        if int(dut.sa_evt.value) != first:
            break
    else:
        raise AssertionError("the detector never fired")
    # the old word (a NOP) ran: INCR has not run, and the next pass is several cycles away
    await ClockCycles(dut.clk, 5)
    assert int(dut.user_project.u_ctrl.u_rf.rf[1].value) == 0


# ---------------------------------------------------------------------------- B2, B3
FIRST = "CNT_R R1\nh: JMP h"          # R1 = the core's own cycle count at its first SRAM issue


async def pulse_reset(dut, release_ns, ui_in=1):
    """rst_n low for five clocks, rising `release_ns` after a rising edge."""
    dut.ui_in.value = ui_in
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    await Timer(release_ns, unit="ns")
    dut.rst_n.value = 1


@cocotb.test()
async def test_reset_released_at_any_phase(dut):
    """B2: whatever the phase of rst_n's release, the core starts the same way: the
    first SRAM instruction issues at core cycle 8. In zero-delay simulation every
    phase behaves alike; the point is the synchroniser stays in step with everything
    (the gate-level run with delays is where the phases differ)."""
    start_clock(dut)
    dut.ena.value = 1
    words = assemble(FIRST, base_addr=128)
    for phase in (1, 20, 40, 60, 79):
        for i, w in enumerate(words):
            dut.user_project.u_sram.mem[128 + i].value = w
        await pulse_reset(dut, phase)
        await ClockCycles(dut.clk, 40)
        got = int(dut.user_project.u_ctrl.u_rf.rf[1].value)
        assert got == 8, f"release {phase} ns after the edge: first issue at cycle {got}"


@cocotb.test()
async def test_reset_asserts_without_a_clock_edge(dut):
    """B2: the assertion is asynchronous. A pulse of 10 ns that sits between two clock
    edges resets the whole core (registers back to 0, the program restarts) and the
    synchroniser holds it for two more edges."""
    start_clock(dut)
    await boot_asm(dut, "LDR R1, #0\nl: INCR R1\nJMP l")
    await ClockCycles(dut.clk, 60)
    before = reg(dut, 1)
    assert before > 10, before
    await FallingEdge(dut.clk)
    await Timer(10, unit="ns")                 # inside the low phase: no edge for 30 ns more
    dut.rst_n.value = 0
    await Timer(10, unit="ns")
    dut.rst_n.value = 1
    await Timer(1, unit="ns")
    assert reg(dut, 1) == 0, "the register file was not reset by a pulse with no clock edge"
    await ClockCycles(dut.clk, 30)
    assert 0 < reg(dut, 1) < before, f"program did not restart: {reg(dut, 1)} (was {before})"


@cocotb.test()
async def test_debug_strap_loaded_by_a_two_edge_reset(dut):
    """B3: ui_in[2] raised together with a reset pulse of only two clock edges still
    selects the debug view (v3 sampled ui_sync, two edges behind the pad, while rst_n was
    low, and missed it)."""
    start_clock(dut)
    await boot_asm(dut, "h: JMP h")            # a normal boot first: dbg_mode = 0
    await ClockCycles(dut.clk, 10)
    assert int(dut.user_project.dbg_mode.value) == 0
    dut.ui_in.value = 0b0000_0101              # strap + direct boot, with the reset pulse
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 6)
    assert int(dut.user_project.dbg_mode.value) == 1, "the strap was missed"
    # and a pulse with the strap low clears it again
    dut.ui_in.value = 0b0000_0001
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 6)
    assert int(dut.user_project.dbg_mode.value) == 0


# ---------------------------------------------------------------------------- B4
ENGINES = """
        .include "msf.inc"
        OE_SET   0xFF
        PIN_WI   0xFF                      ; OUT and the engine both 1: the glitch case
        LDI      R1, TX_EN|TX_RELEASE|TX_LSB|0x0700
        TX_CFG   R1                        ; NRZ, pin 0, release mode
        LDR      R2, #6
        TX_DIV   R2
        LDR      R3, #0xFF
        TX_PUSH  R3
        TX_PUSH  R3
        LDR      R3, #0x5A
        TX_PUSH  R3
        OE_TGL   0x04                      ; OE and OUT move while the engine owns pin 0
        PIN_TGL  0x02
        TX_WAIT
        LDI      R1, TX_EN|TX_NRZI|TX_STUFF|TX_DIFF|TX_LSB|0x0700|2
        TX_CFG   R1                        ; USB-style: NRZI, stuffing, pins 2 and 3 differential
        LDR      R2, #4
        TX_DIV   R2
        LDI      R3, 0x00FF
        TX_PUSH  R3
        TX_PUSH  R3
        TX_WAIT
        LDI      R1, TX_EN|TX_MANCH|0x0700|4
        TX_CFG   R1                        ; Manchester on pin 4
        LDR      R2, #3
        TX_DIV   R2
        LDR      R3, #0xA5
        TX_PUSH  R3
        TX_PUSH  R3
        TX_WAIT
        LDR      R1, #0
        TX_CFG   R1                        ; the engine lets go
        LDI      R1, 0x810F
        LATE_CLR R1                        ; the late guard: T0, pins 0-3
        CNT_R    R3
        WAIT_CMP R3                        ; late: pins 0-3 come off the bus
        LDR      R5, #1
        LDR      R0, #30
    w1: DECR     R0
        JNZ      w1
        LATE_CLR R5                        ; and back
    halt: JMP halt
"""


@cocotb.test()
async def test_pads_equal_the_old_expressions(dut):
    """B4: in every cycle uio_out and uio_oe are what (OUT & ~mask) | (val & mask) and
    OE & ~guard give, through three engine modes, OE and OUT writes while the engine
    owns the pin, and the late guard going on and off."""
    await boot_asm(dut, ENGINES)
    pio = dut.user_project.u_pio
    seen = set()
    for c in range(1500):
        await RisingEdge(dut.clk)
        await ReadOnly()
        o, oc = int(dut.uio_out.value), int(pio.uio_out_c.value)
        e, ec = int(dut.uio_oe.value), int(pio.uio_oe_c.value)
        assert o == oc and e == ec, f"cycle {c}: uio_out {o:02x} vs {oc:02x}, uio_oe {e:02x} vs {ec:02x}"
        seen.add((o, e))
    assert len(seen) >= 8, f"the program drove only {len(seen)} different pad states"
    assert any(e != 0xFF for _, e in seen), "the late guard never took a pin off the bus"


# ---------------------------------------------------------------------------- B11
@cocotb.test()
async def test_assembler_refuses_threads_four_to_seven(dut):
    """TSTART/TSTOP read two bits of the thread field: #4-#7 would alias #0-#3."""
    for ok in ("TSTART #1, R2", "TSTART #3, R2", "TSTOP #2", "TSTART R2", "TSTOP"):
        assemble(ok)
    for bad in ("TSTART #4, R2", "TSTART #7, R2", "TSTOP #5"):
        try:
            assemble(bad)
        except AsmError:
            continue
        raise AssertionError(f"{bad!r} assembled")
