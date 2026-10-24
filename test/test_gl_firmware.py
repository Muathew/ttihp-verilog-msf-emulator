# SPDX-License-Identifier: Apache-2.0
"""Firmware on the gate-level netlist, through the pins only.

The hardened netlist is flat, so these tests cannot preload memory or read
registers: each loads its program the way silicon is loaded, through the
boot ROM's parallel loader on ui_in, and checks what comes out of the pins.
Run with `make GATES=yes` (the GDS action's gate-level job), with the
CMOS5L cell models and the SRAM behavioural model. The RTL suite runs the
same programs in more depth; these show the netlist runs them too, with the
same cycle timing.
"""
import os
import random

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge

from assembler import assemble_file
from fw_tb import Bench, Recorder
from msf_tb import start_clock, reset, uart_send, uart_decode
from test_targets import SpiController, flash_ref, FSIZE
from tools_path import frame, CMD_WRITE, CMD_GO

FW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "firmware")
DONE = 0x55


def tx_line(rec):
    """uio[0] as a receiver sees it: high (the line's pull-up) whenever the
    chip does not drive it, as before the firmware takes the pin."""
    return [(o & 1) if e & 1 else 1 for o, e in zip(rec.out, rec.oe)]


async def par_send(dut, b, level, idle):
    """Parallel byte (test_boot.par_send), the other uio pins held at idle."""
    dut.ui_in.value = b
    await ClockCycles(dut.clk, 1)
    dut.uio_in.value = idle | level << 1
    for _ in range(400):
        await RisingEdge(dut.clk)
        if (int(dut.uo_out.value) >> 7) & 1 == level:
            return
    raise AssertionError("no parallel ack")


async def gl_boot(dut, name, data=(), uio_idle=0):
    """Load firmware/<name> (or a list of words for 128 on) and `data`
    [(address, words)] through the parallel loader and run it. The stream
    has an odd number of bytes, so the strobe (uio[1]) is left high, as a
    UART RX line idles."""
    words = name if isinstance(name, list) else         assemble_file(os.path.join(FW, name), base_addr=128)
    stream = []
    for addr, ws in data:
        stream += frame(CMD_WRITE, addr, ws)
    stream += frame(CMD_WRITE, 128, words) + frame(CMD_GO, 128)
    if len(stream) % 2 == 0:
        stream = [0x00] + stream              # the loader skips bytes before a SYNC
    start_clock(dut)
    await reset(dut, ui_in=0b10, uio_in=uio_idle)
    await ClockCycles(dut.clk, 50)
    for k, b in enumerate(stream):
        await par_send(dut, b, (k + 1) & 1, uio_idle & ~0x02)
    # the loader ACKs the last frame on the UART, then sets uo_out[3] and jumps
    for _ in range(5000):
        await RisingEdge(dut.clk)
        if int(dut.uo_out.value) & 0x08:
            return
    raise AssertionError("the loader never started the firmware")


@cocotb.test()
async def test_gl_uart_tx(dut):
    """uart_tx.s: the message from data RAM, every edge on the 104-cycle
    grid of the first start bit (test_firmware.test_fw_uart_tx_drift_free)."""
    msg = b"gate level, drift free"
    await gl_boot(dut, "uart_tx.s", data=[(0, list(msg) + [0])])
    rec = Recorder(dut)
    await rec.until_uo(lambda v: v == DONE, 40000)
    await ClockCycles(dut.clk, 200)
    bits = tx_line(rec)
    got = uart_decode(bits, 104, pin=0)
    assert got == list(msg), got
    first = next(i for i in range(1, len(bits)) if bits[i - 1] == 1 and bits[i] == 0)
    edges = [i for i in range(first, len(bits)) if bits[i] != bits[i - 1]]
    assert all((e - edges[0]) % 104 == 0 for e in edges), \
        f"bit timing drifted: first edges at {edges[:8]}"


@cocotb.test()
async def test_gl_two_threads(dut):
    """thread_demo.s: T1 echoes UART while T0's square wave keeps an exact
    500-cycle half period (test_firmware.test_fw_threads_blink_and_echo)."""
    await gl_boot(dut, "thread_demo.s", uio_idle=0x02)
    rec = Recorder(dut)
    await ClockCycles(dut.clk, 300)
    msg = b"gl!"
    for b in msg:
        await uart_send(dut, b, bit_cycles=104)
    await ClockCycles(dut.clk, 2500)
    got = uart_decode(tx_line(rec), 104, pin=0)
    assert got == list(msg), f"T1 echo: {got}"
    b7 = [(v >> 7) & 1 for v in rec.out]
    edges = [k for k in range(1, len(b7)) if b7[k] != b7[k - 1]]
    assert len(edges) > 10
    assert {y - x for x, y in zip(edges, edges[1:])} == {500}, "T0 square wave must stay exact"


@cocotb.test()
async def test_gl_spi_flash(dut):
    """spi_flash.s at its fastest SCK (28 cycles): JEDEC ID, status, a READ
    across the 512-byte wrap, a page program and its read-back
    (test_targets flash tests)."""
    rng = random.Random(8)
    mem = [rng.randrange(256) for _ in range(FSIZE)]
    await gl_boot(dut, "spi_flash.s", data=[(512, mem)], uio_idle=0x20)
    transfers = [[0x9F, 0, 0, 0], [0x05, 0], [0x03, 0x00, 0x01, 0xFC] + [0] * 6,
                 [0x06], [0x05, 0], [0x02, 0x00, 0x00, 0x40, 0x0F, 0xF0],
                 [0x03, 0x00, 0x00, 0x40, 0, 0]]
    bench = Bench(dut)
    ctl = bench.add(SpiController(0, transfers, half=14, lead=16, gap=64))
    ctl.bench = bench
    await bench.run(ctl.end + 20)
    want, _, _ = flash_ref(mem, transfers)
    got = ctl.bytes_read()
    bad = [(t, i, g[i], w[i]) for t, (g, w) in enumerate(zip(got, want))
           for i in range(len(w)) if w[i] is not None and g[i] != w[i]]
    assert not bad, f"MISO differs (transfer, byte, got, want): {bad[:6]}"
