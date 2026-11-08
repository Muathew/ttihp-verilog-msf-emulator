# SPDX-License-Identifier: Apache-2.0
"""An I2C device on the chip's I2C pins (SDA uio[5], SCL uio[6], open drain with
pull-ups), with hooks for what the device does with the bytes: the bus part follows
the I2C-bus specification (NXP UM10204): a START is SDA falling while SCL is high, a
STOP SDA rising while SCL is high, bits are taken on SCL's rising edge and changed
while SCL is low, the ninth clock is the acknowledge."""
from cocotb.triggers import ReadOnly, RisingEdge, Timer

from fw_tb import spawn


class I2cDevice:
    SDA, SCL = 5, 6

    def __init__(self, dut, addr):
        self.dut, self.addr = dut, addr
        self.events, self.t, self.changes = [], 0, [(0, 0x60)]
        self.sda_low = False
        spawn(self._run())

    # ---- what the device does (override) ----
    def ack_address(self, read):
        return True

    def begin(self, read):
        """A transaction to this device starts."""

    def write(self, b):
        """A byte written to the device (after its address)."""

    def read(self):
        """The next byte the device sends."""
        return 0xFF

    def end(self):
        """STOP (or a repeated START)."""

    # ---- the bus ----
    async def _run(self):
        d = self.dut
        psda = pscl = 1
        state, byte, bits, mine, last = "idle", 0, 0, False, None
        while True:
            await RisingEdge(d.clk)
            await ReadOnly()
            self.t += 1
            oe = int(d.uio_oe.value)
            m_sda_low, m_scl_low = bool((oe >> self.SDA) & 1), bool((oe >> self.SCL) & 1)
            await Timer(1, unit="ns")
            sda = 0 if (m_sda_low or self.sda_low) else 1
            scl = 0 if m_scl_low else 1
            d.uio_in.value = (sda << self.SDA) | (scl << self.SCL)
            pads = (sda << self.SDA) | (scl << self.SCL)
            if pads != last:
                self.changes.append((self.t, pads))
                last = pads
            if scl and pscl and psda and not sda:              # START
                if mine:
                    self.end()
                self.events.append("S")
                state, byte, bits, mine = "addr", 0, 0, False
                self.sda_low = False
            elif scl and pscl and not psda and sda:            # STOP
                if mine:
                    self.end()
                self.events.append("P")
                state, mine = "idle", False
                self.sda_low = False
            elif scl and not pscl:                             # rising: take
                if state in ("addr", "wdata"):
                    byte, bits = (byte << 1) | sda, bits + 1
                elif state == "mack":
                    self.macked = sda == 0
            elif pscl and not scl:                             # falling: drive
                if state in ("addr", "wdata") and bits == 8:
                    if state == "addr":
                        self.mode = "r" if byte & 1 else "w"
                        ok = (byte >> 1) == self.addr and self.ack_address(self.mode == "r")
                        if ok:
                            mine = True
                            self.begin(self.mode == "r")
                    else:
                        ok = True
                        self.write(byte)
                    state = "ack" if ok else "nack"
                    self.sda_low = ok
                elif state == "ack":
                    self.sda_low = False
                    if self.mode == "r":
                        state, bits, byte = "rdata", 0, self.read()
                        self.sda_low = not ((byte >> 7) & 1)
                    else:
                        state, byte, bits = "wdata", 0, 0
                elif state == "rdata":
                    bits += 1
                    if bits < 8:
                        self.sda_low = not ((byte >> (7 - bits)) & 1)
                    else:
                        self.sda_low, state = False, "mack"
                elif state == "mack":
                    if self.macked:
                        state, bits, byte = "rdata", 0, self.read()
                        self.sda_low = not ((byte >> 7) & 1)
                    else:
                        state = "idle"
                elif state == "nack":
                    state = "idle"
            psda, pscl = sda, scl
