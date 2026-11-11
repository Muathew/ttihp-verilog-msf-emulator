# SPDX-License-Identifier: Apache-2.0
"""v4 step 4a/4b: two engine sets and engines on all 24 pins (docs/isa.md section 3.2).

A thread's ESEL bit 0 names the TX / RX / CRC set its instructions and STAT bits 0-4 address.
Expected values are worked out from the spec (CRCs from a bit-serial reference written
here, UART frames from msf_tb's helpers); the RTL is observed at the pins and in data RAM."""
import cocotb
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge, Timer

from msf_tb import boot_asm, reg, sram, tx_cfg, rx_cfg, uart_decode, NRZ

DONE = 0x55


def crc_ref(data, poly, init):
    """The unit's reflected CRC (isa.md 6.3), bit-serial, on a 32-bit register."""
    crc = init
    for byte in data:
        for i in range(8):
            fb = (crc & 1) ^ ((byte >> i) & 1)
            crc = (crc >> 1) ^ (poly if fb else 0)
    return crc & 0xFFFFFFFF


MSG = b"123456789"


@cocotb.test()
async def test_two_crc_units_run_side_by_side(dut):
    """T0 (set 0) computes CRC-32 and T1 (set 1) a CRC-16 over the same bytes at the same time,
    each with its own poly, init and busy flag; neither disturbs the other."""
    fw = f"""
        LDI    R1, t1
        TSTART R1
        LDI R1, {0xEDB88320 & 0xFFFF}
        CRC_POLL R1
        LDI R1, {0xEDB88320 >> 16}
        CRC_POLH R1
        LDI R1, 0xFFFF
        CRC_SETL R1
        CRC_SETH R1
        LDR R1, #0x17            ; 8 bits a write, reflected
        CRC_CFG R1
        LDR R2, #0
        LDR R6, #{len(MSG)}
    l0: LD R3, R2
        CRC_W R3
        INCR R2
        DECR R6
        JNZ l0
        CRC_RDL R4
        CRC_RDH R5
        LDR R2, #64
        ST R2, R4
        INCR R2
        ST R2, R5
    h0: JMP h0
    t1: ESEL #1                  ; engine set 1, port page 0
        LDI R1, 0xA001
        CRC_POLL R1
        LDR R1, #0
        CRC_POLH R1
        LDI R1, 0xFFFF
        CRC_SETL R1
        LDR R1, #0
        CRC_SETH R1
        LDR R1, #0x17
        CRC_CFG R1
        LDR R2, #0
        LDR R6, #{len(MSG)}
    l1: LD R3, R2
        CRC_W R3
        INCR R2
        DECR R6
        JNZ l1
        CRC_RDL R4
        LDR R2, #67
        ST R2, R4
    h1: JMP h1
    """
    clear = {a: 0 for a in range(64, 72)}                   # the SRAM keeps what earlier tests left
    await boot_asm(dut, fw, extra={**clear, **{i: b for i, b in enumerate(MSG)}})
    await ClockCycles(dut.clk, 1500)
    crc32 = sram(dut, 64) | sram(dut, 65) << 16
    assert crc32 == crc_ref(MSG, 0xEDB88320, 0xFFFFFFFF), f"set 0: {crc32:#010x}"
    assert crc32 ^ 0xFFFFFFFF == 0xCBF43926                           # the published check value
    assert sram(dut, 67) == crc_ref(MSG, 0xA001, 0xFFFF) == 0x4B37, f"set 1: {sram(dut, 67):#06x}"


@cocotb.test()
async def test_two_uarts_at_once_from_two_threads(dut):
    """Two threads, one TX engine each: 'HELLO' at 13 cycles a bit on uio0 and 'world' at 20 on
    uio2, concurrently, decoded from the pins. (With one engine the second thread would have
    waited for the first: docs/plan.md 7.13.)"""
    a, b = b"HELLO", b"world"
    words_a = [(c << 1) | 0x200 for c in a]
    words_b = [(c << 1) | 0x200 for c in b]
    fw = f"""
        LDI    R1, t1
        TSTART R1
        LDI    R1, {tx_cfg(pin=0, mode=NRZ, width=10, lsb=True, idle=1)}
        TX_CFG R1
        LDR    R1, #13
        TX_DIV R1
        OE_SET 0x05
        LDR R2, #0
        LDR R6, #{len(a)}
    l0: LD R3, R2
        TX_PUSH R3
        INCR R2
        DECR R6
        JNZ l0
        TX_WAIT
    h0: JMP h0
    t1: ESEL #1
        LDI    R1, {tx_cfg(pin=2, mode=NRZ, width=10, lsb=True, idle=1)}
        TX_CFG R1
        LDR    R1, #20
        TX_DIV R1
        LDR R2, #32
        LDR R6, #{len(b)}
    l1: LD R3, R2
        TX_PUSH R3
        INCR R2
        DECR R6
        JNZ l1
        TX_WAIT
    h1: JMP h1
    """
    extra = {i: w for i, w in enumerate(words_a)}
    extra.update({32 + i: w for i, w in enumerate(words_b)})
    await boot_asm(dut, fw, extra=extra)
    line = []
    for _ in range(1600):
        await RisingEdge(dut.clk)
        await ReadOnly()
        line.append(int(dut.uio_out.value))
    got_a = bytes(x & 0xFF for x in uart_decode(line, 13, pin=0) if x >= 0)
    got_b = bytes(x & 0xFF for x in uart_decode(line, 20, pin=2) if x >= 0)
    assert got_a == a, f"set 0 sent {got_a!r}"
    assert got_b == b, f"set 1 sent {got_b!r}"


@cocotb.test()
async def test_tx_on_uo_received_on_ui_by_the_other_set(dut):
    """Engines on all 24 pins: set 0's TX engine drives uo[3] (TX_CFG bit 15), the testbench
    wires uo[3] to ui[5], and set 1's RX engine reads ui[5] (RX_CFG bit 3): a UART byte stream
    goes out on an output-only pin and comes in on an input-only pin, both engines at once."""
    data = [0x55, 0xA3, 0x00, 0xFF, 0x3C]
    words = [(c << 1) | 0x200 for c in data]
    fw = f"""
        LDI    R1, t1
        TSTART R1
        LDI    R1, {tx_cfg(pin=3, mode=NRZ, width=10, lsb=True, idle=1) | 0x8000}
        TX_CFG R1                    ; the engine owns uo[3], idle high
        LDR    R1, #13
        TX_DIV R1
        LDR    R0, #60
    w0: DECR   R0                    ; let the line reach the receiver
        JNZ    w0
        LDR R2, #0
        LDR R6, #{len(data)}
    l0: LD R3, R2
        TX_PUSH R3
        INCR R2
        DECR R6
        JNZ l0
    h0: JMP h0
    t1: ESEL #1
        LDR    R1, #13
        RX_DIV R1
        LDI    R1, {rx_cfg(pin=5, mode=NRZ, width=8, lsb=True, char=True) | 0x8}
        RX_CFG R1                    ; ui[5]
        LDR R2, #64
        LDR R6, #{len(data)}
    r1: RX_POP R3
        ST R2, R3
        INCR R2
        DECR R6
        JNZ r1
        UO_WI {DONE}
    h1: JMP h1
    """
    clear = {a: 0 for a in range(64, 72)}                   # the SRAM keeps what earlier tests left
    await boot_asm(dut, fw, extra={**clear, **{i: w for i, w in enumerate(words)}})

    async def wire():
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            v = (int(dut.uo_out.value) >> 3) & 1
            await Timer(1, unit="ns")
            dut.ui_in.value = 1 | (v << 5)             # ui[0] = 1 stays: the strap bits are not touched

    w = cocotb.start_soon(wire())
    for _ in range(4000):
        await RisingEdge(dut.clk)
        if int(dut.uo_out.value) & 0xF7 == DONE & 0xF7:      # set 1's thread has all five (uo[3] is the TX line)
            break
    else:
        raise AssertionError("the receiver never finished")
    await ClockCycles(dut.clk, 4)
    w.cancel()
    got = [sram(dut, 64 + i) for i in range(len(data))]
    assert got == data, f"received {[hex(x) for x in got]}"


@cocotb.test()
async def test_set1_wins_a_shared_pin_and_status_follows_the_thread(dut):
    """Both sets enabled on uio4 (set 0 idle low, set 1 idle high): the pad is high, set 1's.
    Set 1 off: the pad shows set 0's low. STAT shows the thread's own set: with only set 1 busy,
    a thread on set 0 reads tx_busy = 0 and the same thread after ESEL #1 reads 1; TX_WAIT on
    the idle set returns at once while the other set is busy."""
    fw = f"""
        OE_SET 0x10
        LDI    R1, {tx_cfg(pin=4, mode=NRZ, width=8, idle=0)}
        TX_CFG R1                    ; set 0: idle low
        LDI    R1, {tx_cfg(pin=4, mode=NRZ, width=8, idle=1) | 0}
        ESEL   #1
        TX_CFG R1                    ; set 1: idle high
        LDR    R1, #200
        TX_DIV R1                    ; slow symbols
        LDR    R1, #0xA5
        TX_PUSH R1                   ; set 1 is busy for 1600 cycles
        LDR    R0, #30
    d1: DECR   R0
        JNZ    d1
        UO_WI  1                     ; marker: the pad is both sets' (set 1 wins)
        ESEL   #0
        STAT   R3                    ; set 0: idle
        ESEL   #1
        STAT   R4                    ; set 1: busy
        ESEL   #0
        CNT_R  R5
        TX_WAIT                      ; set 0 is idle: returns at once
        CNT_R  R6
        ESEL   #1
        LDR    R1, #0
        TX_CFG R1                    ; set 1 off: the pin falls back to set 0's level
        UO_WI  2
    h:  JMP    h
    """
    await boot_asm(dut, fw)
    seen = {}
    for _ in range(600):
        await RisingEdge(dut.clk)
        await ReadOnly()
        u = int(dut.uo_out.value)
        if u in (1, 2) and u not in seen:
            seen[u] = int(dut.uio_out.value)
    await ClockCycles(dut.clk, 6)
    assert (seen.get(1, 0) >> 4) & 1 == 1, f"both sets on uio4: pad {seen.get(1, 0):#04x}"
    assert (int(dut.uio_out.value) >> 4) & 1 == 0, "set 1 off: the pad did not fall back to set 0"
    assert reg(dut, 3) & 2 == 0, f"STAT on set 0: {reg(dut, 3):#06x}"
    assert reg(dut, 4) & 2 == 2, f"STAT on set 1: {reg(dut, 4):#06x}"
    assert reg(dut, 6) - reg(dut, 5) <= 4, "TX_WAIT on the idle set waited for the busy one"
