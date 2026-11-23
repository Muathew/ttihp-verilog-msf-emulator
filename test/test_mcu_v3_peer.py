# SPDX-License-Identifier: Apache-2.0
"""The v3 board test in simulation: firmware/mcu_v3_peer.s on the RTL against the
LP-MSPM0G3519's side of it, with the MCU's own waveform code. mcu_ext/protocol_tests/
v3_lines.c is built on the PC and makes every SPI word the MCU will send; this file
plays them into MOSI (uio[6]) as the MCU's SPI would, at 16 Mbit/s from a clock that
is not ours (here 1.3 % fast, then 1.7 % slow: the LaunchPad runs on its internal
oscillator), and does what v3_test.c does with the answers: it decodes the FPGA's
UART (uio[1], 115200), checks each verdict, and reads the guarded pin as the MCU
does, with a pull-up and then a pull-down. The core runs at 12 MHz, as on the
FPGA, and in one more pass with the 48 MHz bitstream's image (CLK_SHIFT = 2) at
four times as many cycles a microsecond. The order of the calls into v3_lines.c is v3_test.c's, so the frames, packets
and phases are the ones the board will see.

What it cannot show: how the MCU's SPI really spaces its words (v3_test.c times
every stream and calls one with gaps not valid), and the MCU's pin and DriverLib
setup (checked against the SDK's headers by mcu_ext/tests/run_compile_test.sh)."""
import ctypes
import os
import random
import subprocess

import cocotb
from cocotb.triggers import ClockCycles, ReadOnly, Timer

from fw_tb import fw_boot
from msf_tb import CLK_NS
from pins import now

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "mcu_ext", "protocol_tests")
UART_BIT = 104                         # at 12 MHz; x CLK_X at 12 MHz x CLK_X
TXP, LINEP, SCLP, POCIP = 1, 6, 2, 7

ETH = [(64, 0, 0), (64, 100, 0), (64, -100, 0), (1518, -100, 0), (64, 0, 1)]   # v3_test.c
USB = [(0, 0), (2500, 0), (-2500, 0), (0, 1)]
LATE = {"B": 0x8011, "C": 0x8010, "D": 0x8021}

_lib = None


def lines():
    """v3_lines.c as a library, built once per run."""
    global _lib
    if _lib is None:
        out = os.path.join(HERE, "sim_build", "v3lines")
        os.makedirs(out, exist_ok=True)
        # one per process: Windows keeps a loaded library locked
        dll = os.path.join(out, f"v3_lines_{os.getpid()}" + (".dll" if os.name == "nt" else ".so"))
        subprocess.run(["gcc", "-O2", "-shared", "-static-libgcc", "-fPIC", "-o", dll,
                        os.path.join(SRC, "v3_lines.c")], check=True)
        lib = ctypes.CDLL(dll)
        u32, p8, p16, pu32 = (ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint8),
                              ctypes.POINTER(ctypes.c_uint16), ctypes.POINTER(ctypes.c_uint32))
        sig = {"v3_cal_words": (u32, [p16]),
               "v3_symbol_fp": (u32, [u32, u32, ctypes.c_int32]),
               "v3_rand": (u32, [pu32]),
               "v3_eth_frame": (u32, [p8, u32, pu32, ctypes.c_int]),
               "v3_usb_packet": (None, [p8, pu32, ctypes.c_int]),
               "v3_eth_words": (u32, [p8, u32, u32, u32, p16, u32]),
               "v3_usb_words": (u32, [p8, u32, u32, p16, u32])}
        for name, (res, args) in sig.items():
            getattr(lib, name).restype = res
            getattr(lib, name).argtypes = args
        _lib = lib
    return _lib


MAX_WORDS = 5800


class Mcu:
    """v3_test.c's side: its SPI on MOSI, its UART receive, its pins. clk_shift: the
    core at 12 MHz << clk_shift, so x core cycles stand for what one did at 12 MHz."""

    def __init__(self, dut, clock_error, seed, clk_shift=0):
        self.dut = dut
        self.x = 1 << clk_shift
        self.q = 0.75 * self.x / (1 + clock_error)   # core cycles a SPI bit (2 MCU cycles)
        self.uart_bit = UART_BIT * self.x
        self.rx = []
        self.events = []                       # what the A53 would decode from uo_out
        self.rnd = random.Random(seed)
        self.rng = ctypes.c_uint32(0x4D534633)
        self.words = (ctypes.c_uint16 * MAX_WORDS)()
        self.line = 0
        cocotb.start_soon(self._uart())
        cocotb.start_soon(self._uo())

    def _tx(self):
        d = self.dut
        return ((int(d.uio_out.value) >> TXP) & 1) if (int(d.uio_oe.value) >> TXP) & 1 else 1

    async def _uart(self):
        """The MCU's UART1 receiver: 8N1 at the FPGA's bit time, sampled mid-bit."""
        while True:
            await self.dut.uio_out.value_change
            await ReadOnly()
            if self._tx():
                continue
            await ClockCycles(self.dut.clk, self.uart_bit // 2)
            await ReadOnly()
            if self._tx():
                continue                       # not a start bit
            b = 0
            for i in range(8):
                await ClockCycles(self.dut.clk, self.uart_bit)
                await ReadOnly()
                b |= self._tx() << i
            await ClockCycles(self.dut.clk, self.uart_bit)
            await ReadOnly()
            assert self._tx() == 1, "UART framing error"
            self.rx.append(b)

    async def _uo(self):
        """mcu_protocol_target.s's event format, as fpga/ps/mcu_v3 decodes it."""
        last, first = None, None
        while True:
            await self.dut.uo_out.value_change
            await ReadOnly()
            v = int(self.dut.uo_out.value)
            if last is not None and not ((v ^ last) & 0x80):
                last = v
                continue
            last = v
            if v & 0x40:
                first = v & 0x3F
            elif first is not None:
                word = first << 6 | (v & 0x3F)
                assert word >> 8 == 1, f"event code {word >> 8}"
                self.events.append(word & 0xFF)
                first = None

    async def get(self, timeout):
        """The next byte from the FPGA, within timeout cycles of 12 MHz."""
        t0 = now()
        while not self.rx:
            assert now() - t0 < timeout * self.x, "no byte from the FPGA"
            await Timer(50 * CLK_NS, unit="ns")    # not ClockCycles: one wake-up, not 50
        return self.rx.pop(0)

    async def wait_for(self, want, timeout=400_000 * 12):
        while True:
            if await self.get(timeout) == ord(want):
                return

    async def stream(self, words):
        """The SPI: MSB first, one bit every q core cycles, from a random phase. A
        pin change is seen from the first clock edge after it."""
        bits = []
        for w in words:
            bits += [(w >> (15 - i)) & 1 for i in range(16)]
        await ClockCycles(self.dut.clk, self.rnd.randrange(300, 900) * self.x)   # the MCU reacting
        t, c = self.rnd.random(), 0
        while True:
            k = int((c + t) / self.q)
            if k >= len(bits):
                break
            v = bits[k]
            if v != self.line:
                self.line = v
                self.dut.uio_in.value = v << LINEP
            await ClockCycles(self.dut.clk, 1)
            c += 1
        self.line = 0
        self.dut.uio_in.value = 0

    def pins(self, pull):
        d = self.dut
        out, oe = int(d.uio_out.value), int(d.uio_oe.value)
        poci = (out >> POCIP) & 1 if (oe >> POCIP) & 1 else pull
        scl = (out >> SCLP) & 1 if (oe >> SCLP) & 1 else 1           # LaunchPad pull-up
        return poci, scl


async def one_pass(dut, clock_error, seed, clk_shift=0):
    lib = lines()
    # no late_ok: the two deadlines this program misses are marked `@late`, so the watch
    # checks that the RTL misses exactly those, and they count in the RTL cross-check
    await fw_boot(dut, "mcu_v3_peer.s", defines={"CLK_SHIFT": clk_shift} if clk_shift else None)
    mcu = Mcu(dut, clock_error, seed, clk_shift)
    await mcu.wait_for("S")
    n = lib.v3_cal_words(mcu.words)
    await mcu.stream(mcu.words[:n])
    await mcu.wait_for("s")
    t = await mcu.get(20_000) | await mcu.get(20_000) << 8
    want_t = 512 * 32 * mcu.q / mcu.x          # T comes back in 12 MHz cycles
    assert abs(t - want_t) <= 2, f"calibration T {t}, want {want_t:.1f}"
    frame = (ctypes.c_uint8 * 1518)()
    for i, (length, ppm, corrupt) in enumerate(ETH):
        sym = lib.v3_symbol_fp(t, 5, ppm)
        lib.v3_eth_frame(frame, length, ctypes.byref(mcu.rng), corrupt)
        nw = lib.v3_eth_words(frame, length, sym, lib.v3_rand(ctypes.byref(mcu.rng)) & 0xFFFF,
                              mcu.words, MAX_WORDS)
        assert nw > 0
        await mcu.wait_for("M")
        await mcu.stream(mcu.words[:nw])
        await mcu.wait_for("m")
        st, lo, hi = [await mcu.get(20_000) for _ in range(3)]
        assert (st, lo | hi << 8) == (0x82 if corrupt else 0x81, length), \
            f"frame {i} ({length} bytes, {ppm} ppm): status {st:#x}, length {lo | hi << 8}"
    packet = (ctypes.c_uint8 * 36)()
    for i, (ppm, corrupt) in enumerate(USB):
        sym = lib.v3_symbol_fp(t, 8, ppm)
        lib.v3_usb_packet(packet, ctypes.byref(mcu.rng), corrupt)
        nw = lib.v3_usb_words(packet, sym, lib.v3_rand(ctypes.byref(mcu.rng)) & 0xFFFF,
                              mcu.words, MAX_WORDS)
        await mcu.wait_for("U")
        await mcu.stream(mcu.words[:nw])
        await mcu.wait_for("u")
        got = [await mcu.get(20_000) for _ in range(4)]
        assert got == [0x82 if corrupt else 0x81, 36, 0x80, 0x4B], \
            f"packet {i} ({ppm} ppm): {[hex(x) for x in got]}"
    for k in "ABCD":
        await mcu.wait_for(k)
        late = None if k == "A" else await mcu.get(20_000) | await mcu.get(20_000) << 8
        await ClockCycles(dut.clk, 2000 * mcu.x)   # the MCU switching its pulls
        up, scl = mcu.pins(1)
        down, _ = mcu.pins(0)
        assert (up, down, scl) == ((1, 0, 0) if k == "B" else (0, 0, 0)), \
            f"guard {k}: POCI with pull-up {up}, pull-down {down}, SCL {scl}"
        assert late == LATE.get(k), f"guard {k}: LATE_RD {late}"
    await mcu.wait_for("P")
    await mcu.wait_for("S")                    # and the next pass begins
    sent = mcu.events
    assert len(sent) > 40 and sent[0] == ord("S") and ord("P") in sent, sent[:8]
    return t


@cocotb.test()
async def test_mcu_v3_pass_fast_mcu(dut):
    """A whole pass with the MCU's clock 1.3 % fast: calibration finds it, the five
    frames and four packets get the right verdicts, the guard takes UIO7 off the bus
    only while its own thread is late."""
    await one_pass(dut, 0.013, 1)


@cocotb.test()
async def test_mcu_v3_pass_slow_mcu(dut):
    """The same with the MCU's clock 1.7 % slow."""
    await one_pass(dut, -0.017, 2)


@cocotb.test()
async def test_mcu_v3_pass_48mhz(dut):
    """The image for the 48 MHz bitstream (CLK_SHIFT = 2), at four core cycles for
    each one of 12 MHz, against the same MCU code, its clock 1.7 % slow: the UART
    still at 115200, the frames and packets at 20 and 16 cycles a bit, T back in
    12 MHz cycles so that v3_test.c accepts it, and the same verdicts."""
    await one_pass(dut, -0.017, 3, clk_shift=2)
