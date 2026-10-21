# SPDX-License-Identifier: Apache-2.0
"""Directed tests for the ISA v2.0 additions (docs/isa.md).

Each test checks values derived by hand from the spec, independently of the
reference model (test_random_diff.py covers the cross-check at scale).
"""
import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, ReadOnly

from assembler import assemble
from msf_tb import boot_asm, reg, ctrl, sram, load_words


# The boot ROM leaves R1-R3 = 1; tests that expect untouched registers clear them.
CLEAR = "\n".join(f"LDR R{i}, #0" for i in range(8)) + "\n"


async def run(dut, asm, cycles=60, **kw):
    await boot_asm(dut, CLEAR + asm + "\nhalt: JMP halt\n", **kw)
    await ClockCycles(dut.clk, cycles)


@cocotb.test()
async def test_ldi_ldh_swap(dut):
    await run(dut, """
        LDI  R1, 0xBEEF
        LDR  R2, #0x34
        LDH  R2, #0x12
        SWAP R3, R2
    """)
    assert (reg(dut, 1), reg(dut, 2), reg(dut, 3)) == (0xBEEF, 0x1234, 0x3412)


@cocotb.test()
async def test_pin_mask_ops(dut):
    """SETM/CLRM selectors on OUT, OE and UO."""
    await run(dut, """
        PIN_WI  0xF0
        PIN_SET 0x01      ; F1
        PIN_CLR 0x80      ; 71
        PIN_TGL 0x0F      ; 7E
        OE_WI   0x0F
        OE_SET  0x30      ; 3F
        OE_CLR  0x01      ; 3E
        OE_TGL  0xFF      ; C1
        UO_WI   0xA0
        UO_SET  0x05      ; A5
        UO_CLR  0x80      ; 25
        UO_TGL  0x11      ; 34
    """)
    assert int(dut.uio_out.value) == 0x7E
    assert int(dut.uio_oe.value) == 0xC1
    assert int(dut.uo_out.value) == 0x34


@cocotb.test()
async def test_two_banks(dut):
    """1024 words (isa.md §1): data in bank 1, CALL / RET between banks,
    straight-line code and a backward branch across the 511/512 boundary,
    the MEM_W pointer wrapping from 1023 to 0, and the same costs as in
    bank 0 (a taken JMP into the other bank is 2 cycles)."""
    await boot_asm(dut, CLEAR + """
        LDI  R1, 965              ; bank 1 data
        LDI  R2, 0xBEEF
        ST   R1, R2
        LD   R3, R1
        CALL far                  ; bank 1 code, returns to bank 0
        LDI  R1, 1023
        MEM_A R1
        LDR  R2, #0x5A
        MEM_W R2                  ; [1023]
        MEM_W R2                  ; [0]: the pointer wraps
        CNT_R R6
        JMP  edge                 ; into bank 0 just below the boundary
    back: CNT_R R7
        SUBS R7, R7, R6           ; CNT_R, JMP (2), ... measured below
    halt: JMP halt
    .org 508
    edge: LDR  R5, #0
        LDR  R1, #3
    lp: INCR R5                   ; 510
        INCR R5                   ; 511
        INCR R5                   ; 512: bank 1
        DECR R1
        JNZ  lp                   ; back across the boundary
        JMP  back
    .org 900
    far: LDR  R4, #0x44
        RET
    """)
    await ClockCycles(dut.clk, 120)
    assert (reg(dut, 3), reg(dut, 4), reg(dut, 5)) == (0xBEEF, 0x44, 9)
    assert sram(dut, 965) == 0xBEEF
    assert (sram(dut, 1023), sram(dut, 0)) == (0x5A, 0x5A)
    # CNT_R R6 (c), JMP (c+1, 2 cycles), edge: 2 LDR, 3 x (3 INCR, DECR,
    # taken JNZ 2 / last not taken 1), JMP back (2), CNT_R R7
    assert reg(dut, 7) == 1 + 2 + 2 + 3 * 6 - 1 + 2


@cocotb.test()
async def test_fifo_costs(dut):
    """FIFO_PUT costs 1 cycle like ST, FIFO_GET 2 like LD (isa.md §6.5)."""
    await run(dut, """
        LDI  R1, 32 | 2 << 10     ; FIFO in SRAM 32..39, 8 words
        FIFO_CFG R1
        LDR  R2, #0x11
        CNT_R R5
        FIFO_PUT R2
        CNT_R R6
        FIFO_GET R3
        CNT_R R4
        SUBS R6, R6, R5
        SUBS R4, R4, R5
    """)
    assert (reg(dut, 6), reg(dut, 4), reg(dut, 3)) == (2, 5, 0x11)
    assert sram(dut, 32) == 0x11


@cocotb.test()
async def test_fifo_full_empty_drops(dut):
    """Size-aligned block, Z on a full push and an empty pop, the drop count,
    STAT bits 7 (not empty) and 8 (full), FIFO_STAT = drops << 9 | count
    (reading it changes nothing; FIFO_CFG clears it)."""
    await run(dut, """
        LDI  R1, 37 | 2 << 10     ; 8 words: block 32..39 (37's low bits ignored)
        FIFO_CFG R1
        LDR  R3, #0x77
        FIFO_GET R3               ; empty: Z = 1, R3 kept
        LDR  R4, #0
        JNZ  e1
        LDR  R4, #1
    e1: ST   R0, R4               ; [0] = empty-pop Z
        LDR  R2, #0xA0
        LDR  R6, #8
    f:  FIFO_PUT R2
        INCR R2
        DECR R6
        JNZ  f
        STAT R5                   ; full and not empty
        FIFO_PUT R2               ; 9th word: dropped, Z = 1
        LDR  R4, #0
        JNZ  e2
        LDR  R4, #1
    e2: FIFO_STAT R6              ; 1 drop, 8 words
        FIFO_STAT R1              ; the same: reads have no side effect
        LDR  R7, #8
    p:  FIFO_GET R2
        ST   R7, R2               ; [8..15] = the words in order
        INCR R7
        LDR  R2, #16
        SUBS R2, R7, R2
        JNZ  p
        FIFO_PUT R7               ; ends up in slot 0 again: 32
    """, cycles=200)
    assert sram(dut, 0) == 1
    assert reg(dut, 3) == 0x77
    assert reg(dut, 5) & 0x180 == 0x180
    assert (reg(dut, 4), reg(dut, 6), reg(dut, 1)) == (1, 1 << 9 | 8, 1 << 9 | 8)
    assert [sram(dut, 8 + i) for i in range(8)] == list(range(0xA0, 0xA8))
    assert sram(dut, 32) == 16


@cocotb.test()
async def test_out_w(dut):
    await run(dut, "LDR R1, #0x5A\nOUT_W R1")
    assert int(dut.uo_out.value) == 0x5A


@cocotb.test()
async def test_ld_st_data_ram(dut):
    """ST/LD on SRAM 0..127 (data RAM shadowed by the ROM for fetch)."""
    await run(dut, """
        LDI  R1, 0xCAFE
        LDR  R2, #5
        ST   R2, R1        ; mem[5] = CAFE
        LD   R3, R2        ; R3 = CAFE
        LDR  R4, #127
        LD   R5, R4        ; preloaded value
        INCR R5
        ST   R4, R5
    """, extra={127: 0x1233})
    assert reg(dut, 3) == 0xCAFE and sram(dut, 5) == 0xCAFE
    assert reg(dut, 5) == 0x1234 and sram(dut, 127) == 0x1234


@cocotb.test()
async def test_ld_costs_two_cycles(dut):
    """LD = 2 cycles in single-thread mode (isa.md §5)."""
    await run(dut, """
        LDR   R2, #3
        CNT_R R1
        LD    R3, R2
        CNT_R R4
        SUBS  R5, R4, R1
    """)
    assert reg(dut, 5) == 3          # CNT_R(1) + LD(2)


@cocotb.test()
async def test_mem_a_and_wrap(dut):
    """MEM_A sets the MEM_W pointer; it runs on from 511 into bank 1 and
    wraps 1023 -> 0 (10 bits)."""
    await run(dut, """
        LDI   R1, 511
        MEM_A R1
        LDR   R2, #0x11
        MEM_W R2          ; 511
        LDR   R2, #0x22
        MEM_W R2          ; 512
        LDI   R1, 1023
        MEM_A R1
        LDR   R2, #0x33
        MEM_W R2          ; 1023
        LDR   R2, #0x44
        MEM_W R2          ; 0
    """)
    assert [sram(dut, a) for a in (511, 512, 1023, 0)] == [0x11, 0x22, 0x33, 0x44]


@cocotb.test()
async def test_call_jr_ret(dut):
    await run(dut, """
        CALL sub          ; R7 = 137 (8 clearing LDRs first)
        LDI  R2, tgt
        JR   R2
        LDR  R3, #0xEE    ; skipped
    tgt: LDR R4, #0x44
        JMP  halt
    sub: LDR R1, #0x11
        RET
    """)
    assert (reg(dut, 1), reg(dut, 3), reg(dut, 4), reg(dut, 7)) == (0x11, 0, 0x44, 137)


@cocotb.test()
async def test_sr_load_read_shift_config(dut):
    """SR_LD / SR_RD / SHIFT_OUT on a configured pin, MSB- and LSB-first."""
    await run(dut, """
        LDI       R1, 0x8001
        SR_LD     R1
        LDR       R2, #2          ; out pin 2, in pin 0, MSB-first
        SER_CFG   R2
        LDR       R3, #0
        SHIFT_OUT R3              ; OUT[2] <- 1 ; SR = 0x0002
        SR_RD     R4
        PIN_R     R0
        LDR       R2, #0x42       ; out pin 2, in pin 0, LSB-first (bit 6)
        SER_CFG   R2
        SHIFT_OUT R3              ; OUT[2] <- SR[0]=0 ; SR = 0x0001
        SR_RD     R5
    """)
    assert reg(dut, 4) == 0x0002
    assert reg(dut, 5) == 0x0001
    assert (int(dut.uio_out.value) >> 2) & 1 == 0


@cocotb.test()
async def test_xfer_full_duplex(dut):
    """XFER shifts the input pin in while shifting the MSB out."""
    await boot_asm(dut, """
        LDR     R2, #0x18         ; out pin 0, in pin 3 (0x18 = 3<<3)
        SER_CFG R2
        LDR     R1, #0xA5
        SWAP    R1, R1            ; R1 = 0xA500 -> MSB-first byte at the top
        SR_LD   R1
        LDR     R4, #8
    lp: XFER
        NOP
        NOP
        DECR    R4
        JNZ     lp
        SR_RD   R5
    halt: JMP halt
    """, uio_in=0x08)             # MISO (pin 3) held high
    await ClockCycles(dut.clk, 120)
    assert reg(dut, 5) == 0x00FF, f"SR = {reg(dut, 5):#06x}"


@cocotb.test()
async def test_wait_edge_conditions(dut):
    """rising / falling / level conditions and the ui bank."""
    await boot_asm(dut, CLEAR + """
        WAIT_EDGE 4, rise         ; uio[4] 0->1
        LDR R1, #1
        WAIT_EDGE 4, rise         ; needs a NEW rising edge (falling must not count)
        LDR R2, #2
        WAIT_EDGE 6, high, ui     ; ui[6] level
        LDR R3, #3
        WAIT_EDGE 6, fall, ui
        LDR R4, #4
    halt: JMP halt
    """)
    await ClockCycles(dut.clk, 20)
    dut.uio_in.value = 0x10
    await ClockCycles(dut.clk, 10)
    assert reg(dut, 1) == 1 and reg(dut, 2) == 0
    dut.uio_in.value = 0x00               # falling: must not release
    await ClockCycles(dut.clk, 10)
    assert reg(dut, 2) == 0
    dut.uio_in.value = 0x10
    await ClockCycles(dut.clk, 10)
    assert reg(dut, 2) == 2 and reg(dut, 3) == 0
    dut.ui_in.value = 0x41                # level high on ui[6] (keep bit0)
    await ClockCycles(dut.clk, 10)
    assert reg(dut, 3) == 3 and reg(dut, 4) == 0
    dut.ui_in.value = 0x01
    await ClockCycles(dut.clk, 10)
    assert reg(dut, 4) == 4


@cocotb.test()
async def test_tmo_zero_disables_timeout(dut):
    await run(dut, """
        LDR       R1, #0
        TMO       R1
        WAIT_EDGE 5
        LDR       R2, #0x77
    """, cycles=200)
    assert int(ctrl(dut).stall.value) == 1 and reg(dut, 2) == 0


@cocotb.test()
async def test_wait_cmp_exact_timing(dut):
    """Next instruction after WAIT_CMP T issues when CNT == T+1 (isa.md §5)."""
    await run(dut, """
        CNT_R    R1
        LDR      R2, #40
        ADDS     R1, R1, R2
        WAIT_CMP R1
        CNT_R    R3
        SUBS     R4, R3, R1
    """, cycles=100)
    assert reg(dut, 4) == 1


@cocotb.test()
async def test_input_sync_latency(dut):
    """A pad change is visible to instructions two cycles later."""
    await boot_asm(dut, """
        WAIT_EDGE 0           ; uio[0] any edge
        CNT_R R1
    halt: JMP halt
    """)
    await ClockCycles(dut.clk, 20)
    # read CNT in the cycle the pad changes
    await RisingEdge(dut.clk)
    dut.uio_in.value = 1
    await ReadOnly()
    t_pad = int(ctrl(dut).cnt.value)
    await ClockCycles(dut.clk, 20)
    # pad seen at t_pad+2 (sync) -> release cycle t_pad+2 -> CNT_R at t_pad+3
    assert reg(dut, 1) == t_pad + 3, f"CNT_R={reg(dut, 1)} pad change at {t_pad}"


@cocotb.test()
async def test_stat_and_tid(dut):
    await run(dut, "STAT R1\nTID R2")
    assert reg(dut, 1) == 0 and reg(dut, 2) == 0


@cocotb.test()
async def test_reserved_ext_is_nop(dut):
    """EXT sub-ops 30/31 and SETM/CLRM selectors 6/7 do nothing."""
    await boot_asm(dut, """
        LDR R1, #9
        .word 0x7800 | (1 << 8) | 30
        .word 0x7800 | (1 << 8) | 31
        .word 0x9000 | (6 << 8) | 0xFF
        .word 0x9800 | (7 << 8) | 0xFF
        LDR R2, #8
    halt: JMP halt
    """)
    await ClockCycles(dut.clk, 40)
    assert (reg(dut, 1), reg(dut, 2)) == (9, 8)
    assert int(dut.uio_out.value) == 0 and int(dut.uio_oe.value) == 0 and int(dut.uo_out.value) == 0
