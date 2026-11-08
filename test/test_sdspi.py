# SPDX-License-Identifier: Apache-2.0
"""SD card start-up and block read (firmware/sdspi.s) against a card written from the SD
Physical Layer Simplified Specification v9.00, chapter 7 (SPI mode): SPI mode 0; at
least 74 clocks with CS and MOSI high first; 6-byte commands, CRC7 checked for CMD0 and
CMD8; R1 a byte after the command (NCR); CMD8 echoes 0x1AA in R7; ACMD41 (CMD55, CMD41)
leaves idle on its third try; CMD58 gives the OCR (powered up, CCS = 1); CMD17 sends
two NAC bytes of 0xFF, the token 0xFE, 512 bytes and their CRC-16."""
import cocotb
from cocotb.triggers import RisingEdge

from fw_tb import fw_boot, save_trace
from pins import samples

SCK, MOSI, MISO, CS = 0x04, 0x08, 0x10, 0x20
OCR = 0xC0FF8000
BLOCK = list(b"MSFCORE SD BLOCK") + [(7 * i + 3) & 0xFF for i in range(16, 512)]


def crc7(bs):
    c = 0
    for b in bs:
        for i in range(7, -1, -1):
            c = (c << 1) | ((b >> i) & 1)
            if c & 0x80:
                c ^= 0x89
    for _ in range(7):
        c <<= 1
        if c & 0x80:
            c ^= 0x89
    return c & 0x7F


def crc16(bs):
    c = 0
    for b in bs:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021 if c & 0x8000 else c << 1) & 0xFFFF
    return c


class Card:
    def __init__(self, dut):
        self.dut, self.t, self.pre_clocks, self.idle, self.app = dut, 0, 0, True, False
        self.a41, self.cmds, self.out = 0, [], []
        self.changes = [(0, CS | MOSI)]
        cocotb.start_soon(self.run())

    def command(self, c):
        idx, arg = c[0] & 0x3F, int.from_bytes(bytes(c[1:5]), "big")
        assert c[0] >> 6 == 1 and c[5] & 1, f"command framing {c}"
        if idx in (0, 8):
            assert c[5] >> 1 == crc7(c[:5]), f"CMD{idx} CRC7"
        self.cmds.append(("A" if self.app else "") + f"CMD{idx}")
        r1 = 0x01 if self.idle else 0x00
        resp = []
        if self.app and idx == 41:
            assert arg & (1 << 30), "ACMD41 without HCS"
            self.a41 += 1
            if self.a41 >= 3:
                self.idle = False
            resp = [0x01 if self.idle else 0x00]
        elif idx == 0:
            assert self.pre_clocks >= 74, f"only {self.pre_clocks} clocks before CMD0"
            self.idle, resp = True, [0x01]
        elif idx == 8:
            resp = [r1, 0x00, 0x00, (arg >> 8) & 0x0F, arg & 0xFF]
        elif idx == 55:
            resp = [r1]
        elif idx == 58:
            resp = [r1] + list(OCR.to_bytes(4, "big"))
        elif idx == 17:
            assert not self.idle and arg == 0
            c16 = crc16(BLOCK)
            resp = [0x00, 0xFF, 0xFF, 0xFE] + BLOCK + [c16 >> 8, c16 & 0xFF]
        self.app = idx == 55
        self.out = [0xFF] + resp                  # NCR: one byte, then the response

    async def run(self):
        d, last = self.dut, None
        pcs, psck, cmd, sr, n, cur = 1, 0, [], 0, 0, 0xFF
        while True:
            await RisingEdge(d.clk)
            self.t += 1
            o, oe = int(d.uio_out.value), int(d.uio_oe.value)
            cs = (o >> 5) & 1 if oe & CS else 1
            sck = (o >> 2) & 1 if oe & SCK else 0
            mosi = (o >> 3) & 1 if oe & MOSI else 1
            if sck and not psck:
                if cs and mosi:
                    self.pre_clocks += 1
                elif not cs:
                    sr, n = (sr << 1 | mosi) & 0xFF, n + 1
            if not cs and psck and not sck:
                if n == 8:                         # a byte done: the next one goes out
                    if cmd or sr != 0xFF:
                        cmd.append(sr)
                    if len(cmd) == 6:
                        self.command(cmd)
                        cmd = []
                    cur = self.out.pop(0) if self.out else 0xFF
                    n = 0
                else:
                    cur = (cur << 1 | 1) & 0xFF
            miso = (cur >> 7) & 1 if not cs else 1
            d.uio_in.value = miso << 4
            pads = (o & (SCK | MOSI | CS)) | miso << 4
            if pads != last:
                self.changes.append((self.t, pads))
                last = pads
            pcs, psck = cs, sck


def sd_lines():
    """sigrok's reading of the exchange (its names for the commands)."""
    c55 = ["CMD55 (APP_CMD): Next command is an application-specific command", "R1: 0x01"]
    a41 = "ACMD41 (SD_SEND_OP_COND): Send HCS info and activate the card init process"
    return (["CMD0 (GO_IDLE_STATE): Reset the SD card", "R1: 0x01",
             "CMD8: 48 00 00 01 aa 87", "R1: 0x01"]
            + c55 + [a41, "R1: 0x01"] + c55 + [a41, "R1: 0x01"] + c55 + [a41, "R1: 0x00"]
            + ["CMD58: 7a 00 00 00 00 fd", "R1: 0x00",
               "CMD17 (READ_SINGLE_BLOCK): Read a block from address 0x0000", "R1: 0x00",
               "Start Block", "Block data: [" + ", ".join(map(str, BLOCK)) + "]", "CRC"])


@cocotb.test()
async def test_sdspi_init_and_read(dut):
    """CMD0, CMD8, ACMD41 x3, CMD58, CMD17: OCR and block 0 kept, the CRC-16 checked."""
    await fw_boot(dut, "sdspi.s")
    card = Card(dut)
    for _ in range(500):
        await RisingEdge(dut.clk)
    while int(dut.uo_out.value) not in (0x55, 0xEE):
        for _ in range(2000):
            await RisingEdge(dut.clk)
        assert card.t < 600_000, "no result"
    assert int(dut.uo_out.value) == 0x55
    assert card.cmds == ["CMD0", "CMD8"] + ["CMD55", "ACMD41"] * 3 + ["CMD58", "CMD17"], card.cmds
    mem = dut.user_project.u_sram.mem
    assert [int(mem[64 + i].value) for i in range(16)] == BLOCK[:16]
    assert [int(mem[80].value), int(mem[81].value)] == [OCR >> 16, OCR & 0xFFFF]
    assert int(mem[82].value) == 3
    t1 = card.changes[-1][0] + 100
    save_trace("sdspi", samples(card.changes, 0, t1, 2), "spi:clk=2:mosi=3:miso=4:cs=5,sdcard_spi",
               "sdcard_spi=cmd-reply", sd_lines(), {"pin": 2, "hold": 25}, rate=6_000_000)
