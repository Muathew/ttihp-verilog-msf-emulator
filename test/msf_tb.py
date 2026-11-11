# SPDX-License-Identifier: Apache-2.0
"""Shared cocotb helpers for the msfcore test modules."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Edge, RisingEdge, ReadOnly, Timer

from assembler import assemble

CLK_NS = 80  # 12.5 MHz sim clock; the design is cycle-based, period is arbitrary
RESET_SYNC = 2  # clock edges between rst_n rising and the core's cycle 0 (isa.md section 1)


_clock_task = None


async def _store_ahead_watch(dut):
    """Fail the test the moment a word issues within two cycles of a store writing
    it (tb.v's detector; isa.md section 1: the old word would run). A test that
    does it on purpose does not call start_clock."""
    try:
        evt = dut.sa_evt
    except AttributeError:
        return                                  # a gate-level run has no such signal
    await Edge(evt)
    raise AssertionError("store-ahead: a word issued within two cycles of a store "
                         "writing it (docs/isa.md section 1)")


def start_clock(dut, period_ns=CLK_NS):
    """Start the clock once per test (a test may boot several times; cocotb
    kills a test's tasks when it ends, so a finished task means a new test)."""
    global _clock_task
    if _clock_task is None or _clock_task.done():
        _clock_task = cocotb.start_soon(Clock(dut.clk, period_ns, unit="ns").start())
        cocotb.start_soon(_store_ahead_watch(dut))


def load_words(dut, words, start=128):
    """Back-door write into the SRAM model (any address 0..1023)."""
    for i, w in enumerate(words):
        dut.user_project.u_sram.mem[start + i].value = w & 0xFFFF


def load_asm(dut, asm, base=128):
    words = assemble(asm, base_addr=base)
    load_words(dut, words, base)
    return words


def sram(dut, addr):
    return int(dut.user_project.u_sram.mem[addr].value)


async def reset(dut, ui_in=1, uio_in=0, cycles=5):
    """Async reset with ui_in[0]=1 (direct boot to 128) by default."""
    await Timer(1, unit="ns")          # leave any ReadOnly phase before writing
    dut.ena.value = 1
    dut.ui_in.value = ui_in
    dut.uio_in.value = uio_in
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, cycles)
    dut.rst_n.value = 1
    # v4 (plan-v4 B2): the reset is released through two flops, so the core's cycle 0
    # is the third edge after rst_n rises. Wait for the second, so that what the caller
    # does next happens in the core's cycle 0, as it did before the synchroniser
    # (the pads stay as they were: no test drives them in these two cycles).
    await ClockCycles(dut.clk, RESET_SYNC)


async def boot_asm(dut, asm, ui_in=1, uio_in=0, extra=None):
    """Assemble at 128, preload SRAM (+ optional {addr: word}), reset, run."""
    await Timer(1, unit="ns")          # leave any ReadOnly phase before writing
    start_clock(dut)
    words = load_asm(dut, asm)
    for a, w in (extra or {}).items():
        load_words(dut, [w], a)
    await reset(dut, ui_in=ui_in, uio_in=uio_in)
    return words


def reg(dut, i, thread=0):
    c = dut.user_project.u_ctrl
    rf = (c.u_rf, c.u_rf1, c.u_rf2, c.u_rf3)[thread]
    return int(rf.rf[i].value)


def ctrl(dut):
    return dut.user_project.u_ctrl


async def settle():
    """Step just past the clock edge so registered outputs are stable."""
    await Timer(1, unit="ns")


async def uart_send(dut, byte, bit_cycles=104, pin=1, gap=0):
    """Drive one 8N1 UART frame on uio_in[pin] (other uio_in bits held 0)."""
    hi = 1 << pin
    dut.uio_in.value = 0
    await ClockCycles(dut.clk, bit_cycles)
    for i in range(8):
        dut.uio_in.value = hi if (byte >> i) & 1 else 0
        await ClockCycles(dut.clk, bit_cycles)
    dut.uio_in.value = hi
    await ClockCycles(dut.clk, bit_cycles + gap)


async def capture(dut, cycles, sig="uio_out"):
    """Sample a top-level output once per cycle, return the list of ints."""
    out = []
    h = getattr(dut, sig)
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        out.append(int(h.value))
    return out


def uart_decode(samples, bit_cycles, pin=0, start_at=0):
    """Decode 8N1 frames from per-cycle samples of a pin. Returns bytes."""
    bits = [(s >> pin) & 1 for s in samples]
    out, i = [], start_at
    while i < len(bits) - 1:
        if bits[i] == 1 and i + 1 < len(bits) and bits[i + 1] == 0:
            s = i + 1                      # start-bit first cycle
            mid = s + bit_cycles // 2
            if mid + 9 * bit_cycles >= len(bits):
                break
            if bits[mid] != 0:
                i += 1
                continue
            v = 0
            for b in range(8):
                v |= bits[mid + (b + 1) * bit_cycles] << b
            stop = bits[mid + 9 * bit_cycles]
            out.append(v if stop == 1 else -1)
            i = mid + 9 * bit_cycles
        else:
            i += 1
    return out


# ----------------------------------------------------------------------------
# SERDES / CRC configuration words (isa.md §6) -- Python mirrors of the
# firmware constants in firmware/msf.inc
# ----------------------------------------------------------------------------
NRZ, NRZI, MANCH = 0, 1, 2


def tx_cfg(pin, mode=NRZ, width=8, lsb=True, stuff=False, diff=False, idle=0, en=True,
           release=False):
    return (pin | diff << 3 | mode << 4 | stuff << 6 | lsb << 7 |
            (width - 1) << 8 | idle << 12 | en << 13 | release << 14)


def rx_cfg(pin, mode=NRZ, width=8, lsb=True, destuff=False, char=False, resync=False, en=True):
    return (pin | mode << 4 | destuff << 6 | lsb << 7 | (width - 1) << 8 |
            char << 12 | en << 13 | resync << 14)


def word_bits(words, width, lsb):
    """Data bits in transmission order."""
    out = []
    for w in words:
        rng = range(width) if lsb else range(width - 1, -1, -1)
        out += [(w >> i) & 1 for i in rng]
    return out


def stuff_bits(bits):
    out, ones = [], 0
    for b in bits:
        out.append(b)
        ones = ones + 1 if b else 0
        if ones == 6:
            out.append(0)
            ones = 0
    return out


def destuff_bits(bits):
    out, ones, skip = [], 0, False
    for b in bits:
        if skip:
            skip = False
            ones = 0
            continue
        out.append(b)
        ones = ones + 1 if b else 0
        if ones == 6:
            skip = True
    return out


def line_symbols(bits, mode, idle):
    """Expected line level per symbol (Manchester: two symbols per bit)."""
    out, lvl = [], idle
    for b in bits:
        if mode == NRZI:
            lvl = lvl if b else 1 - lvl
            out.append(lvl)
        elif mode == MANCH:
            out += [1 - b, b]
        else:
            out.append(b)
    return out
