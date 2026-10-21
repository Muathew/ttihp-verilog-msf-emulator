# SPDX-License-Identifier: Apache-2.0
"""Hardware-thread tests (isa.md §7). Expected values derived from the spec;
test_random_diff.test_random_dual_thread cross-checks the model at scale."""
import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, ReadOnly

from msf_tb import boot_asm, reg, ctrl, sram

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


@cocotb.test()
async def test_tmo_per_thread(dut):
    """Each thread has its own TMO; T1 starts with its starter's (isa.md §7).
    T1's WAIT times out with the 10 it inherited although T0 then set 0, and
    T1's own TMO write leaves T0's alone. Dual-mode timeout: the release is
    in T1's first own slot at or after expiry, so TMO+2 = 12 stalled cycles
    after the WAIT's issue slot, and CNT_R to CNT_R is 2 + 12 + 2 = 16."""
    await boot_asm(dut, CLEAR + """
        LDR    R1, #10
        TMO    R1
        LDI    R2, t1
        TSTART R2          ; T1 inherits TMO = 10
        LDR    R1, #0
        TMO    R1          ; T0: no timeout from now on
        WAIT_EDGE 0        ; uio[0] never moves: T0 waits for ever
    h0: JMP h0
    t1: CNT_R  R3
        WAIT_EDGE 0
        CNT_R  R4
        LDR    R5, #0
        JNZ    t1n
        LDR    R5, #1      ; Z = 1: timed out
  t1n:  LDR    R1, #3
        TMO    R1          ; T1's own TMO
    h1: JMP h1
    """)
    await ClockCycles(dut.clk, 120)
    assert reg(dut, 5, 1) == 1, "T1's WAIT did not time out"
    assert reg(dut, 4, 1) - reg(dut, 3, 1) == 16
    c = ctrl(dut)
    assert int(c.st.value) & 1, "T0's WAIT (TMO 0) should still be waiting"
    tmo = int(c.tmo.value)
    assert (tmo & 0xFFFF, tmo >> 16) == (0, 3)


@cocotb.test()
async def test_fifo_between_threads(dut):
    """T0 pushes 20 words at its own pace, T1 pops and stores them: all
    arrive, in order, none dropped (default FIFO: SRAM 64..127, 64 words)."""
    await boot_asm(dut, CLEAR + """
        LDI    R2, t1
        TSTART R2
        LDI    R1, 0x200
        LDR    R6, #20
    lp: FIFO_PUT R1
        INCR   R1
        LDR    R3, #5
    d:  DECR   R3
        JNZ    d
        DECR   R6
        JNZ    lp
    h0: JMP h0
    t1: LDR    R5, #0
        LDR    R6, #20
    g:  FIFO_GET R1
        JZ     g           ; empty: try again
        ST     R5, R1
        INCR   R5
        DECR   R6
        JNZ    g
        FIFO_STAT R4
    h1: JMP h1
    """)
    await ClockCycles(dut.clk, 900)
    assert [sram(dut, i) for i in range(20)] == list(range(0x200, 0x214))
    assert reg(dut, 4, 1) == 0, "FIFO should be empty with no drops"


@cocotb.test()
async def test_wait_edge_every_pin_both_threads(dut):
    """Each thread waits for a rising edge on every uio and ui pin in turn
    while the other seven pins of that bank are high: a wait must release
    on its own pin's edge, never earlier (catches a pin read through the
    wrong select, or one pin leaking into another)."""
    waits = "\n".join(f"WAIT_EDGE {p}, rise, {bank}\nUO_WI {k + 1}"
                      for k, (bank, p) in enumerate(
                          [("uio", p) for p in range(8)] + [("ui", p) for p in range(8)]))
    for thread in (0, 1):
        body = "LDR R1, #0\nTMO R1\n" + waits + "\nhw: JMP hw\n"
        asm = body if thread == 0 else ("LDR R1, #0\nTMO R1\nLDI R2, t1\nTSTART R2\n"
                                        "h0: JMP h0\nt1: " + body)
        await boot_asm(dut, CLEAR + asm, ui_in=1, uio_in=0)
        await ClockCycles(dut.clk, 40)
        for k in range(16):
            bank, p = ("uio", k) if k < 8 else ("ui", k - 8)
            others = 0xFF & ~(1 << p)
            sig = dut.uio_in if bank == "uio" else dut.ui_in
            sig.value = others
            await ClockCycles(dut.clk, 30)
            assert int(dut.uo_out.value) == k, f"T{thread}: wait {k} ({bank}{p}) released early"
            sig.value = 0xFF
            await ClockCycles(dut.clk, 12)
            assert int(dut.uo_out.value) == k + 1, f"T{thread}: wait {k} ({bank}{p}) missed its edge"
            sig.value = 0 if bank == "uio" else 1


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
