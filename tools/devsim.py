# SPDX-License-Identifier: Apache-2.0
"""Run a firmware program on the ISA model (test/isa_model.py) against the device a
cocotb test models, in seconds instead of a simulator's minutes: the first thing to
try when a protocol test fails.

    python tools/devsim.py sdspi.s test_sdspi Card 600000
    python tools/devsim.py mdio.s test_mdio Phy 20000 --show "dev.reads"

The device class is the test's own (test/test_<name>.py): one built as
Device(dut) that runs `await RisingEdge(dut.clk)` in a loop, reads dut.uio_out and
dut.uio_oe, and drives dut.uio_in, as test_sdspi.Card, test_nrf24.Radio and the
I2C devices (test/i2c_dev.py) do. Here each RisingEdge is one model cycle: the device
sees the pins the program has driven so far and its uio_in reaches the program
through the model's two-cycle input synchroniser. The model's pins change in the
cycle an instruction issues, the RTL's a cycle later, so an edge can be a cycle
off: a program that passes here can still fail on the RTL by a cycle, never the
other way round for logic errors (a wrong branch, a wrong count, a wrong bit).
It stops when uo_out shows DONE (0x55) or BAD (0xEE), or after the cycles given;
--show evaluates expressions over `dev` (the device), `m` (the model) and `words`
(the program) afterwards."""
import argparse
import importlib
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = os.path.join(HERE, "..", "test")
FW = os.path.join(HERE, "..", "firmware")
sys.path[:0] = [HERE, TEST]


class _Edge:
    """Stands in for cocotb's RisingEdge: one model cycle."""
    def __init__(self, *a, **k):
        pass

    def __await__(self):
        yield None


class _Cycles:
    """ClockCycles(clk, n): n model cycles."""
    def __init__(self, clk, n=1, *a, **k):
        self.n = n

    def __await__(self):
        for _ in range(self.n):
            yield None


class _Now:
    """ReadOnly, ReadWrite, Timer: within the cycle, so no time at all here."""
    def __init__(self, *a, **k):
        pass

    def __await__(self):
        return
        yield


class _Sig:
    def __init__(self, v=0):
        self.value = v

    def __int__(self):
        return int(self.value)


class _Mem:
    def __init__(self, m):
        self.m = m

    def __getitem__(self, a):
        return _Sig(self.m.mem[a])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("firmware", help="firmware/<name>.s")
    ap.add_argument("module", help="the test module, e.g. test_sdspi")
    ap.add_argument("device", help="the device class in it, e.g. Card")
    ap.add_argument("cycles", type=int)
    ap.add_argument("--uio-in", type=lambda s: int(s, 0), default=0,
                    help="uio_in before the device first drives it")
    ap.add_argument("-D", action="append", default=[], metavar="NAME=VALUE",
                    help="an assembler define, as fw_boot's defines")
    ap.add_argument("--show", action="append", default=[],
                    help="a Python expression to print afterwards")
    a = ap.parse_args()

    import cocotb
    coros = []
    cocotb.start_soon = coros.append
    mod = importlib.import_module(a.module)
    stand_in = {"RisingEdge": _Edge, "ClockCycles": _Cycles, "ReadOnly": _Now,
                "ReadWrite": _Now, "Timer": _Now}
    for m in list(sys.modules.values()):          # the test's and its helpers' triggers
        if m is None or m.__name__.startswith("cocotb"):
            continue
        for name, cls in stand_in.items():
            if hasattr(m, name):
                setattr(m, name, cls)
    import assembler as asm
    from isa_model import IsaModel

    defines = {k: int(v, 0) for k, v in (d.split("=", 1) for d in a.D)}
    path = a.firmware if os.path.exists(a.firmware) else os.path.join(FW, a.firmware)
    words = asm.assemble_file(path, base_addr=128, defines=defines or None)
    hist = {}
    m = IsaModel(asm.bootloader_words(), {128 + i: w for i, w in enumerate(words)},
                 uio_pad=lambda c: hist.get(c, a.uio_in), ui_pad=lambda c: 1)
    dut = types.SimpleNamespace(clk=None, uio_out=_Sig(), uio_oe=_Sig(),
                                uio_in=_Sig(a.uio_in), uo_out=_Sig())
    dut.user_project = types.SimpleNamespace(u_sram=types.SimpleNamespace(mem=_Mem(m)))
    dev = getattr(mod, a.device)(dut)
    for co in coros:
        co.send(None)
    c = 0
    for c in range(a.cycles):
        dut.uio_out.value, dut.uo_out.value = m.out, m.uo
        dut.uio_oe.value = m.oe & ~m.oe_off() & 0xFF
        for co in coros:
            co.send(None)
        hist[c] = int(dut.uio_in.value)
        m.step()
        if m.uo in (0x55, 0xEE) and c > 100:
            break
    verdict = {0x55: "DONE", 0xEE: "BAD"}.get(m.uo, "no result")
    print(f"{os.path.basename(path)} against {a.module}.{a.device}: {verdict} "
          f"(uo_out {m.uo:#04x}) after {c + 1} cycles")
    for e in a.show:
        print(f"{e} = {eval(e, {'dev': dev, 'm': m, 'words': words})}")
    return 0 if m.uo == 0x55 else 1


if __name__ == "__main__":
    sys.exit(main())
