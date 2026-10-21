# SPDX-License-Identifier: Apache-2.0
"""Capture engine (isa.md §6.6): the chip as a logic analyser.

The testbench drives the pins as a function of the cycle counter, so the
value every sample must hold follows from the spec's timing alone: armed by
the instruction in cycle a, sample k is taken in cycle a + 1 + k * DIV and
sees the pins of cycle a - 1 + k * DIV (input synchroniser). `cap_ref`
restates the recording rules (packing, change records, trigger, POST, ring)
from the spec; the RTL's SRAM, status and trigger position must match it.
"""
import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, ReadOnly, Timer

from msf_tb import boot_asm, reg, sram, ctrl, uart_decode
from fw_tb import fw_boot, Recorder, spawn

CAP_N, CAP_EDGE, CAP_UI, CAP_CHG, CAP_ARM = 10, 0x1000, 0x2000, 0x4000, 0x8000
CS_RUN, CS_TRIG, CS_DONE, CS_OVF = 0x8000, 0x4000, 0x2000, 0x1000
CLEAR = "\n".join(f"LDR R{i}, #0" for i in range(8)) + "\n"


def cap_ref(samples, chg, mask, val, edge, post):
    """The words the engine records for a sample sequence, and the index of
    the trigger word; stops after the POST-th word past the trigger."""
    words, tw, trig, left = [], None, False, 0
    prev_c, first, half, thit, last, delta = True, True, False, False, 0, 0
    for s in samples:
        cond = (s & mask) == val
        hit = cond and (not edge or not prev_c)
        prev_c = cond
        word, ptrig = None, False
        if chg:
            d = (delta + 1) & 0xFF
            if first or s != last or d == 255 or (hit and not trig):
                word, ptrig, delta, last = s << 8 | d, hit and not trig, 0, s
            else:
                delta = d
        elif not half:
            last, half, thit = s, True, hit and not trig
        else:
            word, ptrig, half = s << 8 | last, not trig and (thit or hit), False
        first = False
        if word is None:
            continue
        words.append(word)
        if ptrig:
            trig, tw, left = True, len(words) - 1, post
            if post == 0:
                return words, tw
        elif trig:
            if left == 1:
                return words, tw
            left -= 1
    raise AssertionError("capture did not finish within the samples given")


def ring(words, tw, base, n):
    """Expected buffer contents {addr: word}, trigger address, next address."""
    size = 2 << n
    block = base & ~(size - 1)
    mem = {}
    for i, w in enumerate(words):
        mem[block | (i & (size - 1))] = w
    return mem, block | (tw & (size - 1)), block | (len(words) & (size - 1))


async def drive(dut, f, bank="uio"):
    """Every cycle t, put f(t) on the pins (t = the cycle counter)."""
    c = ctrl(dut)
    sig = dut.uio_in if bank == "uio" else dut.ui_in
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        if not c.cnt.value.is_resolvable:  # before the first reset
            continue
        t = int(c.cnt.value)
        await Timer(1, unit="ns")
        sig.value = f(t) & 0xFF


def arm_prog(buf, div, trig, ctl, work=""):
    """Configure and arm (R5 = CNT in the cycle before), run `work`, wait
    for done; then R2 = status, R4 = trigger position."""
    return CLEAR + f"""
        LDI  R1, {buf}
        CAP_BUF R1
        LDI  R1, {div}
        CAP_DIV R1
        LDI  R1, {trig}
        CAP_TRIG R1
        LDI  R1, {ctl}
        CNT_R R5
        CAP_CTL R1
        {work}
    wt: CAP_STAT R2
        LDI  R3, {CS_DONE}
        AND  R3, R2, R3
        ADDS R3, R3, R3
        JZ   wt
        CAP_TPOS R4
    h:  JMP h
    """


async def capture(dut, f, base, n, div, mask=0, val=0, edge=False, chg=False,
                  post=15, bank="uio", work="", cycles=3000, ui_boot=1):
    """Run one capture; check SRAM, trigger position and status against
    cap_ref. Returns (words, trigger index, R5)."""
    ctl = post | (CAP_EDGE if edge else 0) | (CAP_UI if bank == "ui" else 0) | \
        (CAP_CHG if chg else 0) | CAP_ARM
    spawn_drv = cocotb.start_soon(drive(dut, f, bank))
    await boot_asm(dut, arm_prog(base | n << CAP_N, div, mask | val << 8, ctl, work),
                   ui_in=ui_boot)
    await ClockCycles(dut.clk, cycles)
    spawn_drv.cancel()
    r5 = reg(dut, 5)
    samples = [f(r5 + k * div) & 0xFF for k in range(4096)]
    words, tw = cap_ref(samples, chg, mask, val, edge, post)
    mem, tpos, nxt = ring(words, tw, base, n)
    stat = reg(dut, 2)
    assert stat & (CS_RUN | CS_TRIG | CS_DONE | CS_OVF) == CS_TRIG | CS_DONE, f"status {stat:#06x}"
    assert stat & 0x3FF == nxt, f"next address {stat & 0x3FF} != {nxt}"
    assert reg(dut, 4) == tpos, f"trigger at {reg(dut, 4)} != {tpos}"
    bad = [(a, sram(dut, a), w) for a, w in sorted(mem.items()) if sram(dut, a) != w]
    assert not bad, f"buffer differs at {bad[:4]} (of {len(bad)})"
    return words, tw, r5


@cocotb.test()
async def test_cap_sample_mode(dut):
    """Two samples a word, every cycle and every 3rd; MASK 0 triggers on the
    first sample, then POST words follow."""
    for div in (1, 3):
        words, tw, _ = await capture(dut, lambda t: t, 512, 4, div, post=15)
        assert (len(words), tw) == (16, 0)


@cocotb.test()
async def test_cap_pretrigger_ring(dut):
    """A level trigger on a pin value long after arming: the ring keeps the
    words before it (16-word buffer, 160 words recorded, 6 after)."""
    words, tw, _ = await capture(dut, lambda t: t >> 1, 640, 3, 1, mask=0xFF,
                                 val=0xC0, post=6)
    assert tw >= 16 and len(words) == tw + 7


@cocotb.test()
async def test_cap_edge_vs_level(dut):
    """Pin 0 high from before arming, low at cycle 400, high again at 440:
    a level trigger fires at once, an edge trigger only at 440."""
    f = lambda t: 0 if 400 <= t < 440 else 0xFF
    _, tw_level, _ = await capture(dut, f, 512, 6, 1, mask=1, val=1, post=4)
    words, tw_edge, r5 = await capture(dut, f, 512, 6, 1, mask=1, val=1, edge=True, post=4)
    assert tw_level == 0
    assert tw_edge == (440 - r5) // 2


@cocotb.test()
async def test_cap_change_mode(dut):
    """Change mode: a word {pins, ticks since the last} at every change, and
    one after 255 ticks without one."""
    steps = [(0, 0x00), (300, 0x01), (310, 0x03), (360, 0x07), (900, 0x0F), (905, 0x00)]

    def f(t):
        v = 0
        for at, val in steps:
            if t >= at:
                v = val
        return v
    for div in (1, 2):
        words, tw, _ = await capture(dut, f, 768, 4, div, chg=True, post=8,
                                     cycles=2600)
        assert any(w & 0xFF == 255 for w in words), "no record after 255 ticks"


@cocotb.test()
async def test_cap_ui_pins(dut):
    """CAP_UI records ui_in (bit 0 kept high: direct boot)."""
    await capture(dut, lambda t: t | 1, 512, 3, 2, bank="ui", post=7)


WORK = """
        LDR  R6, #40
    wl: LDR  R7, #3
        ST   R7, R6
        LD   R1, R7
        NOP
        NOP
        FIFO_PUT R6
        FIFO_GET R1
        DECR R6
        JNZ  wl
        CNT_R R6
        SUBS R6, R6, R5
"""


@cocotb.test()
async def test_cap_never_changes_core_timing(dut):
    """The same data-RAM-heavy loop takes exactly as long with the engine
    recording every cycle as without it, and the capture is intact: the
    engine only uses free port-B cycles."""
    await capture(dut, lambda t: t, 512, 6, 1, post=110, work=WORK)
    with_cap = reg(dut, 6)
    # the same program with the arm bit clear: the engine stays idle
    await boot_asm(dut, arm_prog(512 | 6 << CAP_N, 1, 0, 110, WORK))
    await ClockCycles(dut.clk, 1500)
    assert reg(dut, 6) == with_cap, f"{reg(dut, 6)} cycles without capture, {with_cap} with"
    assert int(ctrl(dut).cap_stat.value) & CS_RUN == 0


@cocotb.test()
async def test_cap_overflow_flag(dut):
    """A burst of 8 MEM_W keeps port B busy 8 cycles while the engine makes a
    word every 2: its 2-word queue overflows, and the status says so."""
    work = "LDR R6, #0\nMEM_A R6\n" + "MEM_W R6\n" * 8
    await boot_asm(dut, arm_prog(512 | 6 << CAP_N, 1, 0, 60 | CAP_ARM, work))
    await ClockCycles(dut.clk, 600)
    assert reg(dut, 2) & CS_OVF, f"status {reg(dut, 2):#06x}"


@cocotb.test()
async def test_fw_logic_analyzer(dut):
    """firmware/logic_analyzer.s: arm, wait, dump over UART; the host gets
    the samples back exactly (sample mode, every 2nd cycle, edge trigger)."""
    defines = {"DIV": 2, "MASK": 0xF0, "VAL": 0x30, "EDGE": CAP_EDGE, "N": 4,
               "BASE": 512, "POST": 9, "BIT": 12}
    f = lambda t: t >> 2
    drv = cocotb.start_soon(drive(dut, f))
    await fw_boot(dut, "logic_analyzer.s", defines=defines)
    rec = Recorder(dut)
    await rec.until_uo(lambda v: v == 0x55, 60000)
    drv.cancel()
    raw = uart_decode(rec.out, 12)
    assert -1 not in raw
    w = [raw[i] | raw[i + 1] << 8 for i in range(0, len(raw) - 1, 2)]
    assert w[0] == 0xA55A, [hex(x) for x in w[:4]]
    cnt0, tidx, stat, data = w[1], w[2], w[3], w[4:]
    size = 2 << defines["N"]
    assert len(data) == size
    samples = [f(cnt0 + k * defines["DIV"]) & 0xFF for k in range(4096)]
    words, tw = cap_ref(samples, False, 0xF0, 0x30, True, defines["POST"])
    assert stat & (CS_RUN | CS_TRIG | CS_DONE | CS_OVF) == CS_TRIG | CS_DONE
    want = ([0] * size + words)[-size:]       # oldest first; cleared before arming
    assert data == want
    assert tidx == (tw - (len(words) - size)) % size
