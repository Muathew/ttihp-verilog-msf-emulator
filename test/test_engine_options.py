# SPDX-License-Identifier: Apache-2.0
"""v4 step 4c-4e: engine options on port page 2 (docs/isa.md section 6.9): general stuffing,
error flags (frame, stuff, underrun, STAT bit 15) and the external clock, TX and RX.

References are written from the spec here: the stuffing rule as a function of bits, and an SPI
bus master that drives SCK / MOSI / CS per cycle (all four modes) and samples MISO in the cycle
of its sampling edge. The RTL is observed at the pins and in data RAM."""
import cocotb
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge, Timer

from msf_tb import boot_asm, reg, sram, tx_cfg, rx_cfg, word_bits, line_symbols, NRZ

DONE = 0x55
SCK, MOSI, MISO, CS = 2, 3, 4, 5            # uio pins of the SPI tests


# ---------------------------------------------------------------------------- references
def stuff_ref(bits, n, kind):
    """Line bits for data bits under isa.md 6.9: kind 0 inserts a 0 after n ones, kind 1 the
    opposite after n equal bits (the inserted bit starts the next run); a stuff bit that is due
    after the last data bit is sent too."""
    out, run, last = [], 0, None
    for b in bits:
        if run == n:
            sb = (1 - last) if kind else 0
            out.append(sb)
            run, last = (1, sb) if kind else (0, last)
        out.append(b)
        if kind:
            run, last = (run + 1 if run and b == last else 1), b
        else:
            run = run + 1 if b else 0
    if run == n:
        out.append((1 - last) if kind else 0)
    return out


def opt(n=0, kind=0, stream=0, schk=0):
    return n | kind << 4 | stream << 5 | schk << 5       # TX_OPT bit 5 = stream, RX_OPT bit 5 = stop check


def ext(pin, edge, gate=None, gate_active_high=0, pre=0, bank=0):
    """TX_EXT / RX_EXT value (isa.md 6.9)."""
    v = pin | bank << 3 | edge << 4 | 1 << 5 | pre << 6
    if gate is not None:
        v |= gate << 8 | gate_active_high << 12 | 1 << 13
    return v


# ---------------------------------------------------------------------------- TX stuffing
async def tx_line(dut, words, cfg, div, topt, cycles=6000):
    """Run a TX firmware with TX_OPT = topt; the line (uio_out) per cycle, and tx_act."""
    fw = f"""
        ESEL   #4
        LDI    R1, {topt}
        TX_OPT R1
        ESEL   #0
        LDI    R1, {cfg}
        TX_CFG R1
        OE_SET 0xFF
        LDR    R2, #{div}
        TX_DIV R2
        LDR R2, #0
        LDR R6, #{len(words)}
    lp: LD R3, R2
        TX_PUSH R3
        INCR R2
        DECR R6
        JNZ lp
        TX_WAIT
        UO_WI {DONE}
    h:  JMP h
    """
    await boot_asm(dut, fw, extra={i: w for i, w in enumerate(words)})
    act = dut.user_project.u_serdes.tx_act
    line, acts = [], []
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        line.append(int(dut.uio_out.value))
        acts.append(int(act.value))
        if int(dut.uo_out.value) == DONE:
            break
    else:
        raise AssertionError("TX firmware never finished")
    return line, acts


@cocotb.test()
async def test_tx_general_stuffing(dut):
    """HDLC (after 5 ones a 0), CAP-style (after 5 equal bits the opposite, and after 3), each
    against the stuffing reference on runs of ones and zeros across words; the v3 rule (n = 6) is
    what TX_CFG bit 6 alone still gives."""
    words = [0xFF, 0x1F, 0x00, 0xC3, 0x7E, 0xF8, 0x07]
    div = 7
    for n, kind, lsb in ((5, 0, True), (5, 1, False), (3, 1, True), (4, 0, False), (6, 0, True)):
        cfg = tx_cfg(pin=0, mode=NRZ, width=8, lsb=lsb, idle=1)
        line, acts = await tx_line(dut, words, cfg, div, opt(n=n, kind=kind))
        bits = word_bits(words, 8, lsb)
        exp = line_symbols(stuff_ref(bits, n, kind), NRZ, 1)
        start = acts.index(1)
        got = [(line[start + k * div + div // 2] >> 0) & 1 for k in range(len(exp))]
        assert got == exp, f"n={n} kind={kind} lsb={lsb}:\n got {got}\n exp {exp}"
    # classic: TX_CFG bit 6 with TX_OPT 0 is n = 6, kind 0
    cfg = tx_cfg(pin=0, mode=NRZ, width=8, lsb=True, idle=1, stuff=True)
    line, acts = await tx_line(dut, words, cfg, div, 0)
    exp = line_symbols(stuff_ref(word_bits(words, 8, True), 6, 0), NRZ, 1)
    start = acts.index(1)
    assert [(line[start + k * div + div // 2]) & 1 for k in range(len(exp))] == exp


# ---------------------------------------------------------------------------- RX stuffing and errors
async def drive_line(dut, symbols, div, pin=1, idle=1):
    """Put `symbols` on uio_in[pin], div cycles each, then idle."""
    for s in symbols:
        dut.uio_in.value = (s << pin)
        await ClockCycles(dut.clk, div)
    dut.uio_in.value = idle << pin


RX_FW = """
        ESEL   #4
        LDI    R1, {ropt}
        RX_OPT R1
        ESEL   #0
        LDR    R1, #{div}
        RX_DIV R1
        LDI    R1, {cfg}
        RX_CFG R1
        LDR R2, #64
        LDR R6, #{count}
    r1: RX_POP R3
        ST R2, R3
        INCR R2
        DECR R6
        JNZ r1
        ESEL   #4
        ERR_STAT R4
        ESEL   #0
        STAT   R5
        UO_WI  {done}
    h:  JMP h
"""


@cocotb.test()
async def test_rx_general_destuffing_and_stuff_errors(dut):
    """Words stuffed by the reference come back whole for n = 5 kind 0 and kind 1; a stuff bit
    that is not what the kind requires is E_STUFF (ERR_STAT bit 1, STAT bit 15) and is dropped
    anyway."""
    div = 9
    words = [0x7E, 0xFC, 0x03, 0xA5, 0x1F]
    for n, kind in ((5, 0), (5, 1), (4, 1)):
        bits = word_bits(words, 8, True)
        line = stuff_ref(bits, n, kind)
        cfg = rx_cfg(pin=1, mode=NRZ, width=8, lsb=True)
        await boot_asm(dut, RX_FW.format(ropt=opt(n=n, kind=kind), div=div, cfg=cfg,
                                         count=len(words), done=DONE), uio_in=1 << 1,
                       extra={a: 0xDEAD for a in range(64, 72)})   # stale words must not pass
        await ClockCycles(dut.clk, 60)
        await drive_line(dut, [0] + line[1:] if line[0] == 0 else line, div)
        await ClockCycles(dut.clk, 40)
        # the first data bit of 0x7E (LSB first) is 0: the stream starts with the transition
        got = [sram(dut, 64 + i) for i in range(len(words))]
        assert got == words, f"n={n} kind={kind}: {[hex(x) for x in got]}"
        assert reg(dut, 4) & 2 == 0 and reg(dut, 5) & 0x8000 == 0, "a stuff error on a legal stream"
    # a violation: after five 1s (n = 5, kind 0) a 1 where the stuff 0 belongs
    bad = [0] + [1] * 5 + [1] + [0, 0, 1, 0, 1, 0, 1, 0] * 1
    await boot_asm(dut, RX_FW.format(ropt=opt(n=5, kind=0), div=div,
                                     cfg=rx_cfg(pin=1, mode=NRZ, width=8, lsb=True),
                                     count=1, done=DONE), uio_in=1 << 1)
    await ClockCycles(dut.clk, 60)
    await drive_line(dut, bad, div)
    await ClockCycles(dut.clk, 40)
    assert reg(dut, 4) & 2 == 2, f"ERR_STAT {reg(dut, 4):03b}: E_STUFF not set"
    assert reg(dut, 5) & 0x8000, "STAT bit 15 is not set"


@cocotb.test()
async def test_frame_error_and_err_clr(dut):
    """RX_OPT bit 5 (character mode): the word is delivered at the stop bit's centre and a stop
    bit of 0 sets E_FRAME (ERR_STAT bit 0, STAT bit 15), the word delivered all the same;
    ERR_CLR clears it. A good frame sets nothing."""
    div = 12
    fw = f"""
        ESEL   #4
        LDI    R1, {opt(schk=1)}
        RX_OPT R1
        ESEL   #0
        LDR    R1, #{div}
        RX_DIV R1
        LDI    R1, {rx_cfg(pin=1, mode=NRZ, width=8, lsb=True, char=True)}
        RX_CFG R1
        RX_POP R3                 ; a good frame
        ESEL   #4
        ERR_STAT R4               ; 0
        ESEL   #0
        RX_POP R5                 ; a frame with stop bit 0
        ESEL   #4
        ERR_STAT R6               ; 1: E_FRAME
        ESEL   #0
        STAT   R7                 ; bit 15
        ESEL   #4
        LDR    R1, #1
        ERR_CLR R1
        ERR_STAT R2               ; 0 again
        ESEL   #0
        UO_WI  {DONE}
    h:  JMP h
    """
    await boot_asm(dut, fw, uio_in=1 << 1)
    await ClockCycles(dut.clk, 60)

    async def frame(byte, stop):
        bits = [0] + [(byte >> i) & 1 for i in range(8)] + [stop]
        await drive_line(dut, bits, div)
        await ClockCycles(dut.clk, 2 * div)

    await frame(0xA5, 1)
    await frame(0x3C, 0)
    await ClockCycles(dut.clk, 60)
    assert (reg(dut, 3), reg(dut, 5)) == (0xA5, 0x3C), (hex(reg(dut, 3)), hex(reg(dut, 5)))
    assert reg(dut, 4) == 0, f"E_FRAME set by a good frame: {reg(dut, 4):03b}"
    assert reg(dut, 6) == 1, f"ERR_STAT after the bad stop bit: {reg(dut, 6):03b}"
    assert reg(dut, 7) & 0x8000, "STAT bit 15 not set with E_FRAME"
    assert reg(dut, 2) == 0, "ERR_CLR did not clear E_FRAME"
    assert int(dut.uo_out.value) == DONE


@cocotb.test()
async def test_tx_underrun_with_stream(dut):
    """TX_OPT bit 5 (stream): a transmitter that finishes a word with none waiting sets E_UNDER;
    clearing the bit when the last word is pushed makes the end of a message no error."""
    def prog(second):
        return f"""
            ESEL   #4
            LDI    R1, {opt(stream=1)}
            TX_OPT R1
            ESEL   #0
            LDI    R1, {tx_cfg(pin=0, mode=NRZ, width=8, idle=1)}
            TX_CFG R1
            LDR    R1, #5
            TX_DIV R1
            LDR    R3, #0x5A
            TX_PUSH R3
            TX_PUSH R3
            {second}
            TX_WAIT
            ESEL   #4
            ERR_STAT R4
            ESEL   #0
            STAT   R5
            UO_WI  {DONE}
        h:  JMP h
        """
    await boot_asm(dut, prog(""))                       # stream stays on: the end is an underrun
    await ClockCycles(dut.clk, 400)
    assert reg(dut, 4) == 0b100, f"ERR_STAT {reg(dut, 4):03b}: E_UNDER expected"
    assert reg(dut, 5) & 0x8000
    await boot_asm(dut, prog("ESEL #4\nLDR R1, #0\nTX_OPT R1\nESEL #0"))   # cleared after the last push
    await ClockCycles(dut.clk, 400)
    assert reg(dut, 4) == 0, f"ERR_STAT {reg(dut, 4):03b}: the end of a message was an underrun"


# ---------------------------------------------------------------------------- external clock
def spi_schedule(words, nbits, half, cpol, cpha, lsb=False, lead=None):
    """Per-cycle (sck, mosi, cs, sample) of one transaction: CS low, the bits, CS high. sample marks
    the cycle of the master's sampling edge (leading edge for CPHA 0, trailing for 1)."""
    lead = half if lead is None else lead
    bits = []
    for w in words:
        order = range(nbits) if lsb else range(nbits - 1, -1, -1)
        bits += [(w >> i) & 1 for i in order]
    s = [(cpol, 0, 1, 0)] * 6                                     # idle
    s += [(cpol, bits[0] if cpha == 0 else 0, 0, 0)] * lead        # CS low, first bit set up for CPHA 0
    for k, b in enumerate(bits):
        nxt = bits[k + 1] if k + 1 < len(bits) else b
        if cpha == 0:      # data valid before the leading edge; changes at the trailing edge
            s += [(1 - cpol, b, 0, 1)] + [(1 - cpol, b, 0, 0)] * (half - 1)
            s += [(cpol, nxt, 0, 0)] * half
        else:              # data changes at the leading edge, is sampled at the trailing edge
            s += [(1 - cpol, b, 0, 0)] * half
            s += [(cpol, b, 0, 1)] + [(cpol, b, 0, 0)] * (half - 1)
    s += [(cpol, 0, 0, 0)] * half + [(cpol, 0, 1, 0)] * 8         # CS high
    return s


async def play(dut, sched, sample_pin=MISO):
    """Drive a schedule from the master's side; return MISO at each sample cycle."""
    got = []
    for sck, mosi, cs, samp in sched:
        await RisingEdge(dut.clk)
        await ReadOnly()
        if samp:
            got.append((int(dut.uio_out.value) >> sample_pin) & 1)
        await Timer(1, unit="ns")
        dut.uio_in.value = (sck << SCK) | (mosi << MOSI) | (cs << CS)
    return got


def bits_to_words(bits, nbits, lsb=False):
    out = []
    for i in range(0, len(bits) - nbits + 1, nbits):
        chunk = bits[i:i + nbits]
        out.append(sum(b << (k if lsb else nbits - 1 - k) for k, b in enumerate(chunk)))
    return out


@cocotb.test()
async def test_rx_external_clock_all_spi_modes(dut):
    """The RX engine on the master's clock: every SPI mode, at 8 and 4 cycles a bit, bytes
    in data RAM; a partial word is dropped when CS rises (isa.md 6.9). RX_EXT's edge is the
    master's sampling edge: rising for modes 0 and 3, falling for 1 and 2."""
    words = [0xA5, 0x3C, 0xFF, 0x01]
    for cpol, cpha in ((0, 0), (0, 1), (1, 0), (1, 1)):
        edge = 1 if (cpol ^ cpha) else 0                   # leading edge is rising for cpol 0
        # sampling edge: leading if cpha 0 else trailing; rising if (cpol == 0) == (cpha == 0)
        samp_rising = (cpol == 0) == (cpha == 0)
        edge = 0 if samp_rising else 1
        for half in (4, 2):
            fw = f"""
                LDI    R1, {rx_cfg(pin=MOSI, mode=NRZ, width=8, lsb=False)}
                RX_CFG R1
                ESEL   #4
                LDI    R1, {ext(SCK, edge, gate=CS, gate_active_high=0)}
                RX_EXT R1
                ESEL   #0
                LDR R2, #64
                LDR R6, #{len(words) + 1}
            r1: RX_POP R3
                ST R2, R3
                INCR R2
                DECR R6
                JNZ r1
                UO_WI {DONE}
            h:  JMP h
            """
            await boot_asm(dut, fw, uio_in=(cpol << SCK) | (1 << CS),
                           extra={a: 0xDEAD for a in range(64, 72)})
            await ClockCycles(dut.clk, 120)
            # a short transaction first: 5 clocks, then CS rises: the partial word is dropped
            await play(dut, spi_schedule([0b10110], 5, half, cpol, cpha))
            await play(dut, spi_schedule(words[:2], 8, half, cpol, cpha))
            await play(dut, spi_schedule(words[2:], 8, half, cpol, cpha))
            await ClockCycles(dut.clk, 60)
            got = [sram(dut, 64 + i) for i in range(len(words))]
            assert got == words, f"mode {cpol}{cpha}, {2 * half} cycles a bit: {[hex(x) for x in got]}"


@cocotb.test()
async def test_tx_external_clock_as_an_spi_slave(dut):
    """The TX engine answering a master: MISO changes on the shifting edge, three cycles after it
    on the pad, so a master that samples half a period later needs half >= 4 (8 cycles a bit).
    Modes 0 (pre: the first bit is out as soon as CS falls) and 1 (the first bit on the first
    edge), two words in one transaction (the buffer holds the second), the pin tri-stated
    (release) while CS is high."""
    words = [0x9C, 0x63]
    for cpol, cpha in ((0, 0), (0, 1)):
        # the engine shifts on the edge the master does not sample on
        shift_rising = (cpha == 1)                           # cpol 0: leading edge is rising
        fw = f"""
            LDI    R1, {tx_cfg(pin=MISO, mode=NRZ, width=8, lsb=False, idle=0, release=True)}
            TX_CFG R1
            ESEL   #4
            LDI    R1, {ext(SCK, 0 if shift_rising else 1, gate=CS, gate_active_high=0, pre=1 - cpha)}
            TX_EXT R1
            ESEL   #0
            OE_SET {1 << MISO}
            LDI    R3, {words[0]}
            TX_PUSH R3
            LDI    R3, {words[1]}
            TX_PUSH R3
            UO_WI  {DONE}
        h:  JMP h
        """
        await boot_asm(dut, fw, uio_in=(cpol << SCK) | (1 << CS))
        await ClockCycles(dut.clk, 120)
        got = await play(dut, spi_schedule([0, 0], 8, 4, cpol, cpha))
        assert bits_to_words(got, 8) == words, \
            f"mode {cpol}{cpha}: {[hex(w) for w in bits_to_words(got, 8)]} != {[hex(w) for w in words]}"
        # CS is high now: the pin is released (the pad shows OUT, 0)
        await ClockCycles(dut.clk, 20)
        assert (int(dut.uio_out.value) >> MISO) & 1 == 0 and \
            int(dut.user_project.u_serdes.tx_act.value) == 0


@cocotb.test()
async def test_external_clock_speed_limits(dut):
    """The limits isa.md 6.9 states, measured (mode 0, zero-delay simulation: a real pad needs
    margin on top): the receiver takes a master clock of 2 cycles a bit, the transmitter answers
    correctly from 6 cycles a bit (half = 3: the bit is on the pad 3 cycles after the edge, just
    in time for a master that samples half a period later) and not at 4."""
    words = [0xA5, 0x3C]
    for half, rx_ok_exp, tx_ok_exp in ((1, True, False), (2, True, False), (3, True, True), (4, True, True)):
        fw = f"""
            LDI R1, {rx_cfg(pin=MOSI, mode=NRZ, width=8, lsb=False)}
            RX_CFG R1
            ESEL #4
            LDI R1, {ext(SCK, 0, gate=CS, gate_active_high=0)}
            RX_EXT R1
            ESEL #0
            LDR R2, #64
            LDR R6, #2
        r1: RX_POP R3
            ST R2, R3
            INCR R2
            DECR R6
            JNZ r1
        h:  JMP h
        """
        await boot_asm(dut, fw, uio_in=(1 << CS), extra={64: 0xDEAD, 65: 0xDEAD})
        await ClockCycles(dut.clk, 120)
        await play(dut, spi_schedule(words, 8, half, 0, 0))
        await ClockCycles(dut.clk, 60)
        assert ([sram(dut, 64), sram(dut, 65)] == words) == rx_ok_exp, f"RX at {2 * half} cycles a bit"
        fw = f"""
            LDI R1, {tx_cfg(pin=MISO, mode=NRZ, width=8, lsb=False, idle=0, release=True)}
            TX_CFG R1
            ESEL #4
            LDI R1, {ext(SCK, 1, gate=CS, gate_active_high=0, pre=1)}
            TX_EXT R1
            ESEL #0
            OE_SET {1 << MISO}
            LDI R3, {words[0]}
            TX_PUSH R3
            LDI R3, {words[1]}
            TX_PUSH R3
        h:  JMP h
        """
        await boot_asm(dut, fw, uio_in=(1 << CS))
        await ClockCycles(dut.clk, 120)
        got = bits_to_words(await play(dut, spi_schedule([0, 0], 8, half, 0, 0)), 8)
        assert (got == words) == tx_ok_exp, f"TX at {2 * half} cycles a bit: {[hex(x) for x in got]}"
