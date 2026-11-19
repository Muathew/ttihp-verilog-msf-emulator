# SPDX-License-Identifier: Apache-2.0
"""msfcore host driver: load, reset, step and inspect the chip from the pins alone.

One driver for two hosts. Every method is a generator that yields pin operations,
and a runner carries them out:

* on the Tiny Tapeout demo board (MicroPython on its RP2350), `host/tt_board.py`
  runs them through the board's `ttboard` SDK;
* on a PC, `test/test_host_driver.py` runs the same generators in cocotb against the
  RTL, so a bring-up is rehearsed, byte for byte, before any chip exists.

The operations (tuples; a runner returns a value for the reads):

    ("ui", v)               drive ui_in = v
    ("uio", v, oe)          drive the uio pins in mask oe to v (the host's side)
    ("rst", level)          rst_n = level (0: held in reset)
    ("clk", n)              n rising clock edges
    ("uo",)                 read uo_out
    ("uio_out",)            read the chip's uio outputs
    ("until", m, v, n)      clock until uo_out & m == v; give up after n edges;
                            returns the edges used, or -1

Written for MicroPython as well as CPython: no f-strings in hot paths, no typing,
no dataclasses. Pin use and frames: docs/info.md "Memory map and boot", the debug
view: docs/isa.md section 8. Frames are byte for byte those of tools/msf_boot.py
(test/test_host_driver.py checks).
"""

SYNC = 0xA5
CMD_WRITE = 0x57
CMD_GO = 0x47
CALIBRATE = 0xFF           # autobaud: a start bit of one bit time, then eight 1s

# ui_in at reset (docs/info.md): bit 0 direct boot, bit 1 parallel loader,
# bit 2 debug view, bit 3 autobaud
UI_DIRECT = 0x01
UI_PARALLEL = 0x02
UI_DEBUG = 0x04
UI_AUTOBAUD = 0x08

# uo_out while the loader runs: bit 0 waiting, 1 OK, 2 CRC error, 3 starting firmware;
# bit 7 the parallel acknowledge
ST_WAIT, ST_OK, ST_CRC, ST_GO, PAR_ACK = 0x01, 0x02, 0x04, 0x08, 0x80

STROBE = 0x02              # uio[1]: parallel strobe, or the UART loader's RX
RESET_LOW = 4              # rst_n low for at least three edges (docs/info.md)
RESET_SYNC = 2             # the core starts on the third edge after rst_n rises
CHUNK = 64                 # words per write frame


class MsfError(Exception):
    pass


def crc8(data, poly=0x07):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def frame(cmd, addr, words=()):
    body = [cmd, addr & 0xFF, (addr >> 8) & 0xFF, len(words) & 0xFF, (len(words) >> 8) & 0xFF]
    for w in words:
        body.append(w & 0xFF)
        body.append((w >> 8) & 0xFF)
    return [SYNC] + body + [crc8(body)]


def frames(words, addr=128, run=True, chunk=CHUNK):
    """The frames that load `words` at `addr` (and jump there if run)."""
    out = []
    for i in range(0, len(words), chunk):
        out.append(frame(CMD_WRITE, addr + i, words[i:i + chunk]))
    if run:
        out.append(frame(CMD_GO, addr))
    return out


def drive(gen, do):
    """Run a driver generator with a synchronous pin function `do(op) -> value`."""
    val = None
    try:
        while True:
            val = do(gen.send(val))
    except StopIteration as e:          # MicroPython keeps the value in args, as CPython does
        return e.args[0] if e.args else None


class Msf:
    """The chip as its pins show it. Methods are generators: run them with a runner
    (host/tt_board.py on the board, test/test_host_driver.py in simulation)."""

    def __init__(self):
        self.ui = 0
        self.debug_mode = False

    # ---- reset ----------------------------------------------------------------
    def reset(self, ui=UI_DIRECT, uio=0, oe=0):
        """Hold rst_n low with the straps on ui_in, release it, and return in the
        core's cycle 0. The SRAM keeps its contents across a reset."""
        self.ui = ui
        self.debug_mode = bool(ui & UI_DEBUG)
        yield ("rst", 0)
        yield ("ui", ui)
        yield ("uio", uio, oe)
        yield ("clk", RESET_LOW)
        yield ("rst", 1)
        yield ("clk", RESET_SYNC)

    # ---- the boot ROM's loaders ---------------------------------------------
    def load_parallel(self, words, addr=128, run=True, timeout=2000):
        """The parallel loader (straps ui_in = 0b10): byte k on ui_in, then strobe
        uio[1] = (k + 1) mod 2; the ROM copies the strobe to uo_out[7] once it has the
        byte. Needs no clock rate: it works single-stepped or free-running."""
        yield from self.reset(UI_PARALLEL, 0, STROBE)
        k = 0
        results = []
        for f in frames(words, addr, run):
            for b in f:
                level = (k + 1) & 1
                yield ("ui", b)
                yield ("clk", 1)
                yield ("uio", level << 1, STROBE)
                n = yield ("until", PAR_ACK, level << 7, timeout)
                if n < 0:
                    raise MsfError("parallel: no acknowledge for byte %d (0x%02X)" % (k, b))
                k += 1
            results.append((yield from self._result(f, timeout)))
        yield ("ui", 0)                 # the firmware should not start with a CRC byte on ui_in
        return results

    def load_uart(self, words, addr=128, run=True, bit=40, timeout=None):
        """The UART loader with autobaud (straps ui_in = 0b1000): the ROM times the
        start bit of a 0xFF and then runs at `bit` clock cycles a bit. The host
        clocks the bits itself, so the rate is exact whatever the host's speed."""
        yield from self.reset(UI_AUTOBAUD, STROBE, STROBE)       # RX idles high
        if (yield ("until", ST_WAIT, ST_WAIT, 2000)) < 0:        # the ROM is ready to time it
            raise MsfError("UART loader: the ROM never showed 'waiting' on uo_out[0]")
        yield from self._uart_byte(CALIBRATE, bit)
        results = []
        for f in frames(words, addr, run):
            for b in f:
                yield from self._uart_byte(b, bit)
            results.append((yield from self._result(f, timeout or 40 * bit)))
        return results

    def _uart_byte(self, b, bit):
        yield ("uio", 0, STROBE)                      # start bit
        yield ("clk", bit)
        for i in range(8):
            yield ("uio", STROBE if (b >> i) & 1 else 0, STROBE)
            yield ("clk", bit)
        yield ("uio", STROBE, STROBE)                 # stop bit
        yield ("clk", bit)

    def _result(self, f, timeout):
        """Each frame's answer on uo_out: OK (bit 1) or a CRC error (bit 2); a go
        frame shows bit 3 as the firmware starts."""
        want = ST_GO if f[1] == CMD_GO else ST_OK
        n = yield ("until", want | ST_CRC, want, timeout)
        if n >= 0:
            return "ok"
        uo = yield ("uo",)
        if uo & ST_CRC:
            raise MsfError("loader: CRC error on frame at 0x%03X" % (f[2] | f[3] << 8))
        raise MsfError("loader: no answer to frame at 0x%03X (uo_out 0x%02X)"
                       % (f[2] | f[3] << 8, uo))

    # ---- running and the debug view ------------------------------------------
    def start(self, debug=True):
        """Reset into the firmware already in SRAM at 128 (ui_in[0] = 1), with the
        debug view on (ui_in[2] = 1 during reset) unless debug=False."""
        yield from self.reset(UI_DIRECT | (UI_DEBUG if debug else 0))

    def step(self, n=1):
        yield ("clk", n)

    def view(self, sel, page=0):
        """One byte of internal state (docs/isa.md section 8). The select needs no
        clock edge, so reading never moves the core."""
        if not self.debug_mode:
            raise MsfError("the debug view needs ui_in[2] high during reset: start(debug=True)")
        self.ui = ((sel & 0x3F) << 2) | ((page & 1) << 1)
        yield ("ui", self.ui)
        v = yield ("uo",)
        return v

    def view16(self, sel, page=0):
        lo = yield from self.view(sel, page)
        hi = yield from self.view(sel + 1, page)
        return lo | (hi << 8)

    def reg(self, thread, r):
        """R`r` of thread 0-3."""
        page, base = thread >> 1, (thread & 1) * 0x10
        v = yield from self.view16(base + 2 * r, page)
        return v

    def regs(self, thread=0):
        out = []
        for r in range(8):
            out.append((yield from self.reg(thread, r)))
        return out

    def last(self):
        """The last instruction issued: (pc, thread, word)."""
        lo = yield from self.view(0x20)
        hi = yield from self.view(0x21)
        word = yield from self.view16(0x22)
        pc = lo | ((hi & 0x03) << 8) | (((hi >> 7) & 1) << 10)
        thread = ((hi >> 2) & 1) | (((hi >> 6) & 1) << 1)
        return pc, thread, word

    def counter(self):
        v = yield from self.view16(0x25)
        return v

    def pins(self):
        """OUT, OE and UO as the firmware set them (the view replaces uo_out)."""
        out = yield from self.view(0x29)
        oe = yield from self.view(0x2A)
        uo = yield from self.view(0x2B)
        return out, oe, uo

    def run_to(self, pc, limit=100000):
        """Single-step until the last instruction issued is at `pc`; returns the
        steps taken, or -1 after `limit`."""
        for n in range(limit):
            yield ("clk", 1)
            p, _, _ = yield from self.last()
            if p == pc:
                return n + 1
        return -1
