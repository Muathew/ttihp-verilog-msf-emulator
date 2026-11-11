#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""msfcore boot-ROM host tool: build boot frames and load firmware over UART.

Boot protocol v2 (see docs/info.md "Loading firmware"):

    A5  cmd  addrL addrH  lenL lenH  w0L w0H ... w(len-1)L w(len-1)H  crc8

    cmd 'W' (0x57) : write `len` words starting at `addr` (any SRAM address;
                     0-127 = data RAM, 128-2047 = code)
    cmd 'G' (0x47) : jump to `addr` (len = 0)
    crc8           : CRC-8, poly 0x07, init 0, over cmd..last payload byte

The chip answers every frame with ACK (0x06) or NAK (0x15) on UART TX
(uio[0], 115200 8N1 at a 12 MHz clock) and mirrors the result on uo_out.

Autobaud (v4, plan-v4 B6): with ui_in[3] high at reset the ROM measures the start bit of
the first byte it sees, which must be 0xFF, and sets its bit time from it, so the loader
works at any clock and any --baud. `--autobaud` sends that byte first. Wait for uo_out[0]
(waiting for a frame) before sending it.

Usage:
    python tools/msf_boot.py firmware.s --port COM5 [--addr 128] [--no-run]
    python tools/msf_boot.py firmware.s --dump     # print the frame bytes
    python tools/msf_boot.py firmware/uart_tx.s --data-str "hello" --port COM5
        --data-str S  : data RAM 0.. = one byte of S per word + 0 terminator
        --data-hex F  : data RAM 0.. = hex words from file F
"""
import argparse
import os
import sys

ROM_PROTOCOL = 2      # protocol implemented by test/bootloader.s

SYNC = 0xA5
CMD_WRITE = 0x57
CMD_GO = 0x47
ACK, NAK = 0x06, 0x15


def crc8(data, poly=0x07, init=0x00):
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def frame(cmd, addr, words=()):
    body = [cmd, addr & 0xFF, (addr >> 8) & 0xFF, len(words) & 0xFF, (len(words) >> 8) & 0xFF]
    for w in words:
        body += [w & 0xFF, (w >> 8) & 0xFF]
    return [SYNC] + body + [crc8(body)]


CALIBRATE = 0xFF       # autobaud: a start bit of exactly one bit time, then eight 1s


def boot_frame(words, addr=128, run=True, protocol=None, autobaud=False):
    """Byte stream that loads `words` at `addr` (and runs it if `run`); with `autobaud` it
    starts with the calibration byte the ROM times its bit rate from."""
    if autobaud:
        return [CALIBRATE] + boot_frame(words, addr, run, protocol)
    protocol = protocol or ROM_PROTOCOL
    if protocol == 1:                         # v1: raw LE words + 0x0000 end
        out = []
        for w in words:
            out += [w & 0xFF, (w >> 8) & 0xFF]
        return out + [0, 0]
    out = frame(CMD_WRITE, addr, words)
    if run:
        out += frame(CMD_GO, addr)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", help="assembly file (.s) or raw hex words (.hex)")
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=128)
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--no-run", action="store_true")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--autobaud", action="store_true",
                    help="send the 0xFF calibration byte first (the chip's ui_in[3] must be high at reset)")
    ap.add_argument("--data-str", help="load these bytes (+0) into data RAM 0..")
    ap.add_argument("--data-hex", help="load hex words from this file into data RAM 0..")
    a = ap.parse_args()

    if a.source.endswith(".hex"):
        words = [int(t, 16) for t in open(a.source).read().split()]
    else:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "test"))
        from assembler import assemble
        words = assemble(open(a.source).read(), base_addr=a.addr)

    data = []
    if a.data_str is not None:
        data = list(a.data_str.encode()) + [0]
    elif a.data_hex:
        data = [int(t, 16) for t in open(a.data_hex).read().split()]
    d_frame = frame(CMD_WRITE, 0, data) if data else []
    w_frame = frame(CMD_WRITE, a.addr, words)
    g_frame = [] if a.no_run else frame(CMD_GO, a.addr)
    if a.dump or not a.port:
        print(" ".join(f"{b:02X}" for b in ([CALIBRATE] if a.autobaud else []) + d_frame + w_frame + g_frame))
        return 0

    import serial  # pyserial, only needed for real hardware
    with serial.Serial(a.port, a.baud, timeout=2) as s:
        if a.autobaud:
            s.write(bytes([CALIBRATE]))
            s.flush()
        for f, what in ((d_frame, "data"), (w_frame, "write"), (g_frame, "go")):
            if not f:
                continue
            s.write(bytes(f))
            r = s.read(1)
            if r != bytes([ACK]):
                print(f"{what}: no ACK (got {r!r})")
                return 1
            print(f"{what}: ACK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
