# SPDX-License-Identifier: Apache-2.0
"""Boot ROM v2 tests: framed UART and parallel loading (tools/msf_boot.py frames).

The chip's replies are decoded from its UART TX pin (uio[0]) by a monitor
coroutine, independently of the ROM code.
"""
import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, ReadOnly

from assembler import assemble
from msf_tb import start_clock, reset, uart_send, uart_decode, sram
from tools_path import boot_frame, frame, CMD_WRITE, CMD_GO, ACK, NAK

FW = """
    OE_SET 0xFE
    LDR    R1, #0x42
    PIN_W  R1
done:
    JMP    done
"""


class TxMonitor:
    """Records uio_out every cycle until the loader hands the pins to the
    firmware (uo_out bit3); .bytes() decodes UART on uio[0]."""

    def __init__(self, dut):
        self.dut, self.samples = dut, []
        cocotb.start_soon(self._run())

    async def _run(self):
        while True:
            await RisingEdge(self.dut.clk)
            await ReadOnly()
            if int(self.dut.uo_out.value) & 0x08:
                return
            self.samples.append(int(self.dut.uio_out.value))

    def bytes(self, bit=104):
        return uart_decode(self.samples, bit, pin=0)


async def uart_boot(dut, stream, pre_idle=300, gap=0, after=4000):
    start_clock(dut)
    await reset(dut, ui_in=0, uio_in=2)
    mon = TxMonitor(dut)
    await ClockCycles(dut.clk, pre_idle)
    for b in stream:
        await uart_send(dut, b, gap=gap)
    await ClockCycles(dut.clk, after)
    return mon


@cocotb.test()
async def test_uart_load_and_run(dut):
    words = assemble(FW, base_addr=128)
    mon = await uart_boot(dut, boot_frame(words))
    assert [sram(dut, 128 + i) for i in range(len(words))] == words
    assert int(dut.uio_out.value) & 0xFE == 0x42
    assert mon.bytes() == [ACK, ACK], mon.bytes()
    assert int(dut.uo_out.value) & 0x0F == 0x0A, "status: OK + starting firmware"


@cocotb.test()
async def test_uart_bad_crc_nak_then_retry(dut):
    words = assemble(FW, base_addr=128)
    bad = frame(CMD_WRITE, 128, words)
    bad[8] ^= 0x10                                  # corrupt a payload byte
    mon = await uart_boot(dut, [0x00, 0x13] + bad + boot_frame(words))   # junk first
    assert mon.bytes() == [NAK, ACK, ACK], mon.bytes()
    assert [sram(dut, 128 + i) for i in range(len(words))] == words
    assert int(dut.uio_out.value) & 0xFE == 0x42


@cocotb.test()
async def test_uart_data_ram_and_go_anywhere(dut):
    """Load data RAM (0..127) and code at 300, then 'G' to 300."""
    table = [0x1234, 0xBEEF, 0x0F0F]
    fw = """
        LDR   R2, #1
        LD    R3, R2         ; 0xBEEF from data RAM
        OUT_W R3
    h:  JMP h
    """
    words = assemble(fw, base_addr=300)
    stream = frame(CMD_WRITE, 0, table) + frame(CMD_WRITE, 300, words) + frame(CMD_GO, 300)
    mon = await uart_boot(dut, stream)
    assert [sram(dut, i) for i in range(3)] == table
    assert int(dut.uo_out.value) == 0xEF
    assert mon.bytes() == [ACK, ACK, ACK]


@cocotb.test()
async def test_uart_word_0000_loads(dut):
    """LDR R0,#0 encodes as 0x0000 -- v1 used that as the terminator (bug B7)."""
    fw = """
        LDR   R0, #0
        LDR   R0, #0
        UO_WI 0x77
    h:  JMP h
    """
    words = assemble(fw, base_addr=128)
    assert words[0] == 0x0000
    await uart_boot(dut, boot_frame(words))
    assert int(dut.uo_out.value) == 0x77


async def par_send(dut, b, level):
    """Parallel byte k: data on ui_in, then strobe uio[1] = k mod 2; the chip
    copies the level to uo_out[7] once it has taken the byte."""
    dut.ui_in.value = b
    await ClockCycles(dut.clk, 1)
    dut.uio_in.value = level << 1
    for _ in range(400):
        await RisingEdge(dut.clk)
        if (int(dut.uo_out.value) >> 7) & 1 == level:
            return
    raise AssertionError("no parallel ack")


@cocotb.test()
async def test_parallel_load_and_run(dut):
    words = assemble(FW, base_addr=128)
    start_clock(dut)
    await reset(dut, ui_in=0b10, uio_in=0)        # ui_in[0]=0 loader, ui_in[1]=1 parallel
    await ClockCycles(dut.clk, 50)
    for k, b in enumerate(boot_frame(words)):
        await par_send(dut, b, (k + 1) & 1)      # first byte: strobe high
    await ClockCycles(dut.clk, 3000)
    assert [sram(dut, 128 + i) for i in range(len(words))] == words
    assert int(dut.uio_out.value) & 0xFE == 0x42
    assert int(dut.uo_out.value) & 0x0A == 0x0A


@cocotb.test()
async def test_direct_boot_timing_unchanged(dut):
    """ui_in[0]=1: first SRAM instruction issues at cycle 8, as in v1."""
    start_clock(dut)
    words = assemble("CNT_R R1\nh: JMP h", base_addr=128)
    for i, w in enumerate(words):
        dut.user_project.u_sram.mem[128 + i].value = w
    await reset(dut, ui_in=1)
    await ClockCycles(dut.clk, 30)
    assert int(dut.user_project.u_ctrl.u_rf.rf[1].value) == 8


@cocotb.test()
async def test_autobaud_loads_at_any_clock(dut):
    """v4 (plan-v4 B6): with ui_in[3] high in UART mode the host sends 0xFF first; the ROM times
    its start bit and sets both dividers, so the loader works at 33, 61 and 160 cycles a bit, and
    its ACKs come back at that rate. Without the strap the old 104 is kept (every test above)."""
    words = assemble(FW, base_addr=128)
    for bit in (61, 160, 33):
        start_clock(dut)
        await reset(dut, ui_in=0b1000, uio_in=2)       # loader, UART, autobaud strap
        mon = TxMonitor(dut)
        await ClockCycles(dut.clk, 300)
        await uart_send(dut, 0xFF, bit_cycles=bit)      # the calibration byte
        for b in boot_frame(words):
            await uart_send(dut, b, bit_cycles=bit)
        await ClockCycles(dut.clk, 60 * bit)
        assert [sram(dut, 128 + i) for i in range(len(words))] == words, f"{bit} cycles a bit"
        assert int(dut.uio_out.value) & 0xFE == 0x42
        assert mon.bytes(bit) == [ACK, ACK], f"{bit} cycles a bit: {mon.bytes(bit)}"
