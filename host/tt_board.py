# SPDX-License-Identifier: Apache-2.0
"""Run host/msf_host.py on the Tiny Tapeout demo board (MicroPython, RP2350).

Copy msf_host.py and this file to the board, then:

    from tt_board import Board
    b = Board()                       # selects tt_um_msf, single-steps the clock
    b.load_parallel(words)            # words: a list of 16-bit ints (assembled on a PC)
    b.start()                         # reset into SRAM at 128, debug view on
    b.step(100); print(b.last(), b.regs(0))
    b.free(1000000)                 # let it run at 1 MHz
    b.load_parallel(words)            # the parallel loader handshakes, so it works free-running too

Clock modes: `stepped` (the default) edges the clock once per cycle the driver asks
for, so every count is exact and the UART loader's bit time is exact. `free(hz)` runs
the board's PWM clock; then ("clk", n) waits n cycles' time and ("until", ...) polls,
which suits the parallel loader and running firmware, not the UART loader.

The board's SDK names pins from the chip's side: ui_in and uio_in are what the
RP2350 drives, uo_out and uio_out what it reads, uio_oe_pico which uio pins it drives.
"""
import time

from msf_host import Msf, MsfError, drive

PROJECT = "tt_um_msf"


class Board:
    def __init__(self, tt=None, project=PROJECT):
        if tt is None:
            from ttboard.demoboard import DemoBoard
            from ttboard.mode import RPMode
            tt = DemoBoard.get()
            tt.mode = RPMode.ASIC_RP_CONTROL
        self.tt = tt
        if project:
            getattr(tt.shuttle, project).enable()
        self.msf = Msf()
        self.hz = 0                       # 0: stepped
        tt.clock_project_stop()

    # ---- clock ------------------------------------------------------------
    def stepped(self):
        self.tt.clock_project_stop()
        self.hz = 0

    def free(self, hz):
        self.tt.clock_project_PWM(hz)
        self.hz = hz

    # ---- the pin operations of msf_host.py --------------------------------
    def do(self, op):
        tt, kind = self.tt, op[0]
        if kind == "ui":
            tt.ui_in.value = op[1]
        elif kind == "uio":
            tt.uio_oe_pico.value = op[2]
            tt.uio_in.value = op[1] & op[2]
        elif kind == "rst":
            tt.reset_project(op[1] == 0)
        elif kind == "clk":
            self._clk(op[1])
        elif kind == "uo":
            return int(tt.uo_out.value)
        elif kind == "uio_out":
            return int(tt.uio_out.value)
        elif kind == "until":
            return self._until(op[1], op[2], op[3])
        else:
            raise MsfError("unknown pin operation %r" % (op,))
        return None

    def _clk(self, n):
        if self.hz:
            time.sleep_us(max(1, (n * 1000000) // self.hz))
            return
        once = self.tt.clock_project_once
        for _ in range(n):
            once()

    def _until(self, mask, val, limit):
        uo = self.tt.uo_out
        if self.hz:
            deadline = time.ticks_add(time.ticks_us(), max(1000, (limit * 1000000) // self.hz))
            while time.ticks_diff(deadline, time.ticks_us()) > 0:
                if int(uo.value) & mask == val:
                    return 0              # free-running: the edge count is not known
            return -1
        once = self.tt.clock_project_once
        for n in range(limit + 1):
            if int(uo.value) & mask == val:
                return n
            once()
        return -1

    # ---- the driver, synchronously ----------------------------------------
    def run(self, gen):
        return drive(gen, self.do)

    def reset(self, *a, **k):
        return self.run(self.msf.reset(*a, **k))

    def load_parallel(self, words, addr=128, run=True):
        return self.run(self.msf.load_parallel(words, addr, run))

    def load_uart(self, words, addr=128, run=True, bit=40):
        if self.hz:
            raise MsfError("the UART loader needs the stepped clock: call stepped() first")
        return self.run(self.msf.load_uart(words, addr, run, bit))

    def start(self, debug=True):
        return self.run(self.msf.start(debug))

    def step(self, n=1):
        return self.run(self.msf.step(n))

    def view(self, sel, page=0):
        return self.run(self.msf.view(sel, page))

    def regs(self, thread=0):
        return self.run(self.msf.regs(thread))

    def last(self):
        return self.run(self.msf.last())

    def counter(self):
        return self.run(self.msf.counter())

    def pins(self):
        return self.run(self.msf.pins())

    def run_to(self, pc, limit=100000):
        return self.run(self.msf.run_to(pc, limit))
