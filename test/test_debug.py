# SPDX-License-Identifier: Apache-2.0
"""Debug view (isa.md §8): ui_in[2] high during reset makes uo_out show the
byte of internal state chosen by ui_in[7:2]. Expected values come from the
programs below, not from the RTL's internal signals (except the free-running
cycle counter)."""
import cocotb
from cocotb.triggers import ClockCycles, Timer

from assembler import assemble
from msf_tb import boot_asm, load_words, ctrl

DIRECT = 0b001          # ui_in[0] at reset: run the firmware in SRAM
STRAP = 0b100           # ui_in[2] at reset: debug mode
JMP = 0x09 << 11


async def view(dut, sel):
    """Select a view (no clock edge needed) and read it."""
    dut.ui_in.value = sel << 2 | DIRECT
    await Timer(1, unit="ns")
    return int(dut.uo_out.value)


def self_jump(words, base=128):
    """Address of the first `x: JMP x` in an image."""
    return next(base + i for i, w in enumerate(words) if w == JMP | (base + i))


PROG = """
        LDI    R6, t1
        TSTART R6
        LDI    R1, 0x1234
        LDI    R2, 0xBEEF
        LDR    R3, #0
        DECR   R3          ; 0xFFFF: Z0 = 0, N0 = 1
        LDI    R4, 0x8001
        SR_LD  R4
        PIN_WI 0x3C
        OE_WI  0xC3
        UO_WI  0x5A
    h0: JMP    h0
    t1: LDI    R1, 0xCAFE
        LDI    R5, 0x0FF0
        SUBS   R2, R2, R2  ; Z1 = 1, N1 = 0
        TSTOP              ; single mode again: T0 alone at h0
"""


@cocotb.test()
async def test_debug_view_reads_state(dut):
    words = await boot_asm(dut, PROG, ui_in=STRAP | DIRECT)
    await ClockCycles(dut.clk, 80)
    h0 = self_jump(words)
    t1 = h0 + 1
    regs0 = [0, 0x1234, 0xBEEF, 0xFFFF, 0x8001, 0, t1, 0]
    regs1 = [0, 0xCAFE, 0, 0, 0, 0x0FF0, 0, 0]
    for t, regs in enumerate((regs0, regs1)):
        for r, v in enumerate(regs):
            got = (await view(dut, t << 4 | r << 1 | 1)) << 8 | await view(dut, t << 4 | r << 1)
            assert got == v, f"T{t} R{r}: {got:#06x} != {v:#06x}"
    expect = {
        0x20: h0 & 0xFF,
        0x21: h0 >> 8,              # PC[9:8]; T0 issued last, single mode, nothing stalled
        0x22: (JMP | h0) & 0xFF, 0x23: (JMP | h0) >> 8,
        0x24: 0b0110,               # N1 Z1 N0 Z0
        0x27: 0x01, 0x28: 0x80,     # SR
        0x29: 0x3C, 0x2A: 0xC3, 0x2B: 0x5A,
        0x2C: 0, 0x2D: 0,           # FIFO empty, no drops
    }
    expect.update({s: 0 for s in range(0x2E, 0x40)})
    for sel, v in expect.items():
        assert await view(dut, sel) == v, f"view {sel:#04x}"
    lo = await view(dut, 0x25)
    assert lo == int(ctrl(dut).cnt.value) & 0xFF, "CNT low byte"
    assert int(dut.uio_out.value) == 0x3C, "the debug view leaves uio alone"


@cocotb.test()
async def test_debug_view_pc_in_bank1(dut):
    """The last issued PC's bits 9:8 (view 0x21) for code in SRAM bank 1."""
    words = await boot_asm(dut, """
        JMP  far
    .org 950
    far: JMP far
    """, ui_in=STRAP | DIRECT)
    await ClockCycles(dut.clk, 40)
    assert (await view(dut, 0x21)) & 3 == 950 >> 8
    assert await view(dut, 0x20) == 950 & 0xFF
    assert len(words) == 950 - 128 + 1


@cocotb.test()
async def test_debug_mode_off_without_strap(dut):
    """Without the strap uo_out is the firmware's, whatever ui_in[7:2] is."""
    await boot_asm(dut, PROG, ui_in=DIRECT)
    await ClockCycles(dut.clk, 80)
    for sel in (0x00, 0x13, 0x20, 0x3F):
        assert await view(dut, sel) == 0x5A


@cocotb.test()
async def test_debug_single_step(dut):
    """Clock the chip one edge at a time (as the demo board's RP2040 can) and
    read the last issued PC and a register between edges: the PC trace is
    the program's order, and R1 counts the loop's INCRs."""
    words = assemble("""
        LDR  R1, #0
    lp: INCR R1
        NOP
        JMP  lp
    """, base_addr=128)
    load_words(dut, words, 128)

    async def edge():
        dut.clk.value = 1
        await Timer(5, unit="ns")
        dut.clk.value = 0
        await Timer(5, unit="ns")

    dut.ena.value = 1
    dut.uio_in.value = 0
    dut.clk.value = 0
    dut.ui_in.value = STRAP | DIRECT
    dut.rst_n.value = 0
    for _ in range(5):
        await edge()
    dut.rst_n.value = 1
    pcs = []
    for _ in range(60):
        await edge()
        pcs.append((await view(dut, 0x21) & 3) << 8 | await view(dut, 0x20))
    trace = [p for i, p in enumerate(pcs) if i == 0 or p != pcs[i - 1]]
    run = trace[trace.index(128):]
    loop = [129, 130, 131]
    assert run[:1 + 3 * 4] == [128] + loop * 4, f"PC trace {run}"
    incrs = run.count(129)
    r1 = (await view(dut, 0x03)) << 8 | await view(dut, 0x02)
    assert r1 == incrs, f"R1 = {r1}, INCR issued {incrs} times"


async def view1(dut, sel):
    """A view on page 1 (ui_in[1] = 1): T2/T3 registers, threads."""
    dut.ui_in.value = sel << 2 | 0b10 | DIRECT
    await Timer(1, unit="ns")
    return int(dut.uo_out.value)


@cocotb.test()
async def test_debug_view_page1(dut):
    """Page 1: T2 and T3 registers, the running and stalled threads, every
    thread's flags and fetch pointers (four threads, all parked)."""
    words = await boot_asm(dut, """
        LDI    R2, t1
        TSTART R2
        LDI    R2, t2
        TSTART #2, R2
        LDI    R2, t3
        TSTART #3, R2
    h0: JMP h0
    t1: JMP t1
    t2: LDI R1, 0x2211
        LDI R7, 0x2277
        LDR R3, #0
        DECR R3                 ; Z2 = 0, N2 = 1
    h2: JMP h2
    t3: LDI R1, 0x3311
        LDR R3, #1
        DECR R3                 ; Z3 = 1
        WAIT_EDGE 0             ; T3 stalls for good (uio0 never moves)
    """, ui_in=STRAP | DIRECT)
    await ClockCycles(dut.clk, 120)
    for t, regs in ((2, {1: 0x2211, 7: 0x2277, 3: 0xFFFF}), (3, {1: 0x3311, 3: 0})):
        for r, v in regs.items():
            base = (t - 2) << 4 | r << 1
            got = (await view1(dut, base | 1)) << 8 | await view1(dut, base)
            assert got == v, f"T{t} R{r}: {got:#06x} != {v:#06x}"
    assert await view1(dut, 0x20) == 0xF0 | 0x08     # all run, T3 stalled
    flags = await view1(dut, 0x21)
    assert (flags >> 4) & 3 == 0b10 and (flags >> 6) & 3 == 0b01, f"flags {flags:#04x}"
    h0 = self_jump(words)                            # T0 parks at h0, the first self-jump
    assert await view1(dut, 0x22) == h0 & 0xFF       # T0's fetch pointer
