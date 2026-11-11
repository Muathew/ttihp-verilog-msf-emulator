#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""msfc: a small timing-first language for msfcore. A program that compiles is on time.

Time is syntax. A `time` variable holds a cycle count; `at t { ... }` waits until the
cycle counter equals t and runs the block; `at t set(1);` makes one pin write that the
hardware itself puts on the pad in cycle t+1 (`AT`, docs/isa.md 6.8) and waits for it, so
every such edge is exact whatever the code around it does; `every N { ... }` runs a block
with an exact period, with no drift. The compiler places the WAIT_CMPs and ATs, then runs
the deadline checker (tools/msf_timing.py) on the code it generated and refuses the
program, naming the source line, if any deadline cannot be certified.

Language (16-bit variables, no types, no functions; every program is one thread):

    const NAME = expr;            compile-time constant
    var x; var y = expr;          a variable (up to 6, with time variables and counters)
    time t; time t = now() + 20;  a cycle count (a time variable is also a register)
    x = expr;  x += expr;  (also -= &= |= ^= <<= >>=; shift counts are constants)
    if (cond) { } else { }   while (cond) { }   loop { }   repeat (n) { }   break;   halt;
    set(m); clear(m); toggle(m); write(e);      pin writes (uio): the OUT register
    oe_set(m); oe_clear(m); uo(e);              output enables, the uo pins
    wait_edge(pin, rise|fall|any|low|high);     wait for an input (no timeout)
    x = in(); x = pins(); x = now();            inputs and the cycle counter
    tx_cfg(e); tx_div(e); tx_push(e); tx_wait(); rx_cfg(e); rx_div(e); x = rx_pop();
    x = mem[e]; mem[e] = x;                     data RAM
    t += expr;  at t { ... }  at t set(m);  every N { ... }
    cond:  e == e   e != e   e < e   e <= e   e > e   e >= e   or an expression (non-zero)
    expr:  + - & | ^ << >> on variables, constants and one nested level on the left;
           < > compare as signed 16-bit (values below 32768 compare as expected)

Usage:
    python tools/msfc.py prog.msf [-o prog.s] [--no-check] [--run CYCLES]
"""
import argparse
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "test"))
sys.path.insert(0, HERE)

M16 = 0xFFFF
TEMPS = ("R6", "R7")
VAR_REGS = ["R0", "R1", "R2", "R3", "R4", "R5"]


class MsfError(Exception):
    def __init__(self, msg, line=None):
        super().__init__(f"line {line}: {msg}" if line else msg)
        self.line = line


# ---------------------------------------------------------------------------- lexer
TOKEN = re.compile(r"""\s*(?:(?P<num>0[xX][0-9a-fA-F]+|0[bB][01]+|\d+)|(?P<id>[A-Za-z_]\w*)|
    (?P<op><<=|>>=|\+=|-=|&=|\|=|\^=|==|!=|<=|>=|<<|>>|[-+&|^<>=!(){}\[\];,.~]))""", re.X)


def lex(text):
    toks, line, pos = [], 1, 0
    text = re.sub(r"//[^\n]*", lambda m: "", text)
    for ln, raw in enumerate(text.split("\n"), 1):
        pos = 0
        while pos < len(raw):
            if raw[pos:].strip() == "":
                break
            m = TOKEN.match(raw, pos)
            if not m:
                raise MsfError(f"unexpected character {raw[pos:].strip()[0]!r}", ln)
            pos = m.end()
            if m.group("num"):
                toks.append(("num", int(m.group("num"), 0), ln))
            elif m.group("id"):
                toks.append(("id", m.group("id"), ln))
            else:
                toks.append(("op", m.group("op"), ln))
    toks.append(("eof", None, line))
    return toks


# ---------------------------------------------------------------------------- parser
class Parser:
    def __init__(self, toks):
        self.t, self.i = toks, 0
        self.consts = {}

    def peek(self, k=0):
        return self.t[min(self.i + k, len(self.t) - 1)]

    def take(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def accept(self, kind, val=None):
        tok = self.peek()
        if tok[0] == kind and (val is None or tok[1] == val):
            return self.take()
        return None

    def expect(self, kind, val=None):
        tok = self.accept(kind, val)
        if tok is None:
            got = self.peek()
            want = val if val is not None else kind
            raise MsfError(f"expected {want!r}, found {got[1]!r}", got[2])
        return tok

    def program(self):
        body = []
        while self.peek()[0] != "eof":
            body.append(self.statement(top=True))
        return body

    # expressions: ("num", v) ("var", name) ("bin", op, a, b) ("now",) ("in",) ("pins",) ("rx",) ("mem", e)
    def expr(self, prec=0):
        left = self.unary()
        ops = {"|": 1, "^": 2, "&": 3, "<<": 4, ">>": 4, "+": 5, "-": 5}
        while True:
            tok = self.peek()
            if tok[0] != "op" or tok[1] not in ops or ops[tok[1]] <= prec:
                return left
            self.take()
            right = self.expr(ops[tok[1]])
            left = ("bin", tok[1], left, right, tok[2])

    def unary(self):
        tok = self.take()
        kind, val, ln = tok
        if kind == "num":
            return ("num", val)
        if kind == "op" and val == "(":
            e = self.expr()
            self.expect("op", ")")
            return e
        if kind == "op" and val == "~":
            return ("bin", "^", self.unary(), ("num", M16), ln)
        if kind == "op" and val == "-":
            return ("bin", "-", ("num", 0), self.unary(), ln)
        if kind == "id":
            if val in ("now", "in", "pins", "rx_pop"):
                self.expect("op", "(")
                self.expect("op", ")")
                return ({"now": ("now",), "in": ("in",), "pins": ("pins",), "rx_pop": ("rx",)})[val]
            if val == "mem":
                self.expect("op", "[")
                e = self.expr()
                self.expect("op", "]")
                return ("mem", e)
            if val in self.consts:
                return ("num", self.consts[val])
            return ("var", val, ln)
        raise MsfError(f"unexpected {val!r} in an expression", ln)

    def cond(self):
        a = self.expr()
        tok = self.peek()
        if tok[0] == "op" and tok[1] in ("==", "!=", "<", "<=", ">", ">="):
            self.take()
            return ("cmp", tok[1], a, self.expr(), tok[2])
        return ("cmp", "!=", a, ("num", 0), tok[2])

    def block(self):
        self.expect("op", "{")
        out = []
        while not self.accept("op", "}"):
            if self.peek()[0] == "eof":
                raise MsfError("missing '}'", self.peek()[2])
            out.append(self.statement())
        return out

    def statement(self, top=False):
        kind, val, ln = self.peek()
        if kind == "id" and val == "const":
            self.take()
            name = self.expect("id")[1]
            self.expect("op", "=")
            e = self.expr()
            self.expect("op", ";")
            self.consts[name] = const_eval(e, ln)
            return ("nop", ln)
        if kind == "id" and val in ("var", "time"):
            self.take()
            name = self.expect("id")[1]
            init = None
            if self.accept("op", "="):
                init = self.expr()
            self.expect("op", ";")
            return ("decl", val, name, init, ln)
        if kind == "id" and val == "if":
            self.take()
            self.expect("op", "(")
            c = self.cond()
            self.expect("op", ")")
            then = self.block()
            els = []
            if self.accept("id", "else"):
                els = [self.statement()] if self.peek()[:2] == ("id", "if") else self.block()
            return ("if", c, then, els, ln)
        if kind == "id" and val == "while":
            self.take()
            self.expect("op", "(")
            c = self.cond()
            self.expect("op", ")")
            return ("while", c, self.block(), ln)
        if kind == "id" and val == "loop":
            self.take()
            return ("loop", self.block(), ln)
        if kind == "id" and val == "repeat":
            self.take()
            self.expect("op", "(")
            n = self.expr()
            self.expect("op", ")")
            return ("repeat", n, self.block(), ln)
        if kind == "id" and val in ("break", "halt"):
            self.take()
            self.expect("op", ";")
            return (val, ln)
        if kind == "id" and val == "every":
            self.take()
            n = self.expr()
            return ("every", n, self.block(), ln)
        if kind == "id" and val == "at":
            self.take()
            t = self.expect("id")[1]
            if self.peek()[:2] == ("op", "{"):
                return ("at", t, self.block(), ln)
            call = self.statement()
            if call[0] != "call":
                raise MsfError("`at t` takes a block or one pin write", ln)
            return ("atpin", t, call, ln)
        if kind == "id" and val == "mem":
            self.take()
            self.expect("op", "[")
            a = self.expr()
            self.expect("op", "]")
            self.expect("op", "=")
            v = self.expr()
            self.expect("op", ";")
            return ("memw", a, v, ln)
        if kind == "id" and self.peek(1)[:2] == ("op", "("):
            name = self.take()[1]
            self.take()
            args = []
            if not self.accept("op", ")"):
                while True:
                    tok = self.peek()
                    # wait_edge's second argument is a keyword
                    args.append(("kw", self.take()[1]) if name == "wait_edge" and len(args) == 1
                                else self.expr())
                    if self.accept("op", ")"):
                        break
                    self.expect("op", ",")
            self.expect("op", ";")
            return ("call", name, args, ln)
        if kind == "id":
            name = self.take()[1]
            op = self.expect("op")[1]
            if op not in ("=", "+=", "-=", "&=", "|=", "^=", "<<=", ">>="):
                raise MsfError(f"expected an assignment, found {op!r}", ln)
            e = self.expr()
            self.expect("op", ";")
            return ("assign", name, op, e, ln)
        raise MsfError(f"unexpected {val!r}", ln)


def const_eval(e, ln=None):
    if e[0] == "num":
        return e[1] & M16
    if e[0] == "bin":
        a, b = const_eval(e[2], ln), const_eval(e[3], ln)
        return {"+": a + b, "-": a - b, "&": a & b, "|": a | b, "^": a ^ b,
                "<<": a << b, ">>": a >> b}[e[1]] & M16
    raise MsfError("a constant is needed here", ln)


def is_const(e):
    return e[0] == "num" or (e[0] == "bin" and is_const(e[2]) and is_const(e[3]))


# ---------------------------------------------------------------------------- code generator
CALLS = {"set": ("PIN_SET", 1), "clear": ("PIN_CLR", 1), "toggle": ("PIN_TGL", 1),
         "oe_set": ("OE_SET", 1), "oe_clear": ("OE_CLR", 1)}
TIMED_OK = {"set", "clear", "toggle", "write", "uo", "oe_set", "oe_clear"}
EDGE = {"rise": "rise", "fall": "fall", "any": "any", "low": "low", "high": "high"}


class Gen:
    def __init__(self, source_lines=None):
        self.out = []              # (asm text, msf line or None)
        self.vars = {}             # name -> (register, kind)
        self.labels = 0
        self.loops = []            # break labels
        self.timed_used = False

    # ---- emission
    def emit(self, text, line=None):
        self.out.append((text, line))

    def label(self, p="L"):
        self.labels += 1
        return f"{p}{self.labels}"

    def alloc(self, name, kind, line, hidden=False):
        used = {r for r, _ in self.vars.values()}
        free = [r for r in VAR_REGS if r not in used]
        if not free:
            raise MsfError("out of registers: a program has six variables, time variables and "
                           "loop counters (R0-R5)", line)
        if name in self.vars and not hidden:
            raise MsfError(f"{name} is declared twice", line)
        self.vars[name] = (free[0], kind)
        return free[0]

    def reg(self, name, line, kind=None):
        if name not in self.vars:
            raise MsfError(f"{name} is not declared", line)
        r, k = self.vars[name]
        if kind and k != kind:
            raise MsfError(f"{name} is a {k} variable; this needs a {kind} variable", line)
        return r

    def const_to(self, v, reg, line):
        v &= M16
        self.emit(f"LDR {reg}, #{v}" if v < 256 else f"LDI {reg}, {v}", line)

    # ---- expressions: the value goes to `dest`
    def operand(self, e, temp, line):
        """A register that holds e: a variable's own, or `temp` loaded with it."""
        if e[0] == "var":
            return self.reg(e[1], e[2] if len(e) > 2 else line)
        self.expr(e, temp, line)
        return temp

    def expr(self, e, dest, line):
        k = e[0]
        if k == "num":
            self.const_to(e[1], dest, line)
        elif k == "var":
            r = self.reg(e[1], e[2] if len(e) > 2 else line)
            if r != dest:
                self.emit(f"MOV {dest}, {r}", line)
        elif k == "now":
            self.emit(f"CNT_R {dest}", line)
        elif k == "in":
            self.emit(f"IN_RD {dest}", line)
        elif k == "pins":
            self.emit(f"PIN_R {dest}", line)
        elif k == "rx":
            self.emit(f"RX_POP {dest}", line)
        elif k == "mem":
            a = self.operand(e[1], TEMPS[0] if dest != TEMPS[0] else TEMPS[1], line)
            self.emit(f"LD {dest}, {a}", line)
        elif k == "bin":
            _, op, a, b, ln = e
            ln = ln or line
            if is_const(e):
                self.const_to(const_eval(e), dest, ln)
                return
            if op in ("<<", ">>"):
                if not is_const(b):
                    raise MsfError("a shift count must be a constant", ln)
                n = const_eval(b)
                self.expr(a, dest, ln)
                mn = "SHL" if op == "<<" else "SHR"
                while n > 0:
                    step = min(n, 7)
                    self.emit(f"{mn} {dest}, {dest}, #{step}", ln)
                    n -= step
                return
            # a right operand that is itself an operation: evaluate it first, into R7, when
            # the left one is simple (a variable or a constant: loading it needs no temporary)
            if b[0] in ("bin", "mem") and not is_const(b):
                if a[0] not in ("var", "num"):
                    raise MsfError("expression too complex: assign one side to a variable first", ln)
                self.expr(b, TEMPS[1], ln)
                acc = dest if dest != TEMPS[1] else TEMPS[0]
                self.expr(a, acc, ln)
                mn = {"+": "ADDS", "-": "SUBS", "&": "AND", "|": "OR", "^": "XOR"}[op]
                self.emit(f"{mn} {acc}, {acc}, R7", ln)
                if acc != dest:
                    self.emit(f"MOV {dest}, {acc}", ln)
                return
            acc = dest if not self.reads(b, dest) else TEMPS[0]
            self.expr(a, acc, ln)
            tmp = TEMPS[1] if acc == TEMPS[0] else TEMPS[0]
            rb = self.operand(b, tmp, ln)
            mn = {"+": "ADDS", "-": "SUBS", "&": "AND", "|": "OR", "^": "XOR"}[op]
            self.emit(f"{mn} {acc}, {acc}, {rb}", ln)
            if acc != dest:
                self.emit(f"MOV {dest}, {acc}", ln)
        else:
            raise MsfError(f"cannot compile {e!r}", line)

    def reads(self, e, reg):
        if e[0] == "var":
            return self.vars.get(e[1], (None,))[0] == reg
        if e[0] == "bin":
            return self.reads(e[2], reg) or self.reads(e[3], reg)
        if e[0] == "mem":
            return self.reads(e[1], reg)
        return False

    # ---- conditions: branch to `skip` when the condition is false
    def branch_false(self, c, skip, line):
        _, op, a, b, ln = c
        ln = ln or line
        if op in (">", "<="):                     # swap: a > b is b < a, a <= b is b >= a
            a, b, op = b, a, {">": "<", "<=": ">="}[op]
        if all(x[0] in ("bin", "mem") and not is_const(x) for x in (a, b)):
            raise MsfError("condition too complex: assign one side to a variable first", ln)
        ra = self.operand(a, TEMPS[0], ln)
        rb = self.operand(b, TEMPS[1], ln)
        self.emit(f"SUBS R6, {ra}, {rb}", ln)
        self.emit({"==": f"JNZ {skip}", "!=": f"JZ {skip}", "<": f"JP {skip}", ">=": f"JN {skip}"}[op], ln)

    # ---- statements
    def block(self, stmts):
        for s in stmts:
            self.stmt(s)

    def pin_call(self, name, args, line, timed_reg=None):
        """One pin write; with timed_reg an AT arms the write (docs/isa.md 6.8)."""
        if timed_reg:
            self.timed_used = True
            self.emit(f"AT {timed_reg}", line)
        if name in CALLS:
            mn, _ = CALLS[name]
            if len(args) != 1 or not is_const(args[0]):
                raise MsfError(f"{name}() takes one constant mask", line)
            self.emit(f"{mn} {const_eval(args[0]) & 0xFF}", line)
        elif name == "write":
            r = self.operand(args[0], TEMPS[0], line)
            self.emit(f"PIN_W {r}", line)
        elif name == "uo":
            r = self.operand(args[0], TEMPS[0], line)
            self.emit(f"OUT_W {r}", line)
        else:
            raise MsfError(f"{name}() cannot be timed", line)

    def stmt(self, s):
        k = s[0]
        if k == "nop":
            return
        if k == "decl":
            _, kind, name, init, ln = s
            r = self.alloc(name, "time" if kind == "time" else "var", ln)
            if init is not None:
                self.expr(init, r, ln)
            elif kind == "var":
                self.emit(f"LDR {r}, #0", ln)
        elif k == "assign":
            _, name, op, e, ln = s
            r = self.reg(name, ln)
            if op == "=":
                self.expr(e, r, ln)
            elif op in ("<<=", ">>="):
                self.expr(("bin", op[:-1], ("var", name, ln), e, ln), r, ln)
            else:
                rb = self.operand(e, TEMPS[0], ln)      # the right side first: it may read x
                mn = {"+=": "ADDS", "-=": "SUBS", "&=": "AND", "|=": "OR", "^=": "XOR"}[op]
                self.emit(f"{mn} {r}, {r}, {rb}", ln)
        elif k == "memw":
            _, a, v, ln = s
            ra = self.operand(a, TEMPS[0], ln)
            rv = self.operand(v, TEMPS[1], ln)
            self.emit(f"ST {ra}, {rv}", ln)
        elif k == "if":
            _, c, then, els, ln = s
            skip = self.label("else")
            self.branch_false(c, skip, ln)
            self.block(then)
            if els:
                end = self.label("end")
                self.emit(f"JMP {end}", ln)
                self.emit(f"{skip}:")
                self.block(els)
                self.emit(f"{end}:")
            else:
                self.emit(f"{skip}:")
        elif k == "while":
            _, c, body, ln = s
            top, end = self.label("top"), self.label("end")
            self.emit(f"{top}:")
            self.branch_false(c, end, ln)
            self.loops.append(end)
            self.block(body)
            self.loops.pop()
            self.emit(f"JMP {top}", ln)
            self.emit(f"{end}:")
        elif k == "loop":
            _, body, ln = s
            top, end = self.label("top"), self.label("end")
            self.emit(f"{top}:")
            self.loops.append(end)
            self.block(body)
            self.loops.pop()
            self.emit(f"JMP {top}", ln)
            self.emit(f"{end}:")
        elif k == "repeat":
            _, n, body, ln = s
            if not is_const(n):
                raise MsfError("repeat() takes a constant count", ln)
            cnt = f"_c{self.labels}"
            r = self.alloc(cnt, "var", ln, hidden=True)
            self.const_to(const_eval(n), r, ln)
            top, end = self.label("top"), self.label("end")
            self.emit(f"{top}:")
            self.loops.append(end)
            self.block(body)
            self.loops.pop()
            self.emit(f"DECR {r}", ln)
            self.emit(f"JNZ {top}", ln)
            self.emit(f"{end}:")
            del self.vars[cnt]
        elif k == "break":
            if not self.loops:
                raise MsfError("break outside a loop", s[1])
            self.emit(f"JMP {self.loops[-1]}", s[1])
        elif k == "halt":
            lab = self.label("halt")
            self.emit(f"{lab}: JMP {lab}", s[1])
        elif k == "at":
            _, t, body, ln = s
            rt = self.reg(t, ln, "time")
            self.emit(f"WAIT_CMP {rt}", ln)
            self.block(body)
        elif k == "atpin":
            _, t, call, ln = s
            rt = self.reg(t, ln, "time")
            self.pin_call(call[1], call[2], ln, timed_reg=rt)
            self.emit(f"WAIT_CMP {rt}", ln)       # let it land: the slot is free for the next one
        elif k == "every":
            _, n, body, ln = s
            if not is_const(n):
                raise MsfError("every takes a constant period", ln)
            cnt = f"_e{self.labels}"
            r = self.alloc(cnt, "time", ln, hidden=True)
            self.emit(f"CNT_R {r}", ln)
            top = self.label("every")
            self.emit(f"{top}:")
            self.const_to(const_eval(n), TEMPS[1], ln)
            self.emit(f"ADDS {r}, {r}, R7", ln)
            self.emit(f"WAIT_CMP {r}", ln)
            self.block(body)
            self.emit(f"JMP {top}", ln)
            del self.vars[cnt]
        elif k == "call":
            _, name, args, ln = s
            if name in CALLS or name in ("write", "uo"):
                self.pin_call(name, args, ln)
            elif name == "wait_edge":
                if len(args) != 2 or not is_const(args[0]) or args[1][0] != "kw" or args[1][1] not in EDGE:
                    raise MsfError("wait_edge(pin, rise|fall|any|low|high)", ln)
                self.emit(f"WAIT_EDGE {const_eval(args[0]) & 7}, {EDGE[args[1][1]]}", ln)
            elif name in ("tx_cfg", "tx_div", "tx_push", "rx_cfg", "rx_div"):
                r = self.operand(args[0], TEMPS[0], ln)
                self.emit(f"{name.upper()} {r}", ln)
            elif name == "tx_wait":
                self.emit("TX_WAIT", ln)
            else:
                raise MsfError(f"unknown function {name}()", ln)
        else:
            raise MsfError(f"cannot compile {k}", s[-1])


def compile_program(text, name="program", with_vars=False):
    """(assembly text, [(msf line, address)]) for a program; with `with_vars` also {name: register}."""
    p = Parser(lex(text))
    ast = p.program()
    g = Gen()
    g.emit("; compiled by tools/msfc.py: do not edit")
    g.emit("LDR R6, #0")
    g.emit("TMO R6                       ; waits never time out")
    body_start = len(g.out)
    g.block(ast)
    if g.timed_used:                      # one port page for the whole program (isa.md 3.2)
        g.out.insert(body_start, ("ESEL #2                      ; port page 1: AT", None))
    g.emit("end_of_program: JMP end_of_program")
    lines, addr, amap = [], 128, []
    for text_, ln in g.out:
        lines.append(f"{text_}" + (f"    ; msf:{ln}" if ln else ""))
        code = text_.split(";")[0].strip()
        code = re.sub(r"^[\w.]+:\s*", "", code)
        if code:
            if ln:
                amap.append((ln, addr))
            addr += 2 if code.upper().startswith("LDI ") else 1
    if with_vars:
        return "\n".join(lines) + "\n", amap, {n: int(r[1]) for n, (r, _) in g.vars.items()}
    return "\n".join(lines) + "\n", amap


def check(asm_text, amap, source_name="program"):
    """Run the deadline checker on generated code: (certified, report lines with msf lines)."""
    import msf_timing
    with tempfile.NamedTemporaryFile("w", suffix=".s", delete=False) as f:
        f.write(asm_text)
        path = f.name
    try:
        a, _, _ = msf_timing.analyse(path)
        ok = msf_timing.certified(a)
        by_addr = {addr: ln for ln, addr in amap}
        lines = []
        for pc, dl in sorted(a.deadlines.items()):
            if dl["late"] or dl["unchecked"]:
                kind = "timed pin write" if dl.get("kind") == "at" else "wait"
                lines.append(f"{source_name}:{by_addr.get(pc, '?')}: this {kind} may miss its time "
                             f"(d at issue {msf_timing.fmt(dl['lo'])}..{msf_timing.fmt(dl['hi'])})")
        for pc, msg in a.problems:
            if "not derived from CNT" not in msg:
                lines.append(f"{source_name}:{by_addr.get(pc, '?')}: {msg}")
        return ok and not lines, lines, a
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------- reference interpreter
class Stop(Exception):
    pass


def interpret(text, max_steps=100000, inputs=None, now0=0):
    """Run a program with the language's own semantics, no cycles: returns (variables, pin writes).
    Time variables are plain numbers, `at t` runs its block at once, `every N` runs 3 rounds."""
    p = Parser(lex(text))
    ast = p.program()
    env, writes, steps = {}, [], [0]
    out_reg = {"OUT": 0}
    clock = [now0]

    def ev(e):
        k = e[0]
        if k == "num":
            return e[1] & M16
        if k == "var":
            if e[1] not in env:
                raise MsfError(f"{e[1]} is not declared")
            return env[e[1]]
        if k == "now":
            clock[0] += 1
            return clock[0] & M16
        if k == "in":
            return (inputs or {}).get("in", 0) & 0xFF
        if k == "pins":
            return (inputs or {}).get("pins", 0) & 0xFF
        if k == "rx":
            return 0
        if k == "mem":
            return mem.get(ev(e[1]) & 0x7FF, 0)
        _, op, a, b, _ln = e
        x, y = ev(a), ev(b)
        return {"+": x + y, "-": x - y, "&": x & y, "|": x | y, "^": x ^ y,
                "<<": x << y, ">>": x >> y}[op] & M16

    mem = {}

    def signed(v):
        return v - 0x10000 if v & 0x8000 else v

    def cond(c):
        _, op, a, b, _ln = c
        x, y = ev(a), ev(b)
        if op == "==":
            return x == y
        if op == "!=":
            return x != y
        d = signed((x - y) & M16)                      # as the code does it: the sign of the difference
        return {"<": d < 0, ">=": d >= 0, ">": signed((y - x) & M16) < 0,
                "<=": signed((y - x) & M16) >= 0}[op]

    def run(stmts):
        for s in stmts:
            steps[0] += 1
            if steps[0] > max_steps:
                raise Stop("step limit")
            k = s[0]
            if k == "decl":
                env[s[2]] = ev(s[3]) if s[3] is not None else 0
            elif k == "assign":
                _, name, op, e, _ln = s
                v = ev(e) if op == "=" else ev(("bin", op[:-1], ("var", name), e, None))
                env[name] = v
            elif k == "memw":
                mem[ev(s[1]) & 0x7FF] = ev(s[2])
            elif k == "if":
                run(s[2] if cond(s[1]) else s[3])
            elif k == "while":
                while cond(s[1]):
                    try:
                        run(s[2])
                    except _Break:
                        break
            elif k == "loop":
                while True:
                    try:
                        run(s[1])
                    except _Break:
                        break
            elif k == "repeat":
                n = const_eval(s[1])
                for _ in range(n if n else 65536):
                    try:
                        run(s[2])
                    except _Break:
                        break
            elif k == "break":
                raise _Break()
            elif k == "halt":
                raise Stop("halt")
            elif k == "at":
                run(s[2])
            elif k == "atpin":
                run([s[2]])
            elif k == "every":
                for _ in range(3):
                    run(s[2])
            elif k == "call":
                _, name, args, _ln = s
                if name in CALLS:
                    m = const_eval(args[0]) & 0xFF
                    cur = out_reg["OUT"]
                    if name == "set":
                        cur |= m
                    elif name == "clear":
                        cur &= ~m & 0xFF
                    elif name == "toggle":
                        cur ^= m
                    if name in ("set", "clear", "toggle"):
                        out_reg["OUT"] = cur
                        writes.append(("OUT", cur))
                elif name == "write":
                    out_reg["OUT"] = ev(args[0]) & 0xFF
                    writes.append(("OUT", out_reg["OUT"]))
                elif name == "uo":
                    writes.append(("UO", ev(args[0]) & 0xFF))

    class _Break(Exception):
        pass

    try:
        run(ast)
    except (Stop, _Break):
        pass
    return env, writes


# ---------------------------------------------------------------------------- driver
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source")
    ap.add_argument("-o", help="write the assembly here")
    ap.add_argument("--no-check", action="store_true", help="skip the deadline check")
    ap.add_argument("--run", type=int, metavar="CYCLES", help="run on the ISA model and print the OUT edges")
    a = ap.parse_args()
    text = open(a.source, encoding="utf-8").read()
    try:
        asm_text, amap = compile_program(text)
    except MsfError as e:
        print(f"{a.source}: {e}", file=sys.stderr)
        return 1
    if not a.no_check:
        ok, problems, _ = check(asm_text, amap, os.path.basename(a.source))
        if not ok:
            print("refused: a deadline cannot be certified", file=sys.stderr)
            for l in problems:
                print("  " + l, file=sys.stderr)
            return 2
    if a.o:
        with open(a.o, "w", encoding="utf-8") as f:
            f.write(asm_text)
    else:
        sys.stdout.write(asm_text)
    if a.run:
        from assembler import assemble
        from isa_model import IsaModel
        words = assemble(asm_text, base_addr=128)
        m = IsaModel([assemble("JMP 128")[0]], {128 + i: w for i, w in enumerate(words)},
                     ui_pad=lambda c: 1)
        m.run(a.run)
        last = None
        for e in m.trace:
            if e[0] == "I" and e[5]["out"] != last:
                last = e[5]["out"]
                print(f"cycle {e[1]:6d}: OUT = {last:#04x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
