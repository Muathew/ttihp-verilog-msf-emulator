# SPDX-License-Identifier: Apache-2.0
"""Firmware library demos (firmware/*.s) checked at the pins.

Every protocol is observed only through uio_out/uio_oe/uo_out and driven
only through uio_in/ui_in, by independent decoders and slave models written
from the protocol specs (golden references in golden_model.py).
"""
import random

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, ReadOnly, Timer

from fw_tb import DONE, spawn, fw_boot, Recorder, save_trace
from msf_tb import sram, uart_decode, uart_send
from golden_model import usb_crc16, eth_fcs


# ============================================================================
# UART TX (drift-free)
# ============================================================================
@cocotb.test()
async def test_fw_uart_tx_drift_free(dut):
    msg = b"msfcore says hi! 0123456789"
    await fw_boot(dut, "uart_tx.s", data={i: b for i, b in enumerate(msg + b"\0")})
    rec = Recorder(dut)
    await rec.until_uo(lambda v: v == DONE, 40000)
    await ClockCycles(dut.clk, 200)
    assert bytes(uart_decode(rec.out, 104, pin=0)) == msg
    save_trace("uart", rec.out, "uart:rx=0:baudrate=115200", "uart=rx-data",
               [f"{b:02X}" for b in msg], {"pin": 0, "hold": 104})
    # every edge sits on the 104-cycle grid of the first start bit: no drift
    bits = [v & 1 for v in rec.out]
    first_start = next(i for i in range(1, len(bits)) if bits[i - 1] == 1 and bits[i] == 0)
    edges = [i for i in range(first_start, len(bits)) if bits[i] != bits[i - 1]]
    assert all((e - edges[0]) % 104 == 0 for e in edges), "bit timing drifted"
    assert (edges[-1] - edges[0]) > 104 * 10 * (len(msg) - 1)


@cocotb.test()
async def test_fw_ws2812(dut):
    """WS2812 chain at 800 kHz from the TX engine: every bit 15 cycles, a 1
    high for 10, a 0 for 5 (the LEDs allow +-2), no gap between bits, the
    colours in order, then the line low for over 50 us to latch them."""
    leds = [(0x10, 0xFF, 0x00), (0xA5, 0x5A, 0x81), (0x00, 0x00, 0xFF)]   # G, R, B
    data = [b for led in leds for b in led]
    await fw_boot(dut, "ws2812.s", data={0: len(leds), **{1 + i: b for i, b in enumerate(data)}})
    rec = Recorder(dut)
    await rec.until_uo(lambda v: v == DONE, 20000)
    line = [v & 1 for v in rec.out]
    rises = [i for i in range(1, len(line)) if line[i] and not line[i - 1]]
    assert len(rises) == 24 * len(leds), len(rises)
    assert all(b - a == 15 for a, b in zip(rises, rises[1:])), "gap or jitter between bits"
    bits = []
    for r in rises:
        high = next(i for i in range(r, len(line)) if not line[i]) - r
        assert high in (5, 10), high
        bits.append(high == 10)
    got = [sum(bits[8 * k + j] << (7 - j) for j in range(8)) for k in range(len(data))]
    assert got == data, got
    last_fall = rises[-1] + (10 if bits[-1] else 5)
    assert len(line) - last_fall >= 600, "reset (latch) too short"
    save_trace("ws2812", rec.out, "rgb_led_ws281x:din=0", "rgb_led_ws281x=rgb",
               [f"#{r:02x}{g:02x}{b:02x}" for g, r, b in leds], {"pin": 0, "hold": 15})


# ============================================================================
# SPI master, modes 0-3, full duplex
# ============================================================================
class SpiSlave:
    SCK, MOSI, MISO, CS = 2, 3, 4, 5

    def __init__(self, dut, mode, reply):
        self.dut, self.cpol, self.cpha = dut, (mode >> 1) & 1, mode & 1
        self.reply, self.got = list(reply), []
        spawn(self._run())

    async def _run(self):
        d = self.dut
        prev_sck, prev_cs = self.cpol, 1
        tx = rx = nbits = 0
        miso = 0
        while True:
            await RisingEdge(d.clk)
            await ReadOnly()
            v, oe = int(d.uio_out.value), int(d.uio_oe.value)
            sck, mosi = (v >> self.SCK) & 1, (v >> self.MOSI) & 1
            cs = (v >> self.CS) & 1 if (oe >> self.CS) & 1 else 1   # pulled up until driven
            await Timer(1, unit="ns")
            if prev_cs and not cs:                       # select: load first byte
                tx, nbits, rx = (self.reply.pop(0) if self.reply else 0), 0, 0
                if not self.cpha:
                    miso = (tx >> 7) & 1
            if not cs and sck != prev_sck:
                leading = sck != self.cpol
                sample = leading if not self.cpha else not leading
                if sample:
                    rx = (rx << 1) | mosi
                    nbits += 1
                    if nbits == 8:
                        self.got.append(rx)
                        rx, nbits = 0, 0
                        tx = self.reply.pop(0) if self.reply else 0
                else:
                    miso = (tx >> (7 - nbits)) & 1     # shift edge: next bit out
            d.uio_in.value = miso << self.MISO
            prev_sck, prev_cs = sck, cs


@cocotb.test()
async def test_fw_spi_all_modes_full_duplex(dut):
    rng = random.Random(42)
    for mode in range(4):
        out = [rng.randrange(256) for _ in range(6)]
        back = [rng.randrange(256) for _ in range(6)]
        data = {0: mode, 1: len(out)}
        data.update({2 + i: b for i, b in enumerate(out)})
        await fw_boot(dut, "spi_master.s", data=data)
        slave = SpiSlave(dut, mode, back)
        rec = Recorder(dut)
        await rec.until_uo(lambda v: v == DONE, 20000)
        assert slave.got == out, f"mode {mode}: slave got {slave.got}, master sent {out}"
        got_back = [sram(dut, 64 + i) for i in range(len(back))]
        assert got_back == back, f"mode {mode}: master got {got_back}, slave sent {back}"
        idle = (rec.out[-1] >> 2) & 1
        assert idle == (mode >> 1), "SCK idle level = CPOL"
        save_trace(f"spi_mode{mode}", rec.pad,
                   f"spi:clk=2:mosi=3:miso=4:cs=5:cpol={mode >> 1}:cpha={mode & 1}",
                   "spi=mosi-data:miso-data",
                   [f"{x:02X}" for pair in zip(back, out) for x in pair],
                   {"pin": 3, "hold": 12})


# ============================================================================
# I2C master against a wired-AND slave model
# ============================================================================
class I2cSlave:
    SDA, SCL = 5, 6

    def __init__(self, dut, addr, read_data=(), stretch=0, seed=0):
        self.dut, self.addr = dut, addr
        self.read_data = list(read_data)
        self.stretch, self.rng = stretch, random.Random(seed)
        self.written, self.events, self.master_acks = [], [], []
        self.sda_low = False
        self.hold = 0
        spawn(self._run())

    async def _run(self):
        d = self.dut
        psda = pscl = 1
        state, byte, bits, mode = "idle", 0, 0, "w"
        prev_master_scl_low = False
        while True:
            await RisingEdge(d.clk)
            await ReadOnly()
            oe = int(d.uio_oe.value)
            m_sda_low, m_scl_low = bool((oe >> self.SDA) & 1), bool((oe >> self.SCL) & 1)
            await Timer(1, unit="ns")
            # clock stretching: when the master releases SCL, sometimes hold it
            if prev_master_scl_low and not m_scl_low and self.stretch and self.rng.random() < 0.3:
                self.hold = self.rng.randint(1, self.stretch)
            prev_master_scl_low = m_scl_low
            scl_hold = self.hold > 0
            if self.hold:
                self.hold -= 1
            sda = 0 if (m_sda_low or self.sda_low) else 1
            scl = 0 if (m_scl_low or scl_hold) else 1
            d.uio_in.value = (sda << self.SDA) | (scl << self.SCL)

            if scl and pscl and psda and not sda:
                self.events.append("S")
                state, byte, bits = "addr", 0, 0
                self.sda_low = False
            elif scl and pscl and not psda and sda:
                self.events.append("P")
                state = "idle"
                self.sda_low = False
            elif scl and not pscl:                         # SCL rising: sample
                if state in ("addr", "wdata"):
                    byte, bits = (byte << 1) | sda, bits + 1
                elif state == "mack":
                    self.master_acks.append(sda == 0)
            elif pscl and not scl:                         # SCL falling: drive
                if state in ("addr", "wdata") and bits == 8:
                    if state == "addr":
                        ok = (byte >> 1) == self.addr
                        mode = "r" if byte & 1 else "w"
                    else:
                        ok = True
                        self.written.append(byte)
                    state = "ack" if ok else "nack"
                    self.sda_low = ok
                elif state == "ack":
                    self.sda_low = False
                    if mode == "r":
                        state, bits = "rdata", 0
                        byte = self.read_data.pop(0) if self.read_data else 0xFF
                        self.sda_low = not ((byte >> 7) & 1)
                    else:
                        state, byte, bits = "wdata", 0, 0
                elif state == "rdata":
                    bits += 1
                    if bits < 8:
                        self.sda_low = not ((byte >> (7 - bits)) & 1)
                    else:
                        self.sda_low = False
                        state = "mack"
                elif state == "mack":
                    if self.master_acks and self.master_acks[-1]:
                        state, bits = "rdata", 0
                        byte = self.read_data.pop(0) if self.read_data else 0xFF
                        self.sda_low = not ((byte >> 7) & 1)
                    else:
                        state = "idle"
                elif state == "nack":
                    state = "idle"
            psda, pscl = sda, scl


def i2c_data(addr, write=(), nread=0):
    d = {0: addr, 1: len(write)}
    d.update({2 + i: b for i, b in enumerate(write)})
    d[2 + len(write)] = nread
    return d


async def i2c_run(dut, addr, write=(), nread=0, slave_addr=0x50, rdata=(), stretch=0, seed=0):
    await fw_boot(dut, "i2c_master.s", data=i2c_data(addr, write, nread), uio_in=0x60)
    slave = I2cSlave(dut, slave_addr, rdata, stretch, seed)
    rec = Recorder(dut)
    await rec.until_uo(lambda v: v & 0x80, 200000)
    await ClockCycles(dut.clk, 50)
    return slave, int(dut.uo_out.value)


@cocotb.test()
async def test_fw_i2c_write_ack(dut):
    slave, st = await i2c_run(dut, 0x50, write=[0x12, 0xA5, 0xFF])
    assert st == 0x83, f"status {st:#x}"
    assert slave.written == [0x12, 0xA5, 0xFF]
    assert slave.events == ["S", "P"]


@cocotb.test()
async def test_fw_i2c_address_nack(dut):
    slave, st = await i2c_run(dut, 0x51, write=[0x12])
    assert st == 0x80, f"status {st:#x}: address must be NACKed"
    assert slave.written == [] and slave.events == ["S", "P"]


@cocotb.test()
async def test_fw_i2c_write_then_read_repeated_start(dut):
    slave, st = await i2c_run(dut, 0x50, write=[0x00], nread=4, rdata=[0xDE, 0xAD, 0xBE, 0xEF])
    assert st == 0x83, f"status {st:#x}"
    assert [sram(dut, 64 + i) for i in range(4)] == [0xDE, 0xAD, 0xBE, 0xEF]
    assert slave.master_acks == [True, True, True, False], "ACK all but the last byte"
    assert slave.events == ["S", "S", "P"], "repeated START"
    save_trace("i2c", Recorder.last.pad, "i2c:scl=6:sda=5",
               "i2c=start:repeat-start:stop:ack:nack:address-read:address-write:data-read:data-write",
               ["Start", "Write", "Address write: 50", "ACK", "Data write: 00", "ACK",
                "Start repeat", "Read", "Address read: 50", "ACK", "Data read: DE", "ACK",
                "Data read: AD", "ACK", "Data read: BE", "ACK", "Data read: EF", "NACK", "Stop"],
               {"pin": 5, "hold": 120})


@cocotb.test()
async def test_fw_i2c_constrained_random_with_stretching(dut):
    rng = random.Random(2027)
    for k in range(6):
        addr = rng.randrange(0x08, 0x78)
        hit = rng.random() < 0.7
        wr = [rng.randrange(256) for _ in range(rng.randrange(1, 4))]
        rd = [rng.randrange(256) for _ in range(rng.randrange(0, 3))]
        slave, st = await i2c_run(dut, addr, wr, len(rd), slave_addr=addr if hit else addr ^ 1,
                                  rdata=rd, stretch=40, seed=k)
        if hit:
            assert st == 0x83 and slave.written == wr, f"case {k}: {st:#x} {slave.written} {wr}"
            assert [sram(dut, 64 + i) for i in range(len(rd))] == rd
        else:
            assert st == 0x80 and slave.written == []


# ============================================================================
# USB low speed
# ============================================================================
J, K, SE0 = "J", "K", "0"


def usb_state(v):
    dm, dp = v & 1, (v >> 1) & 1
    return SE0 if (dm, dp) == (0, 0) else J if (dm, dp) == (1, 0) else K if (dm, dp) == (0, 1) else "1"


@cocotb.test()
async def test_fw_usb_ls_tx_packet(dut):
    pid = 0xC3                                  # DATA0
    payload = bytes([0x80, 0x06, 0x00, 0x01, 0xFF, 0xFF, 0x12, 0x00])   # incl. a 1-run
    data = {0: pid, 1: len(payload)}
    data.update({2 + i: b for i, b in enumerate(payload)})
    await fw_boot(dut, "usb_ls_tx.s", data=data)
    rec = Recorder(dut)
    await rec.until_uo(lambda v: v == DONE, 20000)
    states = [usb_state(v) if (oe & 3) == 3 else "z" for v, oe in zip(rec.out, rec.oe)]
    start = states.index(K)
    # every line change sits on the 8-cycle bit grid
    edges = [i for i in range(start + 1, len(states)) if states[i] != states[i - 1]]
    assert all((e - start) % 8 == 0 for e in edges), "bit timing off-grid"
    # NRZI decode at bit centres until SE0
    bits, prev, i = [], J, start + 4
    while states[i] in (J, K):
        bits.append(1 if states[i] == prev else 0)
        prev = states[i]
        i += 8
    eop = i - 4
    # de-stuff and check
    out, ones, skip = [], 0, False
    for b in bits:
        if skip:
            assert b == 0, "stuffed bit must be 0"
            skip, ones = False, 0
            continue
        out.append(b)
        ones = ones + 1 if b else 0
        skip = ones == 6
    nbytes = len(out) // 8
    by = [sum(out[8 * k + j] << j for j in range(8)) for k in range(nbytes)]
    crc = usb_crc16(payload)
    assert by == [0x80, pid] + list(payload) + [crc & 0xFF, crc >> 8], [hex(x) for x in by]
    se0 = 0
    while states[eop + se0] == SE0:
        se0 += 1
    assert se0 == 16, f"SE0 must last 2 bit times (16 cycles), got {se0}"
    j = 0
    while states[eop + se0 + j] == J:
        j += 1
    assert j == 8, f"J after EOP must last one bit (8 cycles), got {j}"
    assert states[eop + se0 + j] == "z", "pins released after the EOP"
    save_trace("usb", rec.out, "usb_signalling:dm=0:dp=1:signalling=low-speed,usb_packet:signalling=low-speed",
               "usb_packet=sync-ok:pid:data:crc16-ok",
               ["SYNC: 00000001", "PID: DATA0"] + [f"Databyte: {b:02X}" for b in payload]
               + [f"CRC16: 0x{crc:04X}"], {"pin": 0, "hold": 8})


def usb_levels(pkt_bytes, corrupt=False):
    bits = [(b >> i) & 1 for b in pkt_bytes for i in range(8)]
    st, ones = [], 0
    for b in bits:
        st.append(b)
        ones = ones + 1 if b else 0
        if ones == 6:
            st.append(0)
            ones = 0
    if corrupt:
        st[40] ^= 1
    lv, cur = [], 1                                  # D- level, J = 1
    for b in st:
        cur = cur if b else 1 - cur
        lv.append(cur)
    return lv


@cocotb.test()
async def test_fw_usb_ls_rx_packet(dut):
    payload = bytes(random.Random(9).randrange(256) for _ in range(8)) + b"\xff\xff"
    crc = usb_crc16(payload)
    pkt = bytes([0x80, 0x4B]) + payload + bytes([crc & 0xFF, crc >> 8])
    for corrupt, ppm in ((False, 0), (False, 15000), (False, -15000), (True, 0)):
        await fw_boot(dut, "usb_ls_rx.s", uio_in=0b01)           # J
        rec = Recorder(dut)
        await rec.until_uo(lambda v: v == 0x40, 2000)
        await ClockCycles(dut.clk, 40)
        p = 8 * (1 + ppm / 1e6)
        levels = usb_levels(pkt, corrupt) + ["se0", "se0", "j", "j"]
        t0 = 0
        for k, lv in enumerate(levels):
            n = round((k + 1) * p) - round(k * p)
            dut.uio_in.value = 0b00 if lv == "se0" else 0b01 if lv in ("j", 1) else 0b10
            await ClockCycles(dut.clk, n)
        dut.uio_in.value = 0b01
        await rec.until_uo(lambda v: v & 0x80, 3000)
        st = rec.uo[-1]
        if corrupt:
            assert st == 0x82, f"corrupted packet accepted ({st:#x})"
        else:
            assert st == 0x81, f"ppm={ppm}: status {st:#x}"
            got = [sram(dut, 64 + i) for i in range(sram(dut, 63))]
            assert got == list(pkt), f"ppm={ppm}: {[hex(x) for x in got]}"


# ============================================================================
# 10BASE-T
# ============================================================================
@cocotb.test()
async def test_fw_10baset_frame_fcs_tpidl_nlp(dut):
    payload = b"msfcore 10BASE-T: bit-banged by a 16-bit protocol CPU!"[:46].ljust(46, b"\0")
    frame = bytes([0xFF] * 6 + [0x02, 0, 0, 0, 0, 0x01] + [0x88, 0xB5]) + payload
    words = [frame[i] | frame[i + 1] << 8 for i in range(0, len(frame), 2)]
    data = {0: len(words)}
    data.update({1 + i: w for i, w in enumerate(words)})
    chunk, chunks = 3000, 2                         # scaled NLP interval for sim speed
    await fw_boot(dut, "eth_10baset_tx.s", data=data,
                  defines={"NLP_CHUNK": chunk, "NLP_CHUNKS": chunks})
    rec = Recorder(dut)
    await rec.until_uo(lambda v: v == 2, 30000)     # two link pulses sent
    tdp = [(v >> 2) & 1 for v in rec.out]
    tdm = [(v >> 3) & 1 for v in rec.out]
    start = next(i for i in range(len(tdp)) if tdp[i] or tdm[i])
    # Manchester decode: pairs of half-bits (1 cycle each) until TP_IDL
    bits, i = [], start
    while True:
        a, b = tdp[i], tdp[i + 1]
        assert tdm[i] == 1 - a and tdm[i + 1] == 1 - b, f"TD- not complementary at {i}"
        if a == b:
            break
        bits.append(b)
        i += 2
    by = bytes(sum(bits[8 * k + j] << j for j in range(8)) for k in range(len(bits) // 8))
    assert len(bits) % 8 == 0
    assert by[:7] == b"\x55" * 7 and by[7] == 0xD5, "preamble / SFD"
    assert by[8:-4] == frame
    assert int.from_bytes(by[-4:], "little") == eth_fcs(frame), "FCS"
    tp = 0
    while tdp[i + tp] == 1 and tdm[i + tp] == 0:
        tp += 1
    assert tp == 6, f"TP_IDL = {tp} cycles (300 ns at 20 MHz)"
    assert not any(tdp[i + tp:i + tp + 100]) and not any(tdm[i + tp:i + tp + 100])
    pulses = [k for k in range(i + tp, len(tdp)) if tdp[k] and not tdp[k - 1]]
    widths = []
    for p in pulses:
        w = 0
        while tdp[p + w]:
            w += 1
        widths.append(w)
    assert widths == [2, 2], f"NLP width {widths} (100 ns = 2 cycles)"
    assert pulses[1] - pulses[0] == chunk * chunks, "NLP period"


# ============================================================================
# Two hardware threads
# ============================================================================
@cocotb.test()
async def test_fw_threads_blink_and_echo(dut):
    await fw_boot(dut, "thread_demo.s", uio_in=0x02)
    rec = Recorder(dut)
    await ClockCycles(dut.clk, 300)
    msg = b"hi!"
    for b in msg:
        await uart_send(dut, b, bit_cycles=104)
    await ClockCycles(dut.clk, 2500)
    assert bytes(uart_decode(rec.out, 104, pin=0)) == msg, "T1 echo"
    b7 = [(v >> 7) & 1 for v in rec.out]
    edges = [k for k in range(1, len(b7)) if b7[k] != b7[k - 1]]
    assert len(edges) > 10
    assert {y - x for x, y in zip(edges, edges[1:])} == {500}, "T0 square wave must stay exact"
