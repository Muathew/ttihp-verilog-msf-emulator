# SPDX-License-Identifier: Apache-2.0
"""CRC unit and SERDES (TX/RX engine) tests.

Expected values come from golden_model.py (published CRC check values,
zlib) and from msf_tb's independent line-code helpers; the RTL is driven by
real firmware and observed only at its pins / data RAM.
"""
import random

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, ReadOnly, Timer

from msf_tb import (boot_asm, reg, sram, load_words, tx_cfg, rx_cfg, word_bits,
                    stuff_bits, destuff_bits, line_symbols, uart_send, NRZ, NRZI, MANCH)
from golden_model import eth_fcs, usb_crc16, usb_crc5, crc8_07

DONE = 0x55


# ----------------------------------------------------------------------------
# CRC unit
# ----------------------------------------------------------------------------
def crc_fw(cfg, poly, init, n):
    """Feed data RAM words 0..n-1 through CRC_W; result in R5:R4."""
    return f"""
        LDI R1, {poly & 0xFFFF}
        CRC_POLL R1
        LDI R1, {poly >> 16}
        CRC_POLH R1
        LDI R1, {init & 0xFFFF}
        CRC_SETL R1
        LDI R1, {init >> 16}
        CRC_SETH R1
        LDR R1, #{cfg}
        CRC_CFG R1
        LDR R2, #0
        LDR R6, #{n}
    lp: LD R3, R2
        CRC_W R3
        INCR R2
        DECR R6
        JNZ lp
        CRC_RDL R4
        CRC_RDH R5
        UO_WI {DONE}
    halt: JMP halt
    """


async def run_crc(dut, cfg, poly, init, words):
    await boot_asm(dut, crc_fw(cfg, poly, init, len(words)),
                   extra={i: w for i, w in enumerate(words)})
    for _ in range(4000):
        await RisingEdge(dut.clk)
        if int(dut.uo_out.value) == DONE:
            break
    await ClockCycles(dut.clk, 2)
    return (reg(dut, 5) << 16) | reg(dut, 4)


CHECK = b"123456789"


@cocotb.test()
async def test_crc8_normal(dut):
    v = await run_crc(dut, cfg=7, poly=0x07000000, init=0, words=list(CHECK))
    assert v >> 24 == crc8_07(CHECK) == 0xF4, f"crc={v:#010x}"


@cocotb.test()
async def test_crc32_ethernet_bytes(dut):
    v = await run_crc(dut, cfg=0x17, poly=0xEDB88320, init=0xFFFFFFFF, words=list(CHECK))
    assert v ^ 0xFFFFFFFF == eth_fcs(CHECK) == 0xCBF43926, f"crc={v:#010x}"


@cocotb.test()
async def test_crc32_ethernet_16bit_words(dut):
    """16 bits per CRC_W, little-endian byte pairs (the Ethernet TX loop)."""
    data = bytes(random.Random(7).randrange(256) for _ in range(46))
    words = [data[i] | data[i + 1] << 8 for i in range(0, len(data), 2)]
    v = await run_crc(dut, cfg=0x1F, poly=0xEDB88320, init=0xFFFFFFFF, words=words)
    assert v ^ 0xFFFFFFFF == eth_fcs(data)


@cocotb.test()
async def test_crc16_usb(dut):
    v = await run_crc(dut, cfg=0x17, poly=0xA001, init=0xFFFF, words=list(CHECK))
    assert (v & 0xFFFF) ^ 0xFFFF == usb_crc16(CHECK) == 0xB4C8


@cocotb.test()
async def test_crc5_usb_11bit(dut):
    vals = [0, 0x15 | 0xE << 7, 0x7F | 0xF << 7, 0x3A1]
    v = await run_crc(dut, cfg=0x10 | 10, poly=0x14, init=0x1F, words=vals[:1])
    assert (v & 0x1F) ^ 0x1F == usb_crc5(0) == 0x02
    for val in vals[1:]:
        v = await run_crc(dut, cfg=0x10 | 10, poly=0x14, init=0x1F, words=[val])
        assert (v & 0x1F) ^ 0x1F == usb_crc5(val), f"crc5({val:#x})"


@cocotb.test()
async def test_crc_busy_timing(dut):
    """CRC_W busy for exactly len cycles: CRC_RDL right after stalls len cycles."""
    await boot_asm(dut, """
        LDR R1, #7
        CRC_CFG R1
        CNT_R R2
        CRC_W R1        ; c
        CRC_RDL R3      ; c+1, busy until c+8 -> releases c+9
        CNT_R R4        ; c+10
        SUBS R5, R4, R2
    halt: JMP halt
    """)
    await ClockCycles(dut.clk, 60)
    assert reg(dut, 5) == 11


# ----------------------------------------------------------------------------
# TX engine
# ----------------------------------------------------------------------------
def tx_fw(cfg, div, n, oe):
    return f"""
        LDI R1, {div}
        TX_DIV R1
        LDI R1, {cfg}
        TX_CFG R1
        OE_SET {oe}
        LDR R2, #0
        LDR R6, #{n}
    lp: LD R3, R2
        TX_PUSH R3
        INCR R2
        DECR R6
        JNZ lp
        TX_WAIT
        UO_WI {DONE}
    halt: JMP halt
    """


async def capture_tx(dut, cfg, div, words, oe=0xFF, max_cycles=20000):
    await boot_asm(dut, tx_fw(cfg, div, len(words), oe),
                   extra={i: w for i, w in enumerate(words)})
    act = dut.user_project.u_serdes.tx_act
    line, acts = [], []
    for _ in range(max_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        line.append(int(dut.uio_out.value))
        acts.append(int(act.value))
        if int(dut.uo_out.value) == DONE:
            break
    else:
        raise AssertionError("TX firmware never finished")
    await RisingEdge(dut.clk)
    return line, acts


def symbols(line, acts, pin, div, count):
    start = acts.index(1)
    return [(line[start + k * div + div // 2] >> pin) & 1 for k in range(count)], start


@cocotb.test()
async def test_tx_nrz_uart_frames(dut):
    """UART 8N1 as 10-bit LSB-first NRZ words (start + 8 data + stop)."""
    data = [0x55, 0x00, 0xFF, 0xA5, 0x3C]
    words = [(b << 1) | 0x200 for b in data]
    div = 13
    cfg = tx_cfg(pin=0, mode=NRZ, width=10, lsb=True, idle=1)
    line, acts = await capture_tx(dut, cfg, div, words)
    exp = line_symbols(word_bits(words, 10, True), NRZ, 1)
    got, _ = symbols(line, acts, 0, div, len(exp))
    assert got == exp


@cocotb.test()
async def test_tx_nrzi_stuffing(dut):
    """NRZI + bit stuffing, MSB-first 16-bit words, runs of 1s across words."""
    words = [0xFFFF, 0xFC00, 0x003F, 0x8001, 0x7E7E, 0x0000]
    for div in (1, 8):
        cfg = tx_cfg(pin=2, mode=NRZI, width=16, lsb=False, stuff=True, idle=1)
        line, acts = await capture_tx(dut, cfg, div, words)
        exp = line_symbols(stuff_bits(word_bits(words, 16, False)), NRZI, 1)
        got, _ = symbols(line, acts, 2, div, len(exp))
        assert got == exp, f"div={div}"
        assert destuff_bits(stuff_bits(word_bits(words, 16, False))) == word_bits(words, 16, False)


@cocotb.test()
async def test_tx_manchester_gapless(dut):
    """IEEE Manchester at 1 cycle per half-bit (10BASE-T rate at 20 MHz)."""
    rng = random.Random(3)
    words = [rng.randrange(65536) for _ in range(12)]
    for div in (1, 3):
        cfg = tx_cfg(pin=4, mode=MANCH, width=16, lsb=True)
        line, acts = await capture_tx(dut, cfg, div, words)
        exp = line_symbols(word_bits(words, 16, True), MANCH, 0)
        got, start = symbols(line, acts, 4, div, len(exp))
        assert got == exp, f"div={div}"
        assert all(acts[start:start + len(exp) * div]), "gap in the Manchester stream"


@cocotb.test()
async def test_tx_differential_pair(dut):
    words = [0x96, 0x3C]
    cfg = tx_cfg(pin=7, mode=NRZI, width=8, lsb=True, diff=True, idle=1)
    line, acts = await capture_tx(dut, cfg, 4, words)
    start = acts.index(1)
    for v in line[start:start + 16 * 4]:
        assert ((v >> 7) & 1) != (v & 1), "pin+1 (wraps to 0) must be the complement"


@cocotb.test()
async def test_tx_width_and_msb_first(dut):
    words = [0x5, 0x2, 0x7]
    cfg = tx_cfg(pin=1, mode=NRZ, width=3, lsb=False, idle=0)
    line, acts = await capture_tx(dut, cfg, 5, words)
    exp = word_bits(words, 3, False)
    got, _ = symbols(line, acts, 1, 5, len(exp))
    assert got == exp


# ----------------------------------------------------------------------------
# RX engine
# ----------------------------------------------------------------------------
def rx_fw(cfg, div, n):
    return f"""
        LDR R1, #0
        TMO R1                  ; RX_POP waits forever
        LDI R1, {div}
        RX_DIV R1
        LDI R1, {cfg}
        RX_CFG R1
        UO_WI 0x01              ; armed
        LDR R2, #0
        LDR R6, #{n}
    lp: RX_POP R3
        ST R2, R3
        INCR R2
        DECR R6
        JNZ lp
        STAT R4
        UO_WI {DONE}
    halt: JMP halt
    """


async def wait_uo(dut, val, limit=200000):
    for _ in range(limit):
        await RisingEdge(dut.clk)
        if int(dut.uo_out.value) == val:
            return
    raise AssertionError(f"uo_out never reached {val:#x}")


@cocotb.test()
async def test_rx_uart_char_mode(dut):
    """Async-character mode = UART RX; back-to-back frames, baud error +-2 %."""
    data = [0x00, 0xFF, 0x55, 0xA5, 0x3C, 0x81]
    for bit in (102, 104, 106):           # firmware assumes 104
        await boot_asm(dut, rx_fw(rx_cfg(pin=1, char=True, width=8, lsb=True), 104, len(data)),
                       uio_in=2)
        await wait_uo(dut, 0x01)
        for b in data:
            await uart_send(dut, b, bit_cycles=bit)
        await wait_uo(dut, DONE)
        got = [sram(dut, i) for i in range(len(data))]
        assert got == data, f"bit={bit}: {got}"
        assert reg(dut, 4) & 0x8 == 0, "unexpected overrun"


@cocotb.test()
async def test_rx_uart_divisor_sweep(dut):
    """UART RX at every divisor 4..40: the bit timer's loads (div-1, div/2-1,
    div+div/2-1) take every low-bit pattern, even and odd."""
    data = [0x55, 0xA3]
    for div in range(4, 41):
        await boot_asm(dut, rx_fw(rx_cfg(pin=1, char=True, width=8, lsb=True), div, len(data)),
                       uio_in=2)
        await wait_uo(dut, 0x01)
        for b in data:
            await uart_send(dut, b, bit_cycles=div)
        await wait_uo(dut, DONE, 200)
        got = [sram(dut, i) for i in range(len(data))]
        assert got == data, f"div={div}: {[hex(x) for x in got]}"


@cocotb.test()
async def test_rx_overrun_flag(dut):
    """Two words without a pop -> rx_overrun in STAT."""
    await boot_asm(dut, f"""
        LDR R1, #104
        RX_DIV R1
        LDI R1, {rx_cfg(pin=1, char=True)}
        RX_CFG R1
        UO_WI 1
        LDR R1, #0
        TMO R1
    w:  STAT R4
        LDR R5, #8
        AND R5, R4, R5
        SUBS R5, R5, R0          ; R0 = 0 at boot
        JZ w
        RX_POP R3
        UO_WI {DONE}
    halt: JMP halt
    """, uio_in=2)
    await wait_uo(dut, 1)
    for b in (0x11, 0x22):
        await uart_send(dut, b)
    await wait_uo(dut, DONE)
    assert reg(dut, 3) == 0x22


async def drive_levels(dut, pin, levels, period, ppm=0, jitter=0, seed=0):
    """Drive uio_in[pin] with per-symbol levels like a real transmitter: symbol
    k starts at round(k*period*(1+ppm/1e6)) + j_k, j_k in [-jitter, jitter]
    independently per edge (jitter does not accumulate)."""
    rng = random.Random(seed)
    p = period * (1 + ppm / 1e6)
    starts = [round(k * p) + (rng.randint(-jitter, jitter) if k else 0)
              for k in range(len(levels) + 1)]
    for k, lv in enumerate(levels):
        dut.uio_in.value = (lv & 1) << pin
        await ClockCycles(dut.clk, max(1, starts[k + 1] - starts[k]))


@cocotb.test()
async def test_rx_nrzi_destuff_resync_jitter(dut):
    """Continuous NRZI + de-stuffing + edge resync, 8 cycles/bit with jitter."""
    rng = random.Random(11)
    words = [0x80, 0x3F, 0xFF, 0xFE, 0x00, 0x7F, 0xC3, 0x01]   # 0x80 = SYNC (LSB-first)
    bits = stuff_bits(word_bits(words, 8, True))
    levels = line_symbols(bits, NRZI, 1)
    for ppm, seed in ((0, 1), (15000, 2), (-15000, 3)):    # +-1.5 % (USB LS limit)
        await boot_asm(dut, rx_fw(rx_cfg(pin=5, mode=NRZI, destuff=True, resync=True), 8,
                                  len(words)), uio_in=1 << 5)
        await wait_uo(dut, 0x01)
        await ClockCycles(dut.clk, 30)
        await drive_levels(dut, 5, levels + [levels[-1]] * 4, 8, ppm=ppm, jitter=1, seed=seed)
        await wait_uo(dut, DONE, 5000)
        got = [sram(dut, i) for i in range(len(words))]
        assert got == words, f"ppm={ppm}: {[hex(x) for x in got]}"


@cocotb.test()
async def test_filter_removes_short_pulses(dut):
    """FILTER on uio2 with len 4, uio3 left alone: of pulses 1, 2, 3, 4, 5
    and 8 cycles wide on both, uio3 shows all six rises and uio2 only those
    of 4 cycles or more (isa.md §6.7)."""
    await boot_asm(dut, f"""
        LDI R1, 300
        TMO R1                  ; a count ends after 300 quiet cycles
        LDI R1, 0x04 | 4 << 8
        FILTER R1
        LDI R2, t1
        TSTART R2               ; T1 counts uio3's rises, T0 uio2's
        LDR R3, #0
        UO_WI 1
    c0: WAIT_EDGE 2, rise
        JZ  d0
        INCR R3
        JMP c0
    d0: UO_WI {DONE}
    h0: JMP h0
    t1: LDR R3, #0
    c1: WAIT_EDGE 3, rise
        JZ  h1
        INCR R3
        JMP c1
    h1: JMP h1
    """)
    await wait_uo(dut, 0x01)
    for w in (1, 2, 3, 4, 5, 8):
        dut.uio_in.value = 0x0C
        await ClockCycles(dut.clk, w)
        dut.uio_in.value = 0
        await ClockCycles(dut.clk, 40)
    await wait_uo(dut, DONE, 2000)
    assert (reg(dut, 3), reg(dut, 3, 1)) == (3, 6)


@cocotb.test()
async def test_filter_delay(dut):
    """A filtered pin's change arrives exactly len cycles after the same
    change on an unfiltered pin."""
    for n in (4, 9):
        await boot_asm(dut, f"""
            LDI R1, 0x04 | {n} << 8
            FILTER R1
            LDR R1, #0
            TMO R1
            UO_WI 1
            WAIT_EDGE 3, rise
            CNT_R R5
            WAIT_EDGE 2, rise
            CNT_R R6
            SUBS R6, R6, R5
            UO_WI {DONE}
        h:  JMP h
        """)
        await wait_uo(dut, 0x01)
        await ClockCycles(dut.clk, 20)
        dut.uio_in.value = 0x0C
        await wait_uo(dut, DONE, 200)
        dut.uio_in.value = 0
        assert reg(dut, 6) == n


def spiky_uart(data, bit=104, spike=3):
    """Per-cycle levels of 8N1 frames with an inverted spike of `spike`
    cycles at the centre of every bit, and in the idle line between frames."""
    lv = []

    def bit_(v):
        seg = [v] * bit
        for i in range(bit // 2 - spike // 2, bit // 2 - spike // 2 + spike):
            seg[i] = 1 - v
        lv.extend(seg)
    for _ in range(2):
        bit_(1)
    for b in data:
        bit_(0)
        for i in range(8):
            bit_((b >> i) & 1)
        bit_(1)
        bit_(1)
    return lv + [1] * bit


@cocotb.test()
async def test_filter_uart_spikes(dut):
    """UART RX at 104 cycles a bit with a 3-cycle spike at every bit centre
    (where the RX engine samples) and in the idle line: without the filter
    the bytes come out wrong, with FILTER on the RX pin (len 6) they are
    right."""
    data = [0x55, 0xA3, 0x00, 0xFF]
    levels = spiky_uart(data)
    for filt, ok in ((0, False), (0x02 | 6 << 8, True)):
        fw = f"LDI R1, {filt}\nFILTER R1\n" + rx_fw(
            rx_cfg(pin=1, char=True, width=8, lsb=True), 104, len(data))
        await boot_asm(dut, fw, uio_in=2)
        await wait_uo(dut, 0x01)
        for v in levels:
            dut.uio_in.value = v << 1
            await ClockCycles(dut.clk, 1)
        await ClockCycles(dut.clk, 200)
        got = [sram(dut, i) for i in range(len(data))]
        assert (got == data) == ok, f"filter {filt:#x}: {[hex(x) for x in got]}"


@cocotb.test()
async def test_rx_manchester_frames_jitter(dut):
    """Manchester RX (mode 2, half-bit = RX_DIV = 8): two frames separated by
    idle line, each starting with a 1 (its first half is the idle level),
    at -2 %, 0 and +2 % rate with +-1 cycle of jitter on every edge. Both
    frames decode, the second after the receiver re-locks."""
    frames = [[0x55, 0xA7, 0x3C, 0x00, 0xFF], [0x81, 0x6E]]
    words = frames[0] + frames[1]
    for ppm, seed in ((0, 1), (20000, 2), (-20000, 3)):
        await boot_asm(dut, rx_fw(rx_cfg(pin=5, mode=MANCH, width=8, lsb=True), 8,
                                  len(words)), uio_in=0)
        await wait_uo(dut, 0x01)
        await ClockCycles(dut.clk, 30)
        for f in frames:
            levels = line_symbols(word_bits(f, 8, True), MANCH, 0)
            await drive_levels(dut, 5, levels + [0] * 6, 8, ppm=ppm, jitter=1, seed=seed)
        await wait_uo(dut, DONE, 5000)
        got = [sram(dut, i) for i in range(len(words))]
        assert got == words, f"ppm={ppm}: {[hex(x) for x in got]}"


@cocotb.test()
async def test_rx_manchester_loopback(dut):
    """TX Manchester (pin 0) wired to RX Manchester (pin 3), same chip, the
    same divider (6 cycles a half-bit: 1 Mbit/s at 12 MHz), back to back."""
    rng = random.Random(9)
    words = [0x55] + [rng.randrange(256) for _ in range(12)]
    fw = f"""
        LDR R1, #0
        TMO R1
        LDR R1, #6
        TX_DIV R1
        RX_DIV R1
        LDI R1, {tx_cfg(pin=0, mode=MANCH, idle=0)}
        TX_CFG R1
        OE_SET 1
        LDI R1, {rx_cfg(pin=3, mode=MANCH)}
        RX_CFG R1
        LDI R1, rx_thread
        TSTART R1               ; T1 receives while T0 transmits
        LDR R2, #0
        LDR R6, #{len(words)}
    tl: LD R3, R2
        TX_PUSH R3
        INCR R2
        DECR R6
        JNZ tl
    halt: JMP halt
    rx_thread:
        LDR R2, #64
        LDR R6, #{len(words)}
    rl: RX_POP R3
        ST R2, R3
        INCR R2
        DECR R6
        JNZ rl
        UO_WI {DONE}
    t1h: JMP t1h
    """
    await boot_asm(dut, fw, extra={i: w for i, w in enumerate(words)})

    async def wire():
        while True:
            await RisingEdge(dut.clk)
            dut.uio_in.value = (int(dut.uio_out.value) & 1) << 3
    cocotb.start_soon(wire())
    await wait_uo(dut, DONE, 10000)
    got = [sram(dut, 64 + i) for i in range(len(words))]
    assert got == words, [hex(x) for x in got]


@cocotb.test()
async def test_tx_rx_loopback(dut):
    """TX (NRZI+stuff, pin 0) wired to RX (pin 3) by the testbench, same chip."""
    rng = random.Random(5)
    words = [0x80] + [rng.randrange(256) for _ in range(10)] + [0xFF, 0xFF]
    fw = f"""
        LDR R1, #0
        TMO R1
        LDR R1, #6
        TX_DIV R1
        RX_DIV R1
        LDI R1, {tx_cfg(pin=0, mode=NRZI, stuff=True, idle=1)}
        TX_CFG R1               ; line goes to idle J first ...
        OE_SET 1
        LDR R1, #20
        CNT_R R4
        ADDS R4, R4, R1
        WAIT_CMP R4             ; ... let it reach the RX pin (wire + sync)
        LDI R1, {rx_cfg(pin=3, mode=NRZI, destuff=True, resync=True)}
        RX_CFG R1               ; NRZI reference = idle level
        LDI R1, rx_thread
        TSTART R1               ; T1 receives while T0 transmits
        LDR R2, #0
        LDR R6, #{len(words)}
    tl: LD R3, R2
        TX_PUSH R3
        INCR R2
        DECR R6
        JNZ tl
    halt: JMP halt
    rx_thread:
        LDR R2, #64
        LDR R6, #{len(words)}
    rl: RX_POP R3
        ST R2, R3
        INCR R2
        DECR R6
        JNZ rl
        UO_WI {DONE}
    t1h: JMP t1h
    """
    await boot_asm(dut, fw, uio_in=1 << 3, extra={i: w for i, w in enumerate(words)})

    async def wire():
        while True:
            await RisingEdge(dut.clk)
            v = (int(dut.uio_out.value) & 1) << 3
            dut.uio_in.value = v
    cocotb.start_soon(wire())
    await wait_uo(dut, DONE, 10000)
    got = [sram(dut, 64 + i) for i in range(len(words))]
    assert got == words, [hex(x) for x in got]
