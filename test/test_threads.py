# SPDX-License-Identifier: Apache-2.0
"""Hardware-thread tests (isa.md §7). Expected values derived from the spec;
test_random_diff.test_random_dual_thread cross-checks the model at scale."""
import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, ReadOnly

from msf_tb import boot_asm, reg, ctrl

CLEAR = "\n".join(f"LDR R{i}, #0" for i in range(8)) + "\n"


@cocotb.test()
async def test_tstart_isolated_registers_and_tid(dut):
    await boot_asm(dut, CLEAR + """
        LDR    R1, #0x11
        LDI    R2, t1
        TSTART R2
        TID    R3          ; 0
        LDR    R4, #0x44
    h0: JMP h0
    t1: LDR    R1, #0x77   ; T1's own R1
        TID    R3          ; 1
        STAT   R5          ; bit5 dual, bit6 thread
    h1: JMP h1
    """)
    await ClockCycles(dut.clk, 60)
    assert (reg(dut, 1), reg(dut, 3), reg(dut, 4)) == (0x11, 0, 0x44)
    assert (reg(dut, 1, 1), reg(dut, 3, 1)) == (0x77, 1)
    assert reg(dut, 5, 1) == 0x60
    assert reg(dut, 2, 1) == 0, "T1 registers start cleared"


@cocotb.test()
async def test_dual_branch_costs_one_slot(dut):
    """In dual mode a taken branch costs one slot (2 cycles), no bubble."""
    await boot_asm(dut, CLEAR + """
        LDI    R2, t1
        TSTART R2
        CNT_R  R1
        JMP    j1
    j1: JMP    j2
    j2: CNT_R  R3
        SUBS   R4, R3, R1     ; 3 slots later = 6 cycles
    h0: JMP h0
    t1: JMP t1
    """)
    await ClockCycles(dut.clk, 60)
    assert reg(dut, 4) == 6


async def t0_toggle_times(dut, t1_body, cycles=600):
    """T0 toggles uio[0] with a WAIT-free loop; return the toggle cycles."""
    await boot_asm(dut, CLEAR + """
        OE_SET 1
        LDI    R2, t1
        TSTART R2
    lp: PIN_TGL 1
        LDR    R1, #3
    d:  DECR   R1
        JNZ    d
        JMP    lp
    t1:
    """ + t1_body)
    times, last = [], 0
    for c in range(cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        v = int(dut.uio_out.value) & 1
        if v != last:
            times.append(c)
            last = v
    return times


@cocotb.test()
async def test_t0_timing_independent_of_t1(dut):
    """Deterministic threads: T0's pin timing is identical whatever T1 does."""
    a = await t0_toggle_times(dut, "spin: JMP spin")
    b = await t0_toggle_times(dut, """
        LDR R1, #5
    w:  CNT_R R2
        LDR R3, #9
        ADDS R2, R2, R3
        WAIT_CMP R2
        LDR R4, #1
        LD  R5, R4
        ST  R4, R5
        DECR R1
        JNZ w
        LDR R6, #3
        CRC_W R6
        CRC_RDL R6
        JMP w
    """)
    assert len(a) > 20 and a == b
    period = {y - x for x, y in zip(a, a[1:])}
    assert period == {18}, f"T0 loop = 9 slots = 18 cycles, got {period}"


@cocotb.test()
async def test_tstop_from_t1_restores_full_rate(dut):
    await boot_asm(dut, CLEAR + """
        LDI    R2, t1
        TSTART R2
    w:  STAT   R3
        LDR    R4, #0x20
        AND    R3, R3, R4
        SUBS   R3, R3, R0
        JNZ    w            ; until T1 has stopped
        CNT_R  R5
        NOP
        NOP
        CNT_R  R6
        SUBS   R7, R6, R5   ; single mode: 3 cycles
    h0: JMP h0
    t1: LDR    R1, #9
        TSTOP
        LDR    R1, #0xEE    ; never executes
    """)
    await ClockCycles(dut.clk, 80)
    assert reg(dut, 7) == 3 and int(ctrl(dut).dual.value) == 0
    assert reg(dut, 1, 1) == 9


@cocotb.test()
async def test_tstop_from_t0_and_restart(dut):
    await boot_asm(dut, CLEAR + """
        LDI    R2, t1
        TSTART R2
        NOP
        NOP
        TSTOP              ; T0 stops T1 (T0 pays one bubble)
        CNT_R  R5
        TSTART R2          ; restart: T1 state cleared, counts from 0 again
        LDR    R6, #0x66
    h0: JMP h0
    t1: INCR   R1
        JMP    t1
    """)
    await ClockCycles(dut.clk, 12)
    await ClockCycles(dut.clk, 20)
    assert reg(dut, 6) == 0x66 and int(ctrl(dut).dual.value) == 1
    assert 0 < reg(dut, 1, 1) < 12, f"T1 R1 = {reg(dut, 1, 1)} (should have restarted)"


@cocotb.test()
async def test_event_in_other_threads_slot_not_lost(dut):
    """A 1-cycle pulse seen in T0's slot still releases T1's WAIT_EDGE."""
    await boot_asm(dut, CLEAR + """
        LDI    R2, t1
        TSTART R2
    h0: JMP h0
    t1: WAIT_EDGE 3, rise
        LDR    R1, #0x31
    h1: JMP h1
    """)
    await ClockCycles(dut.clk, 30)
    for _ in range(2):                    # try both cycle parities
        await RisingEdge(dut.clk)
    dut.uio_in.value = 0x08
    await RisingEdge(dut.clk)
    dut.uio_in.value = 0x00               # one-cycle pulse
    await ClockCycles(dut.clk, 20)
    assert reg(dut, 1, 1) == 0x31


@cocotb.test()
async def test_both_threads_blocking(dut):
    """T0 WAIT_CMP and T1 WAIT_EDGE stalled at the same time."""
    await boot_asm(dut, CLEAR + """
        LDI    R2, t1
        TSTART R2
        CNT_R  R1
        LDR    R3, #100
        ADDS   R1, R1, R3
        WAIT_CMP R1
        CNT_R  R4
        SUBS   R5, R4, R1      ; released in a T0 slot: 1 or 2 cycles after CNT==T
    h0: JMP h0
    t1: WAIT_EDGE 6
        LDR    R7, #0x77
    h1: JMP h1
    """)
    await ClockCycles(dut.clk, 50)
    dut.uio_in.value = 0x40
    await ClockCycles(dut.clk, 120)
    assert reg(dut, 7, 1) == 0x77
    assert reg(dut, 5) in (1, 2, 3), f"T0 woke {reg(dut, 5)} cycles after its target"
