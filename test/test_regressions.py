# SPDX-License-Identifier: Apache-2.0
"""Regression tests for bugs found in the 2026-10-16 review (B1-B5).

Each test failed on the v1 RTL / boot ROM and documents the exact symptom:
  B1  instruction after a WAIT executed twice (first copy with pc one low)
  B2  WAIT_EDGE released by a cycle-counter match; WAIT_CMP by pin-0 edges
  B3  ROM/SRAM bank select one cycle out of step with the registered data
  B4  boot ROM lost back-to-back UART bytes
  B5  boot ROM stored garbage when the host started > 5.5 ms after reset
"""
import cocotb
from cocotb.triggers import ClockCycles

from assembler import assemble
from msf_tb import boot_asm, reg, ctrl, sram, start_clock, reset, uart_send
from tools_path import boot_frame


@cocotb.test()
async def test_b1_insn_after_wait_cmp_runs_once(dut):
    await boot_asm(dut, """
        LDR      R1, #0
        CNT_R    R2
        LDR      R3, #20
        ADDS     R2, R2, R3
        WAIT_CMP R2
        INCR     R1
    done: JMP done
    """)
    await ClockCycles(dut.clk, 80)
    assert reg(dut, 1) == 1, f"INCR after WAIT_CMP executed {reg(dut, 1)} times"


@cocotb.test()
async def test_b1_insn_after_wait_edge_runs_once(dut):
    await boot_asm(dut, """
        LDR       R1, #0
        WAIT_EDGE 2
        INCR      R1
    done: JMP done
    """)
    await ClockCycles(dut.clk, 20)
    dut.uio_in.value = 4
    await ClockCycles(dut.clk, 20)
    assert reg(dut, 1) == 1, f"INCR after WAIT_EDGE executed {reg(dut, 1)} times"


@cocotb.test()
async def test_b1_branch_right_after_wait(dut):
    """JNZ immediately after WAIT_EDGE must land exactly on its target."""
    await boot_asm(dut, """
        WAIT_EDGE 2
        JNZ      ok
        LDR      R5, #0xBB      ; fall-through marker
        LDR      R6, #0xEE      ; target-1 marker
    ok: LDR      R4, #0x11
    done: JMP done
    """)
    await ClockCycles(dut.clk, 20)
    dut.uio_in.value = 4
    await ClockCycles(dut.clk, 30)
    assert (reg(dut, 4), reg(dut, 5), reg(dut, 6)) == (0x11, 0, 0)


@cocotb.test()
async def test_b1_back_to_back_waits(dut):
    """Second WAIT_EDGE must wait for a second edge."""
    await boot_asm(dut, """
        WAIT_EDGE 2
        WAIT_EDGE 2
        LDR      R4, #0x22
    done: JMP done
    """)
    await ClockCycles(dut.clk, 20)
    dut.uio_in.value = 4
    await ClockCycles(dut.clk, 40)
    assert reg(dut, 4) == 0 and int(ctrl(dut).stall.value) == 1
    dut.uio_in.value = 0
    await ClockCycles(dut.clk, 20)
    assert reg(dut, 4) == 0x22


@cocotb.test()
async def test_b1_call_after_wait(dut):
    await boot_asm(dut, """
        WAIT_EDGE 2       ; 128
        CALL     sub      ; 129 -> R7 = 130
        LDR      R4, #0x44
    done: JMP done
    sub:  RET
    """)
    await ClockCycles(dut.clk, 20)
    dut.uio_in.value = 4
    await ClockCycles(dut.clk, 40)
    assert reg(dut, 7) == 130 and reg(dut, 4) == 0x44


@cocotb.test()
async def test_b2_wait_edge_not_released_by_counter(dut):
    """No edge => WAIT_EDGE must stall until its timeout (R0 used to leak in)."""
    await boot_asm(dut, """
        LDR       R0, #200
        WAIT_EDGE 2
        JZ        timed_out
        LDR       R4, #0xBA
    timed_out:
        LDR       R5, #0x77
    done: JMP done
    """)
    await ClockCycles(dut.clk, 400)
    assert int(ctrl(dut).stall.value) == 1 and reg(dut, 4) == 0


@cocotb.test()
async def test_b2_wait_cmp_not_released_by_pin0(dut):
    await boot_asm(dut, """
        CNT_R    R2
        LDR      R3, #250
        ADDS     R2, R2, R3
        WAIT_CMP R2
        LDR      R4, #0x44
    done: JMP done
    """)
    await ClockCycles(dut.clk, 30)
    dut.uio_in.value = 1
    await ClockCycles(dut.clk, 10)
    assert int(ctrl(dut).stall.value) == 1 and reg(dut, 4) == 0


@cocotb.test()
async def test_b2_wait_cmp_after_write_of_target(dut):
    """WAIT_CMP on the register written by the previous instruction (no hazard)."""
    await boot_asm(dut, """
        CNT_R    R1
        LDR      R2, #20
        ADDS     R1, R1, R2
        WAIT_CMP R1
        LDR      R4, #0x33
    done: JMP done
    """)
    await ClockCycles(dut.clk, 60)
    assert reg(dut, 4) == 0x33 and int(ctrl(dut).z.value) == 0


@cocotb.test()
async def test_b3_rom_sram_boundary_127(dut):
    """Executing word 127 must run rom[127], not the SRAM copy of 127."""
    await boot_asm(dut, """
        LDR R0, #9
        JMP 127
    """, extra={127: assemble("LDR R5, #0x77")[0]})
    await ClockCycles(dut.clk, 30)
    assert reg(dut, 5) == 0, "fetch mux selected SRAM for ROM address 127"


FW = """
    OE_SET 255
    LDR    R1, #0x42
    PIN_W  R1
done:
    JMP    done
"""


async def _boot_over_uart(dut, pre_idle, gap):
    start_clock(dut)
    words = assemble(FW, base_addr=128)
    await reset(dut, ui_in=0, uio_in=2)
    await ClockCycles(dut.clk, pre_idle)
    for b in boot_frame(words, run=True):
        await uart_send(dut, b, gap=gap)
    await ClockCycles(dut.clk, 3000)
    got = [sram(dut, 128 + i) for i in range(len(words))]
    assert got == words, f"SRAM {[hex(x) for x in got]} != {[hex(x) for x in words]}"
    assert int(dut.uio_out.value) & 0x7E == 0x42 & 0x7E


@cocotb.test()
async def test_b4_boot_back_to_back_bytes(dut):
    await _boot_over_uart(dut, pre_idle=200, gap=0)


@cocotb.test()
async def test_b5_boot_host_starts_late(dut):
    await _boot_over_uart(dut, pre_idle=70000, gap=5000)
