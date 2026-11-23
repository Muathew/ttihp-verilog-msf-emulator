#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the MCU protocol-test monitor (fpga/ps/mcu_test/src/main.c) on the host.

    python fpga/tests/run_mcu_monitor_test.py
    python fpga/tests/run_mcu_monitor_test.py --replay uo.txt   # print its log

Builds the monitor with the stand-in link in mcu_monitor_replay.c and plays it
uo_out reads, here made from events by the format in
firmware/mcu_protocol_target.s (test_targets.py checks the firmware against the
same format at the pins). Needs gcc. Exit code 0 = all passed.
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PS = REPO / "fpga" / "ps"

SENT, UART, SPI_OK, SPI_BAD, SPI_EXP, SPI_RST, I2C_ADR, I2C_ADX, I2C_PTR, I2C_WR, I2C_RD, \
    I2C_RDA, BUS, LA, PINS, ACT = range(16)
BOOT = (BUS, 0x83)
IDLE_PINS = 0xAF                    # RX, TX, SCL, SDA, CS and MISO high (0xAA is next)


class Trace:
    """uo_out as the A53 reads it: every group for a few reads."""

    def __init__(self):
        self.reads, self.strobe = [0, 0], 0

    def group(self, value, reads=3):
        self.strobe ^= 0x80
        self.reads += [value | self.strobe] * reads

    def event(self, code, data):
        word = code << 8 | data
        self.group(0x40 | (word >> 6))
        self.group(word & 0x3F)

    def events(self, *items):
        for code, data in items:
            self.event(code, data)

    def beat(self, sent, pins=IDLE_PINS, moved=0):
        self.events((PINS, pins), (ACT, moved), (SENT, sent))


def build(out_dir, app="mcu_test", firmware="mcu_protocol_target.s", defines=()):
    out_dir = out_dir / (app + "".join(f"_{d}" for d in defines).replace("=", ""))
    out_dir.mkdir(exist_ok=True)
    image = out_dir / "firmware_image.h"
    subprocess.run([sys.executable, str(PS / "loader" / "generate_image.py"),
                    str(REPO / "firmware" / firmware), "--output", str(image)]
                   + [a for d in defines for a in ("-D", d)],
                   check=True, stdout=subprocess.DEVNULL)
    exe = out_dir / "mcu_monitor"
    subprocess.run(["gcc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", "-DMSF_HOST",
                    f"-I{out_dir}", f"-I{PS / 'common'}", str(PS / app / "src" / "main.c"),
                    str(HERE / "mcu_monitor_replay.c"), "-o", str(exe)], check=True)
    return exe


def v3_pass_bytes():
    """One pass of firmware/mcu_v3_peer.s, as it tells the MCU: T = 12288 (0x3000),
    five frames, four packets, the guard. Data bytes that equal prompt letters ('S',
    'P', 'm') must not be taken for prompts."""
    b = [ord("S"), ord("S"), ord("s"), 0x00, 0x30]
    for st, n in ((0x81, 64), (0x81, 0x153), (0x81, 64), (0x81, 1518), (0x82, 0x6D)):
        b += [ord("M"), ord("m"), st, n & 0xFF, n >> 8]
    for st in (0x81, 0x81, 0x81, 0x82):
        b += [ord("U"), ord("u"), st, 36, 0x80, 0x4B]
    b += [ord("A"), ord("B"), 0x11, 0x80, ord("C"), 0x10, 0x80, ord("D"), 0x21, 0x80, ord("P")]
    return b


def run(exe, reads):
    text = "\n".join(f"{r:02x}" for r in reads) + "\n"
    out = subprocess.run([str(exe)], input=text.encode(), capture_output=True, check=True).stdout
    # The monitor ends lines with CR LF; on Windows the C library adds another CR.
    return out.decode().replace("\r\r\n", "\n").replace("\r\n", "\n")


def check(name, log, *want, absent=()):
    """Every string of `want` appears in the log, in that order."""
    at = 0
    for item in want:
        found = log.find(item, at)
        if found < 0:
            raise AssertionError(f"{name}: no {item!r} after offset {at} in:\n{log}")
        at = found + len(item)
    for item in absent:
        if item in log:
            raise AssertionError(f"{name}: unexpected {item!r} in:\n{log}")
    print(f"PASS: {name}")


def pass_events():
    return [(UART, 0x01)] + [(SPI_OK, b) for b in (0x55, 0xAA)] + [
        (I2C_ADR, 0xA0), (I2C_PTR, 0), (BUS, 1), (I2C_ADR, 0xA1), (I2C_RD, 0xA5)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, help="print the log for a file of uo_out reads")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        exe = build(Path(tmp))
        if args.replay:
            reads = [int(v, 16) for v in args.replay.read_text().split()]
            sys.stdout.write(run(exe, reads))
            return 0

        t = Trace()
        t.events(BOOT)
        t.beat(0)
        check("boot", run(exe, t.reads), "firmware v3 is running",
              "pins RX=1 TX=1 SCL=1 SDA=1 SCK=0 CS=1 MOSI=0 MISO=1 moved: none\n",
              absent=("WARNING", "hint", "---- burst"))

        t = Trace()
        t.events(BOOT)
        t.beat(0)
        t.events(*pass_events())
        t.beat(8, moved=0xFF)
        before_quiet = run(exe, t.reads)
        check("nothing is printed while traffic may still arrive", before_quiet, "pins",
              absent=("---- burst", "got 0x01"))
        t.beat(8)
        check("a quiet heartbeat prints the burst", run(exe, t.reads),
              "burst 1: 8 events", "UART got 0x01", "byte 0: MOSI 0x55 ok",
              "byte 1: MOSI 0xaa ok", "address byte 0xa0 (write 0x50): ACK", "pointer 0x00",
              "repeated START", "address byte 0xa1 (read 0x50): ACK",
              "sent 0xa5, controller NACK", "totals: UART 1 | SPI ok 2 bad 0",
              "moved: RX TX SCL SDA SCK CS MOSI MISO", absent=("WARNING",))

        t = Trace()
        t.events((SPI_OK, 0x55), (SPI_BAD, 0xA0), (SPI_EXP, 0xAA), (SPI_RST, 5 << 4 | 2),
                 (SPI_OK, 0x55))
        t.beat(5)
        t.beat(5)
        check("SPI reports", run(exe, t.reads), "byte 1: MOSI 0xa0 MISMATCH, expected 0xaa",
              "went quiet at byte 2 with 5 bits to go", "byte 0: MOSI 0x55 ok", "SPI ok 2 bad 1")

        t = Trace()
        t.beat(0)
        t.event(UART, 0x01)
        t.reads += [0x3C]               # a read during a change: bits not all there yet
        t.beat(3)                       # two events were sent that never came
        t.beat(3)
        check("a read mid-change is ignored and missed events are counted", run(exe, t.reads),
              "WARNING: missed 2 events", "UART got 0x01", "lost 2 framing 0")

        t = Trace()
        t.beat(0)
        t.group(0x40 | 0x01)            # a first group without its second ...
        t.event(UART, 0x05)             # ... then a whole event
        t.beat(1)
        t.beat(1)
        check("a lost group costs one event, not the stream", run(exe, t.reads),
              "UART got 0x05", "framing 1")

        t = Trace()
        t.beat(0)
        t.events(*[(LA, b) for b in (0x2F, 200, 0x2E, 7, 0x2E, 255, 0x2F, 3)])
        t.beat(0)
        t.beat(0)
        check("capture table", run(exe, t.reads),
              "t_us RX TX SCL SDA SCK CS MOSI MISO",
              "LA      0.0  1  1   1   1   0  1    0    0\n",
              "LA      2.3  0  1   1   1   0  1    0    0\n",
              "LA     88.3  1  1   1   1   0  1    0    0\n", "end, 4 words")

        t = Trace()
        t.beat(0, pins=0x23)
        t.event(BUS, 2)
        t.beat(0, pins=0x23)
        check("stuck lines give a hint and the watchdog is reported", run(exe, t.reads),
              "SCL=0 SDA=0", "hint: I2C line low", "WATCHDOG", absent=("---- burst",))

        t = Trace()
        t.beat(0)
        t.event(UART, 0x07)
        for sent in range(1, 200):      # never a quiet beat, and more than fits the queue
            t.event(UART, sent & 0xFF)
            t.event(UART, sent & 0xFF)
            t.beat((2 * sent + 1) & 0xFF, moved=1)
        check("traffic with no pause is still printed", run(exe, t.reads),
              "UART got 0x07", absent=("did not fit",))

        v3 = build(Path(tmp), "mcu_v3", "mcu_v3_peer.s")
        t = Trace()
        for byte in v3_pass_bytes():
            t.event(1, byte)
        check("v3: a pass's summary, data bytes kept apart from prompts", run(v3, t.reads),
              "for a 12 MHz core clock",
              "v3 pass 1", "cal  T=12288 cycles: MCU clock 32000 kHz",
              "eth  0: status 81, 64 bytes", "eth  1: status 81, 339 bytes",
              "eth  3: status 81, 1518 bytes", "eth  4: status 82, 109 bytes",
              "usb  0: status 81, 36 bytes, SYNC 80 PID 4b", "usb  3: status 82",
              "guard A", "guard B: T0 late: UIO7 off the bus, LATE_RD 8011",
              "guard C: flag cleared: UIO7 back, LATE_RD 8010",
              "guard D: the guard names T1, T0 late: UIO7 stays, LATE_RD 8021",
              "passes 1 | framing 0 | other events 0 | stray bytes 0",
              absent=("eth  5", "usb  4"))

        # the 48 MHz bitstream's image: the peer still sends T in 12 MHz cycles
        v3 = build(Path(tmp), "mcu_v3", "mcu_v3_peer.s", defines=("CLK_SHIFT=2",))
        check("v3 at 48 MHz: the clock named, T in 12 MHz cycles", run(v3, t.reads),
              "for a 48 MHz core clock", "v3 pass 1",
              "cal  T=12288 cycles of 12 MHz: MCU clock 32000 kHz",
              "passes 1 | framing 0 | other events 0 | stray bytes 0")
    print("PASS: MCU monitor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
