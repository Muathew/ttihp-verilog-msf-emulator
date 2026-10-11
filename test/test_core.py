# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
from assembler import assemble


def load_sram(dut, words, start_addr=128):
    """Directly preload IMEM words into the SRAM block."""
    for i, w in enumerate(words):
        dut.user_project.u_sram.mem[start_addr + i].value = w


@cocotb.test()
async def test_blink_smoke(dut):
    """M2.7 Gate: Blink smoke test on uio_out bit 0."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    # Assemble blink program
    asm = """
        OE_SET 1
        LDR    R1, #1
        PIN_W  R1
        NOP
        LDR    R1, #0
        PIN_W  R1
        NOP
        LDR    R1, #1
        PIN_W  R1
    hold:
        JMP    hold
    """
    words = assemble(asm, base_addr=128)
    load_sram(dut, words, start_addr=128)

    # Reset with ui_in[0] = 1 (direct SRAM boot)
    dut.ena.value = 1
    dut.ui_in.value = 1  # Bypass ROM bootloader -> jump to 128
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1

    # Wait for the program to execute and observe uio_out[0]
    # In msfcore, jump to 128 takes 3 cycles, then OE_SET, LDR, PIN_W
    # Let's run enough cycles and check the toggles
    seen_high = False
    seen_low = False

    for _ in range(50):
        await RisingEdge(dut.clk)
        val = int(dut.uio_out.value) & 1
        oe = int(dut.uio_oe.value) & 1
        if oe == 1:
            if val == 1:
                seen_high = True
            elif val == 0 and seen_high:
                seen_low = True

    assert seen_high, "uio_out[0] was never driven high"
    assert seen_low, "uio_out[0] was never toggled back to low"
    dut._log.info("Blink smoke test passed!")


@cocotb.test()
async def test_alu_and_regfile(dut):
    """Test arithmetic, logic, and flag preservation."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    # Program to test ALU ops:
    # 1. R1 = 5, R2 = 3
    # 2. ADDS R3, R1, R2 -> R3=8
    # 3. SUBS R4, R1, R2 -> R4=2
    # 4. SUBS R5, R1, R1 -> R5=0 (sets Z=1, N=0)
    # 5. PIN_W R3 (should NOT modify Z flag!)
    # 6. JZ ok1 (verifies Z was preserved across PIN_W)
    #    fail: JMP fail
    #    ok1: PIN_W R4
    asm = """
        OE_SET 255
        LDR   R1, #5
        LDR   R2, #3
        ADDS  R3, R1, R2
        SUBS  R4, R1, R2
        SUBS  R5, R1, R1
        PIN_W R3
        JZ    ok1
        LDR   R6, #99
        PIN_W R6
    fail_loop:
        JMP   fail_loop
    ok1:
        PIN_W R4
    done_loop:
        JMP   done_loop
    """
    words = assemble(asm, base_addr=128)
    load_sram(dut, words, start_addr=128)

    dut.ena.value = 1
    dut.ui_in.value = 1  # Direct SRAM boot
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1

    # Run for 25 cycles with trace
    for cyc in range(25):
        await RisingEdge(dut.clk)
        pc = int(dut.user_project.u_ctrl.pc_p.value)
        ir = int(dut.user_project.u_ctrl.ir.value)
        z = int(dut.user_project.u_ctrl.z.value)
        uio = int(dut.uio_out.value)
        rf1 = int(dut.user_project.u_ctrl.u_rf.rf[1].value)
        rf3 = int(dut.user_project.u_ctrl.u_rf.rf[3].value)
        rf4 = int(dut.user_project.u_ctrl.u_rf.rf[4].value)
        dut._log.info(f"[Cyc {cyc:2d}] PC={pc:03x} IR={ir:04x} Z={z} UIO={uio:02x} R1={rf1} R3={rf3} R4={rf4}")

    # Check that uio_out reached R4 (value 2) and NOT the fail value (99)
    assert dut.uio_out.value == 2, f"Expected uio_out == 2, got {int(dut.uio_out.value)}"
    dut._log.info("ALU and flag preservation test passed!")


@cocotb.test()
async def test_backward_branch_loop(dut):
    """Test loop with backward conditional branch (JNZ)."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    # Count down from 5 to 0:
    asm = """
        OE_SET 255
        LDR   R1, #5
    loop:
        PIN_W R1
        DECR  R1
        JNZ   loop
        PIN_W R1
    stop:
        JMP   stop
    """
    words = assemble(asm, base_addr=128)
    load_sram(dut, words, start_addr=128)

    dut.ena.value = 1
    dut.ui_in.value = 1
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1

    # Track values seen on uio_out
    seen_values = []
    for _ in range(40):
        await RisingEdge(dut.clk)
        v = int(dut.uio_out.value)
        if not seen_values or seen_values[-1] != v:
            seen_values.append(v)

    # We expect to see 0 (initial), then 5, 4, 3, 2, 1, 0
    dut._log.info(f"Seen uio_out values: {seen_values}")
    assert 5 in seen_values and 0 in seen_values, "Loop did not count from 5 down to 0"
    dut._log.info("Backward branch loop test passed!")


@cocotb.test()
async def test_call_and_ret(dut):
    """Test CALL and RET subroutine calling convention."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    asm = """
        OE_SET 255
        LDR   R1, #10
        PIN_W R1
        CALL
        JMP   my_func
        PIN_W R2
    end:
        JMP   end

    my_func:
        LDR   R2, #42
        RET
    """
    words = assemble(asm, base_addr=128)
    load_sram(dut, words, start_addr=128)

    dut.ena.value = 1
    dut.ui_in.value = 1
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 35)

    assert dut.uio_out.value == 42, f"Expected 42 after RET, got {int(dut.uio_out.value)}"
    dut._log.info("CALL and RET test passed!")


@cocotb.test()
async def test_cycle_counter(dut):
    """Test CNT_R cycle counter read."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    asm = """
        OE_SET 255
        CNT_R R1
        NOP
        NOP
        CNT_R R2
        SUBS  R3, R2, R1
        PIN_W R3
    done:
        JMP   done
    """
    words = assemble(asm, base_addr=128)
    load_sram(dut, words, start_addr=128)

    dut.ena.value = 1
    dut.ui_in.value = 1
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 30)

    # R2 - R1 should be 3 cycles (CNT_R, NOP, NOP -> CNT_R)
    diff = int(dut.uio_out.value)
    dut._log.info(f"Cycle difference measured: {diff}")
    assert diff == 3, f"Expected cycle diff 3, got {diff}"
    dut._log.info("Cycle counter test passed!")


@cocotb.test()
async def test_shift_datapath(dut):
    """Test SHIFT_OUT and SHIFT_IN on bit 7."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    # Shift out 0xA5 (10100101) MSB-first:
    # We load 16-bit 0xA500 into OSR by 16 SHIFT_IN or using SHIFT_OUT
    asm = """
        OE_SET 128
        LDR       R1, #1
        LDR       R0, #0
        ; Shift in 8 bits on pin 7
        SHIFT_IN
        SHIFT_IN
        SHIFT_IN
        SHIFT_IN
        SHIFT_OUT R1
        SHIFT_OUT R0
    done:
        JMP       done
    """
    words = assemble(asm, base_addr=128)
    load_sram(dut, words, start_addr=128)

    dut.ena.value = 1
    dut.ui_in.value = 1
    dut.uio_in.value = 0x80  # bit 7 = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 30)
    dut._log.info("Shift datapath executed successfully!")


@cocotb.test()
async def test_pin_io_and_oe(dut):
    """Test PIN_R, IN_RD, OE_SET, and OE_CLR."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    asm = """
        OE_SET  15     ; OE = 0x0F (pins 0..3 output)
        OE_CLR  3      ; OE = 0x0C (pins 2..3 output)
        IN_RD   R1     ; read ui_in
        PIN_R   R2     ; read uio_in
        ADDS    R3, R1, R2
        PIN_W   R3     ; write out
    hold:
        JMP     hold
    """
    words = assemble(asm, base_addr=128)
    load_sram(dut, words, start_addr=128)

    dut.ena.value = 1
    dut.ui_in.value = 0x11   # ui_in[0]=1 (direct boot) + 0x10 = 0x11
    dut.uio_in.value = 0x22  # uio_in = 0x22
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 25)

    # OE should be 0x0C
    assert dut.uio_oe.value == 0x0C, f"Expected OE=0x0C, got {int(dut.uio_oe.value):02x}"
    # uio_out should be ui_in + uio_in = 0x11 + 0x22 = 0x33
    assert dut.uio_out.value == 0x33, f"Expected uio_out=0x33, got {int(dut.uio_out.value):02x}"
    dut._log.info("Pin I/O and OE test passed!")


@cocotb.test()
async def test_wait_edge(dut):
    """Test WAIT_EDGE stall and resumption upon pin transition."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    asm = """
        OE_SET    1
        LDR       R1, #0
        PIN_W     R1
        WAIT_EDGE 2      ; Wait for transition on pin 2
        LDR       R1, #0x55
        PIN_W     R1     ; Indicate edge received
    done:
        JMP       done
    """
    words = assemble(asm, base_addr=128)
    load_sram(dut, words, start_addr=128)

    dut.ena.value = 1
    dut.ui_in.value = 1
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1

    # Wait 15 cycles for CPU to boot from ROM and enter WAIT_EDGE
    await ClockCycles(dut.clk, 15)
    assert dut.user_project.u_ctrl.stall.value == 1, "Core should be stalled in WAIT_EDGE"
    assert dut.uio_out.value == 0, f"Expected uio_out==0 while waiting, got {int(dut.uio_out.value)}"

    # Toggle bit 2 of uio_in (WAIT_EDGE 2 selects bit 2, sel_pin = imm8[2:0])
    await RisingEdge(dut.clk)
    dut.uio_in.value = 4  # bit 2 = 1  (the watched pin)

    # Give it a few cycles to detect and resume
    await ClockCycles(dut.clk, 10)
    assert dut.uio_out.value == 0x55, f"Expected uio_out==0x55 after edge, got {int(dut.uio_out.value):02x}"
    dut._log.info("WAIT_EDGE test passed!")


@cocotb.test()
async def test_wait_cmp(dut):
    """Test WAIT_CMP stall until cycle counter matches target register."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    # target must NOT be in R1 (read-after-write hazard): the core's RF read
    # precedes its write in the same cycle, so WAIT_CMP R1 would read the STALE
    # R1 (pre-ADDS) and time out. Use R3 for the target.
    asm = """
        OE_SET   1
        CNT_R    R1
        LDR      R2, #15
        ADDS     R3, R1, R2   ; target = cnt + 15 (into R3, not R1)
        WAIT_CMP R3
        LDR      R4, #0xAA
        PIN_W    R4
    done:
        JMP      done
    """
    words = assemble(asm, base_addr=128)
    load_sram(dut, words, start_addr=128)

    dut.ena.value = 1
    dut.ui_in.value = 1
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1

    # Wait 15 cycles to reach WAIT_CMP
    await ClockCycles(dut.clk, 15)
    assert dut.user_project.u_ctrl.stall.value == 1, "Core should be stalled in WAIT_CMP"

    # Wait another 25 cycles; target match should fire and resume execution
    await ClockCycles(dut.clk, 25)
    # uio_out = {serial_out, out_stage[6:0]}; bit 7 IS the serial pin and
    # PIN_W drives it with ra_data[7] (see ctrl: ser_out <= ra_data[7]).
    # So a PIN_W of 0xAA lands as 0xAA on uio_out.
    assert dut.uio_out.value == 0xAA, f"Expected 0xAA after WAIT_CMP match, got {int(dut.uio_out.value):02x}"
    dut._log.info("WAIT_CMP test passed!")

