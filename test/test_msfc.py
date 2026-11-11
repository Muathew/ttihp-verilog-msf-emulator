# SPDX-License-Identifier: Apache-2.0
"""msfc (docs/msfc.md): a program that compiles is on time.

Pure Python (pytest): the compiled code runs on the ISA model and is compared with msfc's own
reference interpreter, written from the language's semantics, on random programs; timed programs
are checked at the pins' cycle level; programs whose deadlines cannot be certified are refused."""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import msfc  # noqa: E402
from assembler import assemble  # noqa: E402
from isa_model import IsaModel  # noqa: E402

FW = os.path.join(HERE, "..", "firmware", "msfc")


def run_model(asm_text, cycles):
    words = assemble(asm_text, base_addr=128)
    m = IsaModel([assemble("JMP 128")[0]], {128 + i: w for i, w in enumerate(words)}, ui_pad=lambda c: 1)
    m.run(cycles)
    return m, words


def out_changes(m):
    """[(cycle, OUT)] for every change of OUT seen at an issue."""
    last, out = 0, []
    for e in m.trace:
        if e[0] == "I" and e[5]["out"] != last:
            last = e[5]["out"]
            out.append((e[1], last))
    return out


# ---------------------------------------------------------------------------- timing at the pins
def test_every_toggles_with_an_exact_period():
    asm, amap = msfc.compile_program(open(os.path.join(FW, "blink.msf")).read())
    ok, problems, _ = msfc.check(asm, amap)
    assert ok, problems
    m, _ = run_model(asm, 2000)
    ch = out_changes(m)
    gaps = {b[0] - a[0] for a, b in zip(ch, ch[1:])}
    assert len(ch) > 30 and gaps == {50}, f"periods {sorted(gaps)}"


def test_uart_edges_are_104_cycles_apart_and_decode():
    for byte in (0x55, 0xA3, 0x00, 0xFF, 0x7E):
        src = open(os.path.join(FW, "uart_tx.msf")).read().replace("var byte = 0x55;", f"var byte = {byte:#x};")
        asm, amap = msfc.compile_program(src)
        ok, problems, _ = msfc.check(asm, amap)
        assert ok, problems
        m, _ = run_model(asm, 1400)
        ch = out_changes(m)
        # the line: sample the pin at the middle of each bit, from the start bit's edge
        edges = [(c, v & 1) for c, v in ch]
        start = next(c for c, v in edges if v == 0)

        def at(c):
            cur = 1
            for c0, v0 in edges:
                if c0 <= c:
                    cur = v0
            return cur
        bits = [at(start + 104 * (k + 1) + 52) for k in range(8)]
        assert sum(b << i for i, b in enumerate(bits)) == byte, f"{byte:#x}: {bits}"
        assert at(start + 104 * 9 + 52) == 1               # the stop bit
        # every transition after the start is a multiple of 104 from it: the schedule has no drift
        assert all((c - start) % 104 == 0 for c, v in edges if c > start), edges


# ---------------------------------------------------------------------------- refusal
def test_a_deadline_that_cannot_be_certified_is_refused_with_its_line():
    # the body costs more than the period: the loop cannot get back to its WAIT_CMP in time
    body = "\n".join("    a += 1;" for _ in range(30))
    src = f"var a;\noe_set(1);\nevery 20 {{\n{body}\n    toggle(1);\n}}\n"
    asm, amap = msfc.compile_program(src)
    ok, problems, _ = msfc.check(asm, amap, "p.msf")
    assert not ok and problems and "p.msf:" in problems[0], problems
    # a timed write too soon after a long computation: the hardware would refuse it
    src = "var a;\ntime t = now() + 4;\n" + "\n".join("a += 1;" for _ in range(20)) + "\nat t set(1);\nhalt;\n"
    asm, amap = msfc.compile_program(src)
    ok, problems, _ = msfc.check(asm, amap, "q.msf")
    assert not ok and any("timed pin write" in p for p in problems), problems
    # the same with room is certified
    src = src.replace("now() + 4", "now() + 200")
    asm, amap = msfc.compile_program(src)
    assert msfc.check(asm, amap)[0]


def test_language_errors_name_the_line():
    for bad, frag in (("var a;\na = b;\n", "b is not declared"), ("x += 1;\n", "x is not declared"),
                      ("var a;\nvar a;\n", "declared twice"), ("var a;\nat a { }\n", "time variable"),
                      ("break;\n", "outside a loop"), ("var a;\na = a << a;\n", "constant")):
        try:
            msfc.compile_program(bad)
        except msfc.MsfError as e:
            assert frag in str(e) and "line" in str(e), (bad, str(e))
        else:
            raise AssertionError(f"accepted {bad!r}")


# ---------------------------------------------------------------------------- compiled == interpreted
def rand_expr(r, vs, depth=2):
    if depth == 0 or r.random() < 0.3:
        return r.choice(vs) if r.random() < 0.6 else str(r.randrange(0, 600))
    a = rand_expr(r, vs, depth - 1)
    b = r.choice(vs) if r.random() < 0.5 else str(r.randrange(0, 300))
    op = r.choice(["+", "-", "&", "|", "^"])
    if r.random() < 0.15:
        return f"({a} {r.choice(['<<', '>>'])} {r.randrange(1, 12)})"
    return f"({a} {op} {b})"


def rand_cond(r, vs):
    a = r.choice(vs)
    b = r.choice(vs) if r.random() < 0.5 else str(r.randrange(0, 40))
    return f"{a} {r.choice(['==', '!=', '<', '<=', '>', '>='])} {b}"


def rand_block(r, vs, depth, n):
    out = []
    for _ in range(n):
        k = r.random()
        if k < 0.40:
            op = r.choice(["=", "=", "+=", "-=", "^=", "|=", "&="])
            out.append(f"{r.choice(vs)} {op} {rand_expr(r, vs)};")
        elif k < 0.55:
            out.append(f"{r.choice(['set', 'clear', 'toggle'])}({r.randrange(1, 256):#x});")
        elif k < 0.62:
            out.append(f"write({rand_expr(r, vs, 1)});")
        elif k < 0.80 and depth > 0:
            e = ""
            if r.random() < 0.5:
                e = " else { " + " ".join(rand_block(r, vs, depth - 1, r.randrange(1, 3))) + " }"
            out.append(f"if ({rand_cond(r, vs)}) {{ " + " ".join(rand_block(r, vs, depth - 1, r.randrange(1, 3)))
                       + f" }}{e}")
        elif k < 0.93 and depth > 0:
            out.append(f"repeat ({r.randrange(1, 5)}) {{ " + " ".join(rand_block(r, vs, depth - 1, r.randrange(1, 3)))
                       + " }")
        elif depth > 0:
            out.append(f"w = {r.randrange(1, 5)}; while (w != 0) {{ w -= 1; "
                       + " ".join(rand_block(r, vs, depth - 1, r.randrange(1, 3))) + " }")
        else:
            out.append(f"{r.choice(vs)} = {rand_expr(r, vs, 1)};")
    return out


def test_compiled_code_equals_the_interpreter_on_random_programs():
    """Final variables and every change of OUT, in order, are the same."""
    for seed in range(120):
        r = random.Random(seed)
        vs = ["a", "b", "c"]
        init = " ".join(f"var {v} = {r.randrange(0, 500)};" for v in vs)
        src = init + " var w;\n" + "\n".join(rand_block(r, vs, 2, r.randrange(4, 9))) + "\nhalt;\n"
        asm, amap, regs = msfc.compile_program(src, with_vars=True)
        env, writes = msfc.interpret(src)
        m, _ = run_model(asm, 6000)
        for v in vs:
            got = m.th[0].regs[regs[v]]
            assert got == env[v], f"seed {seed}: {v} = {got} (model) vs {env[v]} (interpreter)\n{src}"
        last, exp = 0, []
        for _, val in writes:
            if val != last:
                exp.append(val)
                last = val
        got = [v for _, v in out_changes(m)]
        assert got == exp, f"seed {seed}: OUT {got} vs {exp}\n{src}"


def test_size_against_hand_written_assembly():
    """Informational and checked for sanity: the compiled UART transmitter against the
    hand-written firmware/uart_tx.s."""
    asm, _ = msfc.compile_program(open(os.path.join(FW, "uart_tx.msf")).read())
    compiled = len(assemble(asm, base_addr=128))
    hand = len(assemble(open(os.path.join(HERE, "..", "firmware", "uart_tx.s")).read(), base_addr=128))
    print(f"uart_tx: compiled {compiled} words, hand-written {hand} words")
    assert 0 < compiled < 200 and hand > 0


def test_spi_master_clocks_the_byte_out_on_an_exact_grid():
    """firmware/msfc/spi_mode0.msf: CS low, eight MOSI bits MSB first each valid at SCK's rising edge, CS
    high; every edge on a 16-cycle grid. With half a period of 4 the compiler refuses it."""
    src = open(os.path.join(FW, "spi_mode0.msf")).read()
    asm, amap = msfc.compile_program(src)
    ok, problems, _ = msfc.check(asm, amap, "spi_mode0.msf")
    assert ok, problems
    m, _ = run_model(asm, 900)
    ch = out_changes(m)
    # sample MOSI (bit 3) at every rising edge of SCK (bit 2), with CS (bit 5) low
    bits, prev = [], 0
    for c, v in ch:
        if (v & 4) and not (prev & 4):
            assert not (v & 0x20), "SCK rose with CS high"
            bits.append((v >> 3) & 1)
        prev = v
    assert sum(b << (7 - i) for i, b in enumerate(bits)) == 0xA5 and len(bits) == 8, bits
    start = next(c for c, v in ch if v & 0x20 == 0)
    assert all((c - start) % 16 == 0 for c, _ in ch if c >= start), ch
    cs_high = [c for c, v in ch if v & 0x20 and c > start]
    assert cs_high and (cs_high[-1] - start) % 16 == 0
    asm4, amap4 = msfc.compile_program(src.replace("const HALF = 16;", "const HALF = 4;"))
    ok4, problems4, _ = msfc.check(asm4, amap4, "spi_mode0.msf")
    assert not ok4 and any("spi_mode0.msf:" in p for p in problems4), problems4
