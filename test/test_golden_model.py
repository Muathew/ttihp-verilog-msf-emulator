# SPDX-License-Identifier: Apache-2.0
"""
Golden reference test suite for the msfcore protocol emulator.

1. test_golden_lockstep -- runs a firmware image on the RTL and on the
   independent ISA model (isa_model.py) and diffs every issue/release event
   and the full architectural state (diffcheck.py). If the RTL ever drifts
   from the ISA spec, this fails on the first divergent cycle.

2. test_golden_uart_reference / test_golden_i2c_conditions / test_golden_crcs
   -- pure-Python checks of the protocol golden references (golden_model.py).
"""

import cocotb
from cocotb.clock import Clock

from diffcheck import run_and_compare
from golden_model import (
    UartRxDecoder,
    I2cConditionDetector,
    uart_frame_bits,
    uart_decode_data_bits,
    usb_crc16,
    usb_crc5,
    eth_fcs,
    crc8_07,
)


# ---------------------------------------------------------------------------
# Firmware: exercises the datapath, flags, and a taken branch.
# ---------------------------------------------------------------------------
_FW = """
    OE_SET  255
    LDR     R1, #5
    LDR     R2, #3
    ADDS    R3, R1, R2      ; R3 = 8
    AND     R6, R1, R2      ; R6 = 1
    OR      R6, R6, R3      ; R6 = 9
    XOR     R6, R6, R3      ; R6 = 1
    SHL     R6, R6, #2      ; R6 = 4
    SHR     R6, R6, #1      ; R6 = 2
    INCR    R6             ; R6 = 3
    DECR    R6             ; R6 = 2
    SUBS    R5, R1, R1      ; R5 = 0, Z = 1
    PIN_W   R3             ; out = 8
    JZ      ok            ; taken (Z = 1)
    LDR     R7, #99
    PIN_W   R7
fail_loop:
    JMP     fail_loop
ok:
    SUBS    R4, R1, R2      ; R4 = 2, Z = 0
    PIN_W   R4            ; out = 2
done_loop:
    JMP     done_loop
"""


@cocotb.test()
async def test_golden_lockstep(dut):
    """Differential check: DUT vs the ISA model, every event, 300 cycles."""
    cocotb.start_soon(Clock(dut.clk, 80, unit="ns").start())
    dut.ena.value = 1
    n, _, _ = await run_and_compare(dut, _FW, 300, tag="golden lockstep")
    dut._log.info(f"Golden lock-step PASSED ({n} instructions, all state matched)")


@cocotb.test()
async def test_golden_uart_reference(dut):
    """The UART reference model round-trips a byte (TX frame -> RX decode)."""
    # 115200, 8N1: expected TX pin levels for 0xA5.
    expected = [0, 1, 0, 1, 0, 0, 1, 0, 1, 1]  # start, b0..b7 LSB, stop
    assert uart_frame_bits(0xA5) == expected, "UART TX frame reference is wrong"

    # Decode the same frame back into a byte.
    frame = uart_frame_bits(0x3D)
    dec = UartRxDecoder()
    rx = dec.feed(frame[0], frame[1:9], frame[9])
    assert rx == 0x3D, f"UART RX decode expected 0x3D, got {rx:#04x}"
    assert uart_decode_data_bits(frame[1:9]) == 0x3D
    assert dec.errors == 0
    dut._log.info("Golden UART reference round-trip PASSED (0x3D)")


@cocotb.test()
async def test_golden_i2c_conditions(dut):
    """The I2C condition detector classifies START / data / STOP correctly."""
    det = I2cConditionDetector()
    # Idle (both high).
    assert det.update(1, 1) is None
    # START: SDA falls while SCL held high.
    assert det.update(0, 1) == "START"
    # Data bit 0: SDA held low, SCL pulses (low -> high) -> sample 0.
    det.update(0, 0)                       # SCL low, SDA=0
    assert det.update(0, 1) == "bit0"       # SCL rising, SDA stable 0
    # STOP: with SDA low and SCL high, releasing SDA (0->1) is a STOP.
    assert det.update(1, 1) == "STOP"
    dut._log.info(f"Golden I2C condition detector PASSED: {det.events}")


@cocotb.test()
async def test_golden_crcs(dut):
    """CRC references match the published check values / known packets."""
    assert eth_fcs(b"123456789") == 0xCBF43926
    assert crc8_07(b"123456789") == 0xF4
    assert usb_crc16(b"123456789") == 0xB4C8
    # SETUP to address 0 / endpoint 0 is sent as 2D 00 10: CRC5 = 0x10 >> 3
    assert usb_crc5(0) == 0x02
