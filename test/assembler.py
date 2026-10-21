# SPDX-License-Identifier: Apache-2.0
"""msfcore assembler / disassembler for ISA v2.0 (docs/isa.md).

    assemble(text, max_words=None, base_addr=0) -> [16-bit words]
    disassemble(word, addr=None)                -> str
    bootloader_words()                          -> boot ROM image (test/bootloader.s)
    crc8(data)                                  -> CRC-8/0x07 (boot protocol)

Syntax
    label:  MNEMONIC operands      ; comment
    Registers R0..R7. Immediates: 12, 0x1F, 0b101, 'A', labels, .equ names
    and expressions (+ - * << >> & | ~, lo(x), hi(x)). A leading '#' is optional.
Directives
    .org ADDR        move the location counter forward (gap filled with NOP)
    .word V[, V...]  raw words
    .equ NAME, V     constant (also: NAME = V); `defines=` passed to
                     assemble() take precedence (tests use this to scale
                     timing constants)
    .include "FILE"  textual include, searched in the including file's
                     directory and firmware/
Pseudo-ops
    LDI rd, V        LDR rd,#lo(V) ; LDH rd,#hi(V)   (always 2 words)
    LD ra, [rb] / ST [ra], rb are accepted as well as LD ra, rb / ST ra, rb
    FIFO_PUT rb = PW #0, rb    FIFO_CFG rb  = PW #1, rb
    FIFO_GET ra = PR ra, #0    FIFO_STAT ra = PR ra, #1
    CAP_BUF / CAP_DIV / CAP_TRIG / CAP_CTL rb = PW #2..#5, rb
    CAP_STAT ra = PR ra, #2    CAP_TPOS ra = PR ra, #3
    FILTER rb   = PW #6, rb
"""
import ast
import operator
import os
import re

OP = {
    "LDR": 0x00, "ADDS": 0x01, "SUBS": 0x02, "AND": 0x03, "OR": 0x04, "XOR": 0x05,
    "SHL": 0x06, "SHR": 0x07, "MOV": 0x08, "JMP": 0x09, "JZ": 0x0A, "JNZ": 0x0B,
    "JP": 0x0C, "JN": 0x0D, "NOP": 0x0E, "EXT": 0x0F, "PIN_W": 0x10, "PIN_R": 0x11,
    "SETM": 0x12, "CLRM": 0x13, "CNT_R": 0x14, "INCR": 0x15, "DECR": 0x16,
    "IN_RD": 0x17, "CALL": 0x18, "RET": 0x19, "WAIT_EDGE": 0x1A, "WAIT_CMP": 0x1B,
    "SHIFT_OUT": 0x1C, "SHIFT_IN": 0x1D, "MEM_W": 0x1E, "LDH": 0x1F,
}
OPNAME = {v: k for k, v in OP.items()}

# SETM / CLRM aliases: mnemonic -> (opcode, selector)
MASK_OPS = {
    "OE_SET": (0x12, 0), "PIN_SET": (0x12, 1), "PIN_TGL": (0x12, 2),
    "UO_SET": (0x12, 3), "UO_TGL": (0x12, 4), "OE_TGL": (0x12, 5),
    "OE_CLR": (0x13, 0), "PIN_CLR": (0x13, 1), "PIN_WI": (0x13, 2),
    "UO_CLR": (0x13, 3), "UO_WI": (0x13, 4), "OE_WI": (0x13, 5),
}
MASK_NAME = {v: k for k, v in MASK_OPS.items()}

# EXT group: mnemonic -> (sub, operand pattern) ; 'a' = ra (b10..8), 'b' = rb (b7..5)
EXT = {
    "SR_LD": (0, "b"), "SR_RD": (1, "a"), "XFER": (2, ""), "SER_CFG": (3, "b"),
    "OUT_W": (4, "b"), "MEM_A": (5, "b"), "LD": (6, "ab"), "ST": (7, "ab"),
    "SWAP": (8, "ab"), "JR": (9, "b"), "TMO": (10, "b"), "STAT": (11, "a"),
    "TX_CFG": (12, "b"), "TX_DIV": (13, "b"), "TX_PUSH": (14, "b"), "TX_WAIT": (15, ""),
    "RX_CFG": (16, "b"), "RX_DIV": (17, "b"), "RX_POP": (18, "a"), "CRC_CFG": (19, "b"),
    "CRC_W": (20, "b"), "CRC_RDL": (21, "a"), "CRC_RDH": (22, "a"), "CRC_SETL": (23, "b"),
    "CRC_SETH": (24, "b"), "CRC_POLL": (25, "b"), "CRC_POLH": (26, "b"),
    "TSTART": (27, "b"), "TSTOP": (28, ""), "TID": (29, "a"),
}
EXT_NAME = {v[0]: (k, v[1]) for k, v in EXT.items()}

# Peripheral ports: PW #port, rb (port in the ra field) / PR ra, #port (port
# in the rb field), and names for the ports in use: mnemonic -> (sub, port)
X_PW, X_PR = 30, 31
PORT_OPS = {"FIFO_PUT": (X_PW, 0), "FIFO_CFG": (X_PW, 1),
            "FIFO_GET": (X_PR, 0), "FIFO_STAT": (X_PR, 1),
            "CAP_BUF": (X_PW, 2), "CAP_DIV": (X_PW, 3), "CAP_TRIG": (X_PW, 4),
            "CAP_CTL": (X_PW, 5), "CAP_STAT": (X_PR, 2), "CAP_TPOS": (X_PR, 3),
            "FILTER": (X_PW, 6)}
PORT_NAME = {v: k for k, v in PORT_OPS.items()}

WAIT_COND = {"any": 0, "rise": 1, "rising": 1, "fall": 2, "falling": 2,
             "low": 4, "high": 5}

R_OPS = ("ADDS", "SUBS", "AND", "OR", "XOR")
REG_I_OPS = ("PIN_W", "PIN_R", "CNT_R", "INCR", "DECR", "IN_RD", "SHIFT_OUT",
             "WAIT_CMP", "MEM_W")
BRANCHES = ("JZ", "JNZ", "JP", "JN")


class AsmError(ValueError):
    pass


_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.LShift: operator.lshift, ast.RShift: operator.rshift,
           ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor}


def _reg(tok):
    t = tok.strip().strip("[]").strip().upper()
    if not re.fullmatch(r"R[0-7]", t):
        raise AsmError(f"invalid register: {tok!r}")
    return int(t[1])


def _split(args):
    return [a.strip() for a in args.split(",")] if args.strip() else []


def _eval(expr, syms):
    e = expr.strip().lstrip("#").strip()
    e = re.sub(r"'(\\?.)'", lambda m: str(ord(m.group(1)[-1])), e)
    try:
        tree = ast.parse(e, mode="eval")
    except SyntaxError:
        raise AsmError(f"bad expression {expr!r}")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, int):
            return n.value
        if isinstance(n, ast.Name):
            if n.id in syms:
                return syms[n.id]
            raise KeyError(n.id)
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            return {ast.USub: -v, ast.UAdd: v, ast.Invert: ~v}[type(n.op)]
        if isinstance(n, ast.BinOp):
            fn = _BINOPS.get(type(n.op))
            if fn is None:
                raise AsmError(f"operator not allowed in {expr!r}")
            return fn(ev(n.left), ev(n.right))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and len(n.args) == 1:
            v = ev(n.args[0])
            if n.func.id == "lo":
                return v & 0xFF
            if n.func.id == "hi":
                return (v >> 8) & 0xFF
        raise AsmError(f"unsupported expression {expr!r}")

    return ev(tree)


def _imm(expr, syms, bits, signed=False):
    v = _eval(expr, syms)
    lo, hi = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
    if not lo <= v <= hi:
        raise AsmError(f"value {v} of {expr!r} out of range for {bits} bits")
    return v


def _size(mn):
    return 2 if mn == "LDI" else 1


def _parse_lines(text):
    """Yield (line_no, label_list, mnemonic, args) with comments stripped."""
    for ln, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        labels = []
        while True:
            m = re.match(r"^([A-Za-z_.][\w.]*)\s*:(.*)$", line)
            if not m:
                break
            labels.append(m.group(1))
            line = m.group(2).strip()
        if not line:
            yield ln, labels, None, ""
            continue
        m = re.match(r"^([A-Za-z_][\w]*)\s*=\s*(.+)$", line)
        if m:
            yield ln, labels, ".EQU", f"{m.group(1)}, {m.group(2)}"
            continue
        parts = line.split(None, 1)
        yield ln, labels, parts[0].upper(), parts[1].strip() if len(parts) > 1 else ""


FIRMWARE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "firmware")


def _expand_includes(text, dirs, depth=0):
    if depth > 8:
        raise AsmError(".include nested too deeply")
    out = []
    for raw in text.splitlines():
        m = re.match(r'^\s*\.include\s+"([^"]+)"', raw, flags=re.I)
        if not m:
            out.append(raw)
            continue
        for d in dirs:
            path = os.path.join(d, m.group(1))
            if os.path.exists(path):
                with open(path) as f:
                    out.append(_expand_includes(f.read(), [os.path.dirname(path)] + dirs, depth + 1))
                break
        else:
            raise AsmError(f"include file {m.group(1)!r} not found in {dirs}")
    return "\n".join(out)


def assemble_file(path, base_addr=128, defines=None, max_words=None):
    with open(path) as f:
        return assemble(f.read(), max_words, base_addr, defines,
                        include_dirs=[os.path.dirname(os.path.abspath(path))])


def assemble(text, max_words=None, base_addr=0, defines=None, include_dirs=None):
    """Assemble msfcore source into a list of 16-bit words starting at base_addr
    (by default it must end within the 1024-word address space)."""
    if max_words is None:
        max_words = 1024 - base_addr
    text = _expand_includes(text, (include_dirs or []) + [FIRMWARE_DIR])
    defines = dict(defines or {})
    syms = dict(defines)
    # pass 1: addresses
    lines = list(_parse_lines(text))
    pc = base_addr
    for ln, labels, mn, args in lines:
        for lab in labels:
            if lab in syms:
                raise AsmError(f"line {ln}: duplicate label {lab!r}")
            syms[lab] = pc
        if mn is None:
            continue
        if mn == ".EQU":
            name, val = [a.strip() for a in args.split(",", 1)]
            if name not in defines:
                syms[name] = _eval(val, syms)
        elif mn == ".ORG":
            new = _eval(args, syms)
            if new < pc:
                raise AsmError(f"line {ln}: .org {new} is behind the location counter {pc}")
            pc = new
        elif mn == ".WORD":
            pc += len(_split(args))
        else:
            pc += _size(mn)

    # pass 2: encode
    words = []
    pc = base_addr
    for ln, labels, mn, args in lines:
        if mn is None or mn == ".EQU":
            continue
        try:
            if mn == ".ORG":
                new = _eval(args, syms)
                words += [0x7000] * (new - pc)
                pc = new
                continue
            if mn == ".WORD":
                for a in _split(args):
                    v = _eval(a, syms)
                    if not -0x8000 <= v <= 0xFFFF:
                        raise AsmError(f".word value {v} out of 16-bit range")
                    words.append(v & 0xFFFF)
                    pc += 1
                continue
            enc = _encode(mn, args, syms, pc)
        except KeyError as e:
            raise AsmError(f"line {ln}: undefined symbol {e.args[0]!r}")
        except AsmError as e:
            raise AsmError(f"line {ln}: {e}")
        words += enc
        pc += len(enc)

    if len(words) > max_words:
        raise AsmError(f"program too long: {len(words)} > {max_words}")
    return words


def _encode(mn, args, syms, pc):
    a = _split(args)

    def need(n):
        if len(a) != n:
            raise AsmError(f"{mn} expects {n} operand(s), got {args!r}")

    if mn == "LDI":
        need(2)
        rd = _reg(a[0])
        v = _eval(a[1], syms) & 0xFFFF
        return [(0x00 << 11) | rd << 8 | (v & 0xFF), (0x1F << 11) | rd << 8 | (v >> 8)]
    if mn in MASK_OPS:
        need(1)
        op, sel = MASK_OPS[mn]
        return [op << 11 | sel << 8 | _imm(a[0], syms, 8)]
    if mn in EXT:
        sub, pat = EXT[mn]
        need(len(pat))
        ra = _reg(a[pat.index("a")]) if "a" in pat else 0
        rb = _reg(a[pat.index("b")]) if "b" in pat else 0
        return [0x0F << 11 | ra << 8 | rb << 5 | sub]
    if mn == "PW":
        need(2)
        return [0x0F << 11 | _imm(a[0], syms, 3) << 8 | _reg(a[1]) << 5 | X_PW]
    if mn == "PR":
        need(2)
        return [0x0F << 11 | _reg(a[0]) << 8 | _imm(a[1], syms, 3) << 5 | X_PR]
    if mn in PORT_OPS:
        need(1)
        sub, port = PORT_OPS[mn]
        r = _reg(a[0])
        return [0x0F << 11 | (port << 8 | r << 5 if sub == X_PW else r << 8 | port << 5) | sub]
    if mn not in OP:
        raise AsmError(f"unknown mnemonic {mn!r}")
    op = OP[mn]
    if mn in R_OPS:
        if len(a) == 2:
            a = [a[0], a[0], a[1]]
        need(3)
        return [op << 11 | _reg(a[0]) << 8 | _reg(a[1]) << 5 | _reg(a[2]) << 2]
    if mn in ("SHL", "SHR"):
        if len(a) == 2:
            a = [a[0], a[0], a[1]]
        need(3)
        return [op << 11 | _reg(a[0]) << 8 | _reg(a[1]) << 5 | _imm(a[2], syms, 3) << 2]
    if mn == "MOV":
        need(2)
        return [op << 11 | _reg(a[0]) << 8 | _reg(a[1]) << 5]
    if mn in ("LDR", "LDH"):
        need(2)
        return [op << 11 | _reg(a[0]) << 8 | _imm(a[1], syms, 8)]
    if mn in BRANCHES:
        need(1)
        rel = _eval(a[0], syms) - (pc + 1)
        if not -256 <= rel <= 255:
            raise AsmError(f"branch offset {rel} out of range (-256..255)")
        return [op << 11 | (rel & 0x1FF) << 2]
    if mn in ("JMP", "CALL"):
        need(1)
        return [op << 11 | _imm(a[0], syms, 11)]
    if mn in REG_I_OPS:
        need(1)
        return [op << 11 | _reg(a[0]) << 8]
    if mn == "WAIT_EDGE":
        if len(a) == 1:
            return [op << 11 | _imm(a[0], syms, 8)]
        if len(a) not in (2, 3):
            raise AsmError("WAIT_EDGE pin[, any|rise|fall|low|high[, uio|ui]]")
        pin = _imm(a[0], syms, 3)
        cond = WAIT_COND.get(a[1].lower())
        if cond is None:
            raise AsmError(f"bad WAIT_EDGE condition {a[1]!r}")
        bank = 0
        if len(a) == 3:
            if a[2].lower() not in ("uio", "ui"):
                raise AsmError(f"bad WAIT_EDGE bank {a[2]!r}")
            bank = a[2].lower() == "ui"
        return [op << 11 | cond << 4 | bank << 3 | pin]
    if mn in ("NOP", "RET", "SHIFT_IN"):
        need(0)
        return [op << 11]
    raise AsmError(f"unhandled mnemonic {mn!r}")


def disassemble(w, addr=None):
    """One-line disassembly of a 16-bit word (for trace / error messages)."""
    op = (w >> 11) & 0x1F
    a, b, c, imm8 = (w >> 8) & 7, (w >> 5) & 7, (w >> 2) & 7, w & 0xFF
    mn = OPNAME[op]
    if op in (0x01, 0x02, 0x03, 0x04, 0x05):
        return f"{mn} R{a}, R{b}, R{c}"
    if op in (0x06, 0x07):
        return f"{mn} R{a}, R{b}, #{c}"
    if op == 0x08:
        return f"MOV R{a}, R{b}"
    if op in (0x00, 0x1F):
        return f"{mn} R{a}, #{imm8:#04x}"
    if op in (0x09, 0x18):
        return f"{mn} {w & 0x3FF}"
    if 0x0A <= op <= 0x0D:
        rel = (w >> 2) & 0x1FF
        rel = rel - 512 if rel & 0x100 else rel
        return f"{mn} {addr + 1 + rel}" if addr is not None else f"{mn} {rel:+d}"
    if op in (0x12, 0x13):
        return f"{MASK_NAME.get((op, a), 'NOP')} {imm8:#04x}"
    if op == 0x0F:
        sub = w & 0x1F
        if sub in (X_PW, X_PR):
            port, r = (a, b) if sub == X_PW else (b, a)
            name = PORT_NAME.get((sub, port))
            if name:
                return f"{name} R{r}"
            return f"PW #{port}, R{r}" if sub == X_PW else f"PR R{r}, #{port}"
        if sub not in EXT_NAME:
            return "EXT-NOP"
        name, pat = EXT_NAME[w & 0x1F]
        ops = [f"R{a}" if p == "a" else f"R{b}" for p in pat]
        return f"{name} {', '.join(ops)}".strip()
    if op == 0x1A:
        return f"WAIT_EDGE {imm8:#04x}"
    if OPNAME[op] in REG_I_OPS:
        return f"{mn} R{a}"
    return mn


def crc8(data, poly=0x07, init=0x00):
    """CRC-8, poly 0x07 (x^8+x^2+x+1), init 0, no reflection."""
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
    return crc


def bootloader_words():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bootloader.s")
    with open(path) as f:
        return assemble(f.read(), max_words=128)


if __name__ == "__main__":
    words = bootloader_words()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src",
                       "tt_um_msf_bootrom_data.v")
    with open(out, "w", newline="\n") as f:
        f.write("// Auto-generated by test/assembler.py from test/bootloader.s -- do not edit.\n")
        f.write("// Fills `rom[]` of tt_um_msf_bootrom (128 x 16).\n")
        f.write("initial begin\n")
        for i in range(128):
            w = words[i] if i < len(words) else 0x7000
            f.write(f"  rom[{i}] = 16'h{w:04x};\n")
        f.write("end\n")
    print(f"bootloader: {len(words)}/128 words -> {os.path.normpath(out)}")
