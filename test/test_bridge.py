# SPDX-License-Identifier: Apache-2.0
"""UART -> SPI bridge (firmware/bridge.s): thread 0 queues UART bytes,
thread 1 runs the SPI transfers they announce and returns the bytes read on
the UART. The SPI device is a model of an 8-bit shift register, so each
byte read back is the byte written before it.
"""
import cocotb

from fw_tb import Bench, fw_boot
from msf_tb import uart_decode
from test_monitors import Wave, uart_wave

BIT = 104                 # bridge.s default: 115200 baud at 12 MHz
RXP, TXP = 1, 0
SCKP, MOSIP, MISOP, CSP = 2, 3, 4, 5


class ShiftRegDevice:
    """SPI device in `mode`: an 8-bit register shifted MSB first, MOSI in,
    MISO out. Records the bytes it received in each transfer (CS low)."""

    def __init__(self, mode, init=0x5A):
        self.cpol, self.cpha = mode >> 1, mode & 1
        self.reg, self.miso = init, (init >> 7) & 1
        self.sck, self.cs = self.cpol, 1
        self.transfers, self.n = [], 0

    def step(self, k, bench):
        out, oe = bench.out[k], bench.oe[k]
        cs = (out >> CSP) & 1 if (oe >> CSP) & 1 else 1     # undriven: high
        sck, mosi = (out >> SCKP) & 1, (out >> MOSIP) & 1
        if cs == 0 and self.cs == 1:          # selected: first bit on MISO
            self.transfers.append([])
            self.n = 0
            self.miso = (self.reg >> 7) & 1
        if cs == 0 and sck != self.sck:
            leading = sck != self.cpol
            if leading == (self.cpha == 0):   # the sampling edge
                self.reg = (self.reg << 1 | mosi) & 0xFF
                self.n += 1
                if self.n % 8 == 0:
                    self.transfers[-1].append(self.reg)
            else:                             # the shifting edge
                self.miso = (self.reg >> 7) & 1
        self.sck, self.cs = sck, cs
        return 1 << MISOP, self.miso << MISOP


async def bridge_run(dut, mode, frames):
    """frames: lists of bytes sent to the bridge (a length, then the data);
    returns (bytes back on the UART, the device's transfers)."""
    items = [("byte", v, BIT, 1, 3 * BIT) for f in frames for v in f]
    line = Wave(1 << RXP, uart_wave(items, pin=RXP), start=400)
    await fw_boot(dut, "bridge.s", defines={"MODE": mode}, uio_in=1 << RXP)
    dev = ShiftRegDevice(mode)
    bench = Bench(dut)
    bench.add(line)
    bench.add(dev)
    await bench.run(line.end + 12 * BIT * 4)
    return uart_decode(bench.out, BIT, pin=TXP), dev.transfers


@cocotb.test()
async def test_bridge_modes(dut):
    """Every SPI mode: transfers of 3, 2 and 1 bytes (and a 0 that is
    ignored): the device sees each transfer's bytes with CS framing them,
    and the UART returns what it shifted out."""
    frames = [[3, 0x11, 0x22, 0xC3], [0], [2, 0xA5, 0x00], [1, 0xFF]]
    sent = [0x11, 0x22, 0xC3, 0xA5, 0x00, 0xFF]
    for mode in range(4):
        back, transfers = await bridge_run(dut, mode, frames)
        assert transfers == [[0x11, 0x22, 0xC3], [0xA5, 0x00], [0xFF]], \
            f"mode {mode}: {transfers}"
        assert back == [0x5A] + sent[:-1], f"mode {mode}: {back}"
