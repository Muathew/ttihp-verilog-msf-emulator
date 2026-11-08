# SPDX-License-Identifier: Apache-2.0
"""nRF24L01+ transmitter set-up (firmware/nrf24.s) against a radio written from Nordic's
product specification: SPI mode 0 (MOSI taken on SCK's rising edge, MISO moved on the
falling edge), MSB first, a command per CSN-low transaction with STATUS clocked out
during the command byte; W_REGISTER, W_TX_PAYLOAD, FLUSH_TX, NOP; a CE pulse of 10 us
or more, 1.5 ms or more after PWR_UP, sends the payload, and TX_DS (STATUS bit 5) is
set when the auto-acknowledge comes back (here 400 us later); writing 1s to STATUS
clears its flags."""
import cocotb
from cocotb.triggers import RisingEdge

from fw_tb import fw_boot, save_trace
from pins import samples

CE, SCK, MOSI, MISO, CSN = 0x01, 0x04, 0x08, 0x10, 0x20


class Radio:
    def __init__(self, dut):
        self.dut, self.t = dut, 0
        self.regs = {0x00: 0x08, 0x01: 0x3F, 0x04: 0x03, 0x05: 0x02, 0x06: 0x0E, 0x07: 0x0E}
        self.addr = {0x0A: [0xE7] * 5, 0x10: [0xE7] * 5}
        self.tx, self.sent, self.cmds = [], [], []
        self.pwr_up_at, self.ce_from, self.ack_at = None, None, None
        self.changes = [(0, CSN)]
        cocotb.start_soon(self.run())

    def status(self):
        return self.regs[0x07]

    def end(self, frame):
        if not frame:
            return
        c, data = frame[0], frame[1:]
        self.cmds.append(c)
        if c & 0xE0 == 0x20:                         # W_REGISTER
            r = c & 0x1F
            if r in self.addr:
                assert len(data) == 5, "an address is 5 bytes"
                self.addr[r] = data
            elif r == 0x07:
                self.regs[7] &= ~(data[0] & 0x70)
            else:
                if r == 0x00 and data[0] & 2 and not self.regs[0] & 2:
                    self.pwr_up_at = self.t
                self.regs[r] = data[0]
        elif c == 0xA0:
            self.tx.append(data)
        elif c == 0xE1:
            self.tx = []

    async def run(self):
        d, last = self.dut, None
        pcsn, psck, pce = 1, 0, 0
        frame, sr, n, out = [], 0, 0, 0
        while True:
            await RisingEdge(d.clk)
            self.t += 1
            o, oe = int(d.uio_out.value), int(d.uio_oe.value)
            csn = (o >> 5) & 1 if oe & CSN else 1
            sck = (o >> 2) & 1 if oe & SCK else 0
            mosi = (o >> 3) & 1
            ce = o & 1 if oe & CE else 0
            if not csn and pcsn:                     # a transaction: STATUS goes out
                frame, sr, n, out = [], 0, 0, self.status()
            if csn and not pcsn:
                self.end(frame)
            if not csn and sck and not psck:
                sr, n = (sr << 1 | mosi) & 0xFF, n + 1
                if n == 8:
                    frame.append(sr)
                    n, out = 0, 0
            if not csn and psck and not sck:
                out = (out << 1) & 0xFF
            if ce and not pce:
                self.ce_from = self.t
            if pce and not ce:
                assert self.t - self.ce_from >= 120, "CE pulse under 10 us"
                assert self.pwr_up_at is not None and self.ce_from - self.pwr_up_at >= 18000, \
                    "CE under 1.5 ms after PWR_UP"
                assert self.tx, "CE with no payload"
                self.sent.append((self.regs[5], list(self.addr[0x10]), self.tx.pop(0)))
                self.ack_at = self.t + 4800
            if self.ack_at is not None and self.t >= self.ack_at:
                self.regs[7] |= 0x20                 # TX_DS
                self.ack_at = None
            miso = (out >> 7) & 1 if not csn else 0
            d.uio_in.value = miso << 4
            pads = (o & (CE | SCK | MOSI | CSN)) | miso << 4
            if pads != last:
                self.changes.append((self.t, pads))
                last = pads
            pcsn, psck, pce = csn, sck, ce


@cocotb.test()
async def test_nrf24_send(dut):
    """Configured, one 4-byte payload sent on channel 76 to E7E7E7E7E7, TX_DS seen and cleared."""
    await fw_boot(dut, "nrf24.s")
    radio = Radio(dut)
    for _ in range(500):
        await RisingEdge(dut.clk)
    while int(dut.uo_out.value) not in (0x55, 0xEE):
        for _ in range(500):
            await RisingEdge(dut.clk)
        assert radio.t < 100_000, "no result"
    assert int(dut.uo_out.value) == 0x55
    assert radio.regs[0] == 0x0E and radio.regs[1] == 0x01 and radio.regs[6] == 0x06
    assert radio.sent == [(76, [0xE7] * 5, [0x4D, 0x53, 0x46, 0x33])], radio.sent
    assert radio.regs[7] & 0x70 == 0, "flags cleared"
    mem = dut.user_project.u_sram.mem
    assert int(mem[64].value) & 0x20
    t1 = radio.changes[-1][0] + 50
    save_trace("nrf24", samples(radio.changes, 0, t1, 1), "spi:clk=2:mosi=3:miso=4:cs=5,nrf24l01",
               "nrf24l01=commands",
               ['Cmd W_REGISTER: CONFIG = "0E"', 'Cmd W_REGISTER: EN_AA = "01"',
                'Cmd W_REGISTER: SETUP_RETR = "3F"', 'Cmd W_REGISTER: RF_CH = "4C"',
                'Cmd W_REGISTER: RF_SETUP = "06"', 'Cmd W_REGISTER: TX_ADDR = "E7E7E7E7E7"',
                'Cmd W_REGISTER: RX_ADDR_P0 = "E7E7E7E7E7"', "Cmd FLUSH_TX", "Cmd W_TX_PAYLOAD",
                'TX payload = "MSF3"'] + ["Cmd NOP"] * radio.cmds.count(0xFF)
               + ['Cmd W_REGISTER: STATUS = "30"'], {"pin": 2, "hold": 3})
