# SPDX-License-Identifier: Apache-2.0
"""
Protocol golden references for the msfcore protocol emulator.

The *expected* bit-level behaviour of each protocol, used as the golden side
of the protocol checks: UART (TX frame / RX decode), SPI master (per-mode
edge placement), I2C (START/STOP/data condition detector), USB 1.0 low speed
(NRZI, bit stuffing, CRC5/CRC16, packet framing) and Ethernet (CRC-32 FCS).

The CPU reference model lives in ``isa_model.py`` (independent ISA-level
model, compared against the RTL by ``diffcheck.py``). The former
cycle-mirror ``MsfCoreModel`` was retired with ISA v2: it copied the RTL
pipeline (including bug B1), so it could not catch spec deviations.

Pure Python -- no simulator dependency.
"""

from __future__ import annotations

import zlib

from isa_model import IsaModel  # noqa: F401  (re-export for convenience)


# ---------------------------------------------------------------------------
# UART reference (115200 baud, 8N1)
# ---------------------------------------------------------------------------
def uart_frame_bits(byte: int) -> list[int]:
    """Expected TX pin level sequence: start(0), 8 data bits LSB-first,
    stop(1).  Index 0 is the start bit."""
    byte &= 0xFF
    return ([0] + [((byte >> i) & 1) for i in range(8)] + [1])


def uart_decode_data_bits(data_bits_lsb_first: list[int]) -> int:
    """Reconstruct a byte from 8 sampled data bits (LSB first)."""
    val = 0
    for i, b in enumerate(data_bits_lsb_first):
        val |= (b & 1) << i
    return val & 0xFF


class UartRxDecoder:
    """Decode a sampled UART RX bit stream (start, 8 data LSB-first, stop)."""

    def __init__(self):
        self.frames: list[int] = []
        self.errors = 0

    def feed(self, start: int, data_bits_lsb_first: list[int], stop: int) -> int | None:
        if start != 0:
            self.errors += 1
            return None
        if stop != 1:
            self.errors += 1
        byte = uart_decode_data_bits(data_bits_lsb_first)
        self.frames.append(byte)
        return byte


# ---------------------------------------------------------------------------
# SPI master reference (per-mode SCK/MOSI edge placement)
# ---------------------------------------------------------------------------
def spi_master_waveform(byte: int, cpol: int = 0, cpha: int = 0) -> list[dict]:
    """Expected (sck, mosi) state sequence for an SPI master byte, MSB-first.

    Returns one dict per bit with the idle level, the data level, and the
    capture edge.  ``cpol``/``cpha`` select the mode:
      mode 0: cpol=0 cpha=0 -> sample on rising edge, data set before it.
      mode 3: cpol=1 cpha=1 -> sample on falling edge, data set after it.
    """
    byte &= 0xFF
    idle = 1 if cpol else 0
    events = []
    for i in range(7, -1, -1):
        data = (byte >> i) & 1
        events.append({
            "bit": i,
            "data": data,
            "sck_idle": idle,
            # The edge on which the slave samples MOSI.
            "sample_edge": "rising" if cpha == 0 else "falling",
        })
    return events


# ---------------------------------------------------------------------------
# I2C reference (open-drain condition detector + master bit timing)
# ---------------------------------------------------------------------------
class I2cConditionDetector:
    """Classic I2C bus condition detector (START/STOP/data).

    Call :meth:`update` once per observed (sda, scl) sample; it returns an
    event string or ``None``.  Works on the *effective* open-drain line
    levels (0 = driven low, 1 = released/pulled high).
    """

    def __init__(self):
        self.sda_prev = 1
        self.scl_prev = 1
        self.events: list[str] = []

    def update(self, sda: int, scl: int) -> str | None:
        """Feed one (sda, scl) sample.  Returns the event, if any.

        Follows the standard I2C bus-state rules:
          * while SCL is high, an SDA transition defines START (1->0)
            or STOP (0->1);
          * on an SCL rising edge, a stable SDA is a data-bit sample,
            while an SDA 0->1 (release) is a STOP.
        """
        sda &= 1
        scl &= 1
        event = None
        scl_rising = self.scl_prev == 0 and scl == 1
        if scl == 1 and self.scl_prev == 1:
            # SCL held high: SDA transitions are bus conditions.
            if self.sda_prev == 1 and sda == 0:
                event = "START"
            elif self.sda_prev == 0 and sda == 1:
                event = "STOP"
        elif scl_rising:
            # SCL rising edge: sample the data bit (or detect STOP release).
            if self.sda_prev == 0 and sda == 1:
                event = "STOP"
            else:
                event = f"bit{sda}"
        self.events.append(event if event else "")
        self.sda_prev = sda
        self.scl_prev = scl
        return event


def i2c_master_bit_waveform(bit: int) -> list[dict]:
    """Expected open-drain (sda, scl) driver actions to send one I2C bit.

    A bit is sent by holding SDA, then pulsing SCL high and low.  ``bit``
    is the logical value (0 = driven low, 1 = released).
    """
    sda_drive = "release" if bit else "drive-low"
    return [
        {"sda": sda_drive, "scl": "low"},
        {"sda": sda_drive, "scl": "high"},
        {"sda": sda_drive, "scl": "low"},
    ]


# ---------------------------------------------------------------------------
# USB 1.0 Low-Speed reference (NRZI + bit-stuffing + packet framing)
# ---------------------------------------------------------------------------
def nrzi_encode(bits_msb_first: list[int], initial_state: int = 0):
    """NRZI-encode a list of logical bits (MSB first).

    Logical 1 -> no transition, logical 0 -> transition.  State 0 = J,
    1 = K.  Returns (states, final_state) where each state is the line
    level for that bit period.
    """
    states = []
    cur = initial_state & 1
    for bit in bits_msb_first:
        if bit == 0:
            cur ^= 1
        states.append(cur)
    return states, cur


def usb_bit_stuff(bits_msb_first: list[int]) -> list[int]:
    """Insert a stuffing 0 after every run of 5 consecutive logical 1s."""
    out = []
    run = 0
    for bit in bits_msb_first:
        out.append(bit)
        if bit == 1:
            run += 1
            if run == 5:
                out.append(0)   # stuffed 0 (forces a transition)
                run = 0
        else:
            run = 0
    return out


def usb_bits_msb_first(byte: int) -> list[int]:
    return [(byte >> i) & 1 for i in range(7, -1, -1)]


def usb_low_packet(sync: int, pid: int, payload: bytes):
    """Reference USB 1.0 low-speed packet: sync + PID + payload, NRZI with
    bit-stuffing.  Returns (nrzi_states, final_state) for the whole field."""
    bits = usb_bits_msb_first(sync) + usb_bits_msb_first(pid)
    for b in payload:
        bits += usb_bits_msb_first(b)
    bits = usb_bit_stuff(bits)
    return nrzi_encode(bits, 0)


# ---------------------------------------------------------------------------
# CRC references (bitwise, straight from the protocol specs)
# ---------------------------------------------------------------------------
def crc_reflected(bits, width, poly_reflected, init):
    """Generic LSB-first CRC over a bit list."""
    crc = init
    for b in bits:
        fb = (crc ^ b) & 1
        crc >>= 1
        if fb:
            crc ^= poly_reflected
    return crc & ((1 << width) - 1)


def bytes_lsb_bits(data):
    return [(byte >> i) & 1 for byte in data for i in range(8)]


def usb_crc16(data: bytes) -> int:
    """USB data CRC16 (x^16+x^15+x^2+1), init 0xFFFF, transmitted inverted."""
    return crc_reflected(bytes_lsb_bits(data), 16, 0xA001, 0xFFFF) ^ 0xFFFF


def usb_crc5(value11: int) -> int:
    """USB token CRC5 (x^5+x^2+1) over 11 bits LSB-first, init 0x1F, inverted."""
    return crc_reflected([(value11 >> i) & 1 for i in range(11)], 5, 0x14, 0x1F) ^ 0x1F


def eth_fcs(frame: bytes) -> int:
    """Ethernet FCS (CRC-32, reflected 0xEDB88320, init/xorout 0xFFFFFFFF)."""
    return zlib.crc32(frame) & 0xFFFFFFFF


def crc8_07(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    assert uart_frame_bits(0xA5) == [0, 1, 0, 1, 0, 0, 1, 0, 1, 1]
    assert uart_decode_data_bits([1, 0, 1, 0, 0, 1, 0, 1]) == 0xA5
    det = I2cConditionDetector()
    assert det.update(0, 1) == "START"
    assert det.update(1, 1) == "STOP"
    states, _ = nrzi_encode([1, 0], 0)
    assert states == [0, 1]
    assert eth_fcs(b"123456789") == 0xCBF43926          # CRC-32 check value
    assert crc_reflected(bytes_lsb_bits(b"123456789"), 16, 0xA001, 0xFFFF) ^ 0xFFFF == 0xB4C8
    assert crc8_07(b"123456789") == 0xF4                # CRC-8/SMBUS check value
    print("golden_model self-test OK")
