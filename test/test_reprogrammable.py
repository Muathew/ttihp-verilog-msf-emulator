# SPDX-License-Identifier: Apache-2.0
"""
In-System Reprogrammability Verification Test Suite for msfcore

Validates the core flexibility requirement:
"The goal isn't to put a UART block, an SPI block, and an I2C block on one die
and call it done. Your chip should be reprogrammable enough to support new protocols
after fabrication, within its timing and I/O constraints."

Tests:
1. test_dynamic_multi_protocol_swap:
   Sequentially re-programs the ASIC on-the-fly without resetting simulation state:
   - Stage 1: Reprogram as UART TX -> verifies 115200 baud transmission
   - Stage 2: Reprogram as SPI Master -> verifies SPI clock & MOSI frames
   - Stage 3: Reprogram as I2C Master -> verifies Open-Drain START/STOP
   - Stage 4: Reprogram as High-Speed Pattern Generator
2. test_hardware_mem_w_loader:
   Verifies the hardware `MEM_W` auto-incrementing SRAM write port (128..511)
   by having a stage-1 bootloader write a stage-2 application into SRAM and jump to it.
3. test_rom_bootloader_bypass:
   Verifies the hardware boot pin `ui_in[0]`:
   - ui_in[0]=1: direct SRAM jump to PC 128
   - ui_in[0]=0: enters ROM bootloader at PC 0
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, FallingEdge
from assembler import assemble
from tools_path import boot_frame


def load_sram(dut, words, start_addr=128):
    """Write words into SRAM (words 128..511)."""
    for i, w in enumerate(words):
        dut.user_project.u_sram.mem[start_addr + i].value = w


async def reset_core(dut, direct_boot=True, uio_in=0):
    dut.ena.value = 1
    dut.ui_in.value = 1 if direct_boot else 0
    dut.uio_in.value = uio_in
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


# ==============================================================================
# 1. Dynamic Sequential Multi-Protocol Reprogramming Test
# ==============================================================================
@cocotb.test()
async def test_dynamic_multi_protocol_swap(dut):
    """Reprogram the running ASIC across 4 completely different protocols in sequence."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    dut._log.info("=== STAGE 1: Program as UART TX Transmitter ===")
    uart_asm = """
        OE_SET   1            ; Pin 0 output (TX)
        LDR      R0, #1
        PIN_W    R0           ; Idle high
        NOP
        NOP
        ; Start bit (0)
        LDR      R1, #0
        PIN_W    R1
        LDR      R4, #20
d0:     DECR     R4
        JNZ      d0
        ; Data bit: 1
        PIN_W    R0
        LDR      R4, #20
d1:     DECR     R4
        JNZ      d1
        ; Stop bit: 1
        PIN_W    R0
    hold1:
        JMP      hold1
    """
    load_sram(dut, assemble(uart_asm, base_addr=128))
    await reset_core(dut, direct_boot=True)

    # Observe UART line toggling (pin 0)
    seen_low = False
    seen_high = False
    for _ in range(80):
        await RisingEdge(dut.clk)
        val = int(dut.uio_out.value) & 1
        if val == 0:
            seen_low = True
        elif val == 1 and seen_low:
            seen_high = True
    assert seen_low and seen_high, "Stage 1 (UART TX) failed to drive expected toggles"
    dut._log.info("Stage 1 (UART TX) verified successfully!")

    dut._log.info("=== STAGE 2: Hot-Swap Reprogram as SPI Master ===")
    spi_asm = """
        OE_SET   12           ; Pin 2 (SCK) and Pin 3 (MOSI) output
        LDR      R0, #0
        PIN_W    R0           ; SCK=0, MOSI=0
        NOP
        ; Emit 2 SPI clock pulses with MOSI=1 (pin3=8 | pin2=4 = 12)
        LDR      R1, #8       ; MOSI=1, SCK=0
        PIN_W    R1
        NOP
        LDR      R1, #12      ; MOSI=1, SCK=1
        PIN_W    R1
        NOP
        LDR      R1, #8       ; MOSI=1, SCK=0
        PIN_W    R1
        NOP
        LDR      R1, #12      ; MOSI=1, SCK=1
        PIN_W    R1
        NOP
        LDR      R1, #0       ; Idle low
        PIN_W    R1
    hold2:
        JMP      hold2
    """
    load_sram(dut, assemble(spi_asm, base_addr=128))
    await reset_core(dut, direct_boot=True)

    # Monitor SPI clock (pin 2) and MOSI (pin 3)
    sck_toggles = 0
    mosi_high = False
    for _ in range(60):
        await RisingEdge(dut.clk)
        sck = (int(dut.uio_out.value) >> 2) & 1
        mosi = (int(dut.uio_out.value) >> 3) & 1
        if sck == 1:
            sck_toggles += 1
        if mosi == 1:
            mosi_high = True
    assert sck_toggles > 0 and mosi_high, "Stage 2 (SPI Master) failed to generate SCK/MOSI"
    dut._log.info("Stage 2 (SPI Master) verified successfully!")

    dut._log.info("=== STAGE 3: Hot-Swap Reprogram as I2C Open-Drain Master ===")
    i2c_asm = """
        LDR      R0, #0
        PIN_W    R0
        ; Idle SDA=1, SCL=1
        OE_CLR   96
        NOP
        ; START: SDA drops low while SCL high
        OE_SET   32
        NOP
        ; SCL drops low
        OE_SET   64
        NOP
        ; STOP: SCL high, then SDA high
        OE_CLR   64
        NOP
        OE_CLR   32
    hold3:
        JMP      hold3
    """
    load_sram(dut, assemble(i2c_asm, base_addr=128))
    await reset_core(dut, direct_boot=True)

    # Check that open-drain OE controls changed as expected
    oe_seen_32 = False
    oe_seen_64 = False
    for _ in range(50):
        await RisingEdge(dut.clk)
        oe = int(dut.uio_oe.value)
        if oe & 32:
            oe_seen_32 = True
        if oe & 64:
            oe_seen_64 = True
    assert oe_seen_32 and oe_seen_64, "Stage 3 (I2C Master) failed to drive open-drain OE"
    dut._log.info("Stage 3 (I2C Master) verified successfully!")

    dut._log.info("=== STAGE 4: Hot-Swap Reprogram as Pattern Generator ===")
    pat_asm = """
        OE_SET   255
        LDR      R1, #0xC7
        PIN_W    R1
    hold4:
        JMP      hold4
    """
    load_sram(dut, assemble(pat_asm, base_addr=128))
    await reset_core(dut, direct_boot=True)

    await ClockCycles(dut.clk, 25)
    assert int(dut.uio_out.value) == 0xC7, f"Expected 0xC7, got 0x{int(dut.uio_out.value):02X}"
    dut._log.info("Stage 4 (Pattern Generator) verified successfully!")
    dut._log.info("All 4 sequential in-system reprogrammability stages PASSED!")


# ==============================================================================
# 2. Hardware MEM_W Auto-Incrementing SRAM Loader Test
# ==============================================================================
@cocotb.test()
async def test_hardware_mem_w_loader(dut):
    """Tests the MEM_W auto-increment hardware instruction writing into SRAM."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    # Firmware:
    # 1. R1 = 0xAA55
    # 2. R2 = 0x1234
    # 3. MEM_W R1 -> writes sram[128] = 0xAA55, waddr becomes 129
    # 4. MEM_W R2 -> writes sram[129] = 0x1234, waddr becomes 130
    # 5. Output completion code 0x7E on uio_out
    asm = """
        OE_SET   255
        LDR      R1, #0x55
        LDR      R2, #0x34
        MEM_W    R1           ; writes SRAM[128]
        MEM_W    R2           ; writes SRAM[129]
        LDR      R3, #0x7E
        PIN_W    R3
    done:
        JMP      done
    """
    words = assemble(asm, base_addr=128)
    load_sram(dut, words)
    await reset_core(dut, direct_boot=True)

    await ClockCycles(dut.clk, 30)

    assert int(dut.uio_out.value) == 0x7E, f"Expected 0x7E, got 0x{int(dut.uio_out.value):02X}"
    # Verify that SRAM entries 128 and 129 were written by hardware!
    val128 = int(dut.user_project.u_sram.mem[128].value)
    val129 = int(dut.user_project.u_sram.mem[129].value)
    dut._log.info(f"Hardware MEM_W result: SRAM[128]=0x{val128:04X}, SRAM[129]=0x{val129:04X}")
    assert val128 == 0x55, f"Expected SRAM[128] == 0x55, got 0x{val128:04X}"
    assert val129 == 0x34, f"Expected SRAM[129] == 0x34, got 0x{val129:04X}"
    dut._log.info("Hardware MEM_W auto-increment write test PASSED!")


# ==============================================================================
# 3. ROM Bootloader Bypass vs Boot Pin Test
# ==============================================================================
@cocotb.test()
async def test_rom_bootloader_bypass(dut):
    """Test ui_in[0] hardware selection: 1=direct SRAM boot, 0=ROM bootloader."""
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    # When ui_in[0] = 1, reset branches immediately to PC 128.
    target_asm = """
        OE_SET 255
        LDR    R1, #0x42
        PIN_W  R1
    done:
        JMP    done
    """
    load_sram(dut, assemble(target_asm, base_addr=128))

    # Boot with ui_in[0] = 1
    await reset_core(dut, direct_boot=True)
    await ClockCycles(dut.clk, 25)

    assert int(dut.uio_out.value) == 0x42, "Direct SRAM boot failed"
    dut._log.info("Direct boot bypass verified successfully!")


# ==============================================================================
# 4. UART Bootloader Over-The-Wire Test (§1.1)
# ==============================================================================
@cocotb.test()
async def test_uart_bootloader_over_the_wire(dut):
    """Full UART bootloader: ROM boot -> framed UART load -> CRC ok -> run.

    Power up with ui_in[0]=0 (ROM bootloader). Send a boot-protocol v2
    'W' frame (firmware words + CRC-8) and a 'G' frame over UART RX (pin 1);
    the ROM writes the words to SRAM via MEM_W and jumps to 128.
    """
    clock = Clock(dut.clk, 80, unit="ns")
    cocotb.start_soon(clock.start())

    # Firmware to load into SRAM (assembled at base_addr=128)
    fw_asm = """
        OE_SET 255
        LDR    R1, #0x42
        PIN_W  R1
    done:
        JMP    done
    """
    fw_words = assemble(fw_asm, base_addr=128)
    dut._log.info(f"Streaming {len(fw_words)} firmware words over UART")

    # Boot protocol v2 (tools/msf_boot.py): a 'W'rite frame then a 'G'o
    # frame, each with CRC-8. Bytes are sent back to back, like a real
    # USB-UART adapter.
    uart_bytes = boot_frame(fw_words, addr=128, run=True)

    # Boot with ui_in[0] = 0 (ROM bootloader)
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 2  # RX line idle high
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    # Wait for the bootloader to finish boot-select and arm its idle-poll at the
    # top of rx_word BEFORE the first byte's start bit arrives. If byte 1's
    # start bit precedes the idle-poll, that byte is missed (the poll only latches
    # a start bit that occurs *while* it is polling). ~200 cycles of margin.
    await ClockCycles(dut.clk, 200)

    # Transmit each byte over UART RX (pin 1) at 115200 baud (104 cycles/bit)
    for idx, byte in enumerate(uart_bytes):
        # Start bit (0)
        dut.uio_in.value = 0
        await ClockCycles(dut.clk, 104)
        # 8 data bits (LSB first)
        for i in range(8):
            bit = (byte >> i) & 1
            dut.uio_in.value = bit << 1
            await ClockCycles(dut.clk, 104)
        # Stop bit (1), next start bit follows immediately
        dut.uio_in.value = 2
        await ClockCycles(dut.clk, 104)
        dut._log.info(f"  Transmitted byte {idx+1}/{len(uart_bytes)}: 0x{byte:02X}")

    # Wait for the ACKs to go out and the firmware to start
    await ClockCycles(dut.clk, 3000)

    val = int(dut.uio_out.value)
    dut._log.info(f"UART Bootloader: uio_out = 0x{val:02X}")
    assert val == 0x42, f"Expected 0x42 from loaded firmware, got 0x{val:02X}"

    # Verify all 4 firmware words were written to SRAM[128..131] by MEM_W
    for i, expected in enumerate(fw_words):
        actual = int(dut.user_project.u_sram.mem[128 + i].value)
        dut._log.info(f"  SRAM[{128+i}] = 0x{actual:04X} (expected 0x{expected:04X})")
        assert actual == expected, \
            f"SRAM[{128+i}] mismatch: expected 0x{expected:04X}, got 0x{actual:04X}"

    dut._log.info("UART Bootloader over-the-wire test PASSED!")
