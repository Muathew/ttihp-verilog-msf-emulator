# SPDX-License-Identifier: Apache-2.0
"""The late guard (isa.md section 6.5, v3): LATE_CLR with bit 15 set names threads
(bits 11-8) and uio pins (bits 7-0); while a named thread's LATE flag is set, those
pins' output enables are forced off, from the cycle its late WAIT_CMP released until
the flag is cleared. LATE_RD returns {pins, threads, LATE}."""
import cocotb
from cocotb.triggers import ClockCycles, RisingEdge

from msf_tb import boot_asm, reg

DELAY = """
        LDR      R0, #20
{0}:    DECR     R0
        JNZ      {0}
"""


async def until_uo(dut, v, limit=2000):
    for _ in range(limit):
        if int(dut.uo_out.value) == v:
            return
        await RisingEdge(dut.clk)
    raise AssertionError(f"uo_out never {v}")


@cocotb.test()
async def test_guard_one_thread(dut):
    """T0 guards pins 0-3: its late WAIT_CMP takes them off the bus before the next
    instruction's effect, LATE_CLR puts them back; guarding T1 only leaves T0 alone."""
    await boot_asm(dut, f"""
        OE_SET   0xFF
        PIN_WI   0xAA
        LDI      R1, 0x810F         ; the guard: T0, pins 0-3
        LATE_CLR R1
        LATE_RD  R2
        UO_WI    1
{DELAY.format("d1")}
        CNT_R    R3
        WAIT_CMP R3                 ; late
        LATE_RD  R4
        UO_WI    2
{DELAY.format("d2")}
        LDR      R5, #1
        LATE_CLR R5                 ; T0's flag cleared: the pins are back
        LATE_RD  R6
        UO_WI    3
{DELAY.format("d3")}
        LDI      R1, 0x820F         ; the guard: T1 only
        LATE_CLR R1
        CNT_R    R3
        WAIT_CMP R3                 ; T0 late again: not guarded
        LATE_RD  R7
        UO_WI    4
    halt: JMP halt
    """)
    await until_uo(dut, 1)
    assert int(dut.uio_oe.value) == 0xFF and int(dut.uio_out.value) == 0xAA
    assert reg(dut, 2) == 0x0F10, hex(reg(dut, 2))
    t, off_at = 0, None
    while int(dut.uo_out.value) != 2:
        await RisingEdge(dut.clk)
        t += 1
        if off_at is None and int(dut.uio_oe.value) == 0xF0:
            off_at = t
        assert t < 2000
    assert off_at is not None and off_at < t, (off_at, t)
    assert int(dut.uio_oe.value) == 0xF0 and int(dut.uio_out.value) == 0xAA
    assert reg(dut, 4) == 0x0F11, hex(reg(dut, 4))
    await until_uo(dut, 3)
    assert int(dut.uio_oe.value) == 0xFF
    assert reg(dut, 6) == 0x0F10, hex(reg(dut, 6))
    await until_uo(dut, 4)
    await ClockCycles(dut.clk, 4)
    assert int(dut.uio_oe.value) == 0xFF, "T1's guard took T0's pins"
    assert reg(dut, 7) == 0x0F21, hex(reg(dut, 7))


@cocotb.test()
async def test_guard_other_thread(dut):
    """T0 guards T1's pins 4-7; T1 is late, so they go off the bus while T0's pins
    0-3 stay on; T0 clears T1's flag and they come back."""
    await boot_asm(dut, f"""
        OE_SET   0xFF
        LDI      R1, 0x82F0         ; the guard: T1, pins 4-7
        LATE_CLR R1
        LDI      R1, t1
        TSTART   R1
{DELAY.format("d1")}
        LATE_RD  R2
        UO_WI    1
{DELAY.format("d2")}
        LDR      R5, #2
        LATE_CLR R5
        LATE_RD  R6
        UO_WI    2
    halt: JMP halt
    t1: CNT_R    R1
        WAIT_CMP R1                 ; late
    spin: JMP    spin
    """)
    await until_uo(dut, 1)
    assert int(dut.uio_oe.value) == 0x0F, hex(int(dut.uio_oe.value))
    assert reg(dut, 2) == 0xF022, hex(reg(dut, 2))
    await until_uo(dut, 2)
    assert int(dut.uio_oe.value) == 0xFF
    assert reg(dut, 6) == 0xF020, hex(reg(dut, 6))
