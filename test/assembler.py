# SPDX-License-Identifier: Apache-2.0
# msfcore micro-assembler (16-bit ISA, isa.md v1.0) + bootloader ROM emitter.
# STATUS: `__main__` path is BROKEN -- assemble() raises on a 2-operand
# R-format line (a,b,c unpack; unfixed); OP table was REALIGNED to the
# final opcode map (NOP=0xE, MEM_W=0x1E), matching tt_um_msf_ctrl's cases.
# INTEGRATION: `python assembler.py` (in test/) regenerates
# src/tt_um_msf_bootrom_data.v (initial block, 128 words, 0-word padded).
# KNOWN BUGS: label mapping assumes a sole-token-per-line for a label only;
# inline label parsing rewrites lines[] DURING the indexing loop (works, fragile).
# NEXT: fix the R-format arg unpack; round-trip test on a blink firmware.

"""
Micro-assembler for the msfcore 16-bit ISA (see docs/sam/isa.md v1.0).

Encoding (16-bit words, b15..b11 = opcode):
  R: b10..b8=rd, b7..b5=rs, b4..b2=rt, b1..b0=0
  I: b10..b8=rd, b7..b0=imm8
  B: b10..b2=rel9 (signed), b1..b0=0
  A: b10..b0=imm11

Also provides:
  assemble_bootloader() -> list of 16-bit words (used to generate the ROM)
  crc8(data)            -> CRC-8 (poly 0x07, init 0x00, no reflection)
"""

OP = {
    # Data ops (R/I formats)
    "LDR": 0b00000, "ADDS": 0b00001, "SUBS": 0b00010, "AND": 0b00011,
    "OR": 0b00100, "XOR": 0b00101, "SHL": 0b00110, "SHR": 0b00111,
    "MOV": 0b01000, "JMP": 0b01001, "JZ": 0b01010, "JNZ": 0b01011,
    "JP": 0b01100, "JN": 0b01101, "NOP": 0b01110,
    # Pin I/O
    "PIN_W": 0b10000, "PIN_R": 0b10001, "OE_SET": 0b10010,
    "OE_CLR": 0b10011, "CNT_R": 0b10100, "INCR": 0b10101,
    "DECR": 0b10110, "IN_RD": 0b10111,
    # Subroutine / events / shift
    "CALL": 0b11000, "RET": 0b11001, "WAIT_EDGE": 0b11010,
    "WAIT_CMP": 0b11011, "SHIFT_OUT": 0b11100, "SHIFT_IN": 0b11101,
    "MEM_W": 0b11110,
}

REGS = {f"R{i}": i for i in range(8)}


def _reg(tok):
    t = tok.strip().upper()
    if t not in REGS:
        raise ValueError(f"invalid register: {tok!r}")
    return REGS[t]


def _imm(tok, bits=8):
    t = tok.strip().lstrip("#")
    v = int(t, 0)
    if v < 0 or v >= (1 << bits):
        raise ValueError(f"immediate {t!r} out of range for {bits} bits")
    return v


def _word(op, rd=0, rs=0, rt=0, imm=0, rel=0, abs11=0):
    w = op << 11
    if op in (0b00001, 0b00010, 0b00011, 0b00100, 0b00101,
             0b00110, 0b00111, 0b01000):
        w |= (rd & 0x7) << 8 | (rs & 0x7) << 5 | (rt & 0x7) << 2
    elif op in (0b01010, 0b01011, 0b01100, 0b01101):
        w |= ((rel & 0x1FF) << 2)
    elif op == 0b01001:
        w |= (abs11 & 0x7FF)
    else:  # I-format
        w |= (rd & 0x7) << 8 | (imm & 0xFF)
    return w


def assemble(text, max_words=512):
    """Assemble a program (list of lines) into a list of 16-bit words."""
    lines = []
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        if line:
            lines.append(line)

    # Labels: "lab:" alone on a line, or "lab:" inline before an instruction.
    labels = {}
    for i, line in enumerate(lines):
        parts = line.split(None, 1)
        if parts[0].endswith(":") and len(parts[0]) > 1:
            labels[parts[0][:-1]] = i
            # Rejoin inline label with its instruction
            if len(parts) > 1:
                lines[i] = parts[1]
            else:
                lines[i] = ""  # label-only line -> skip

    words = []
    for i, line in enumerate(lines):
        if not line:
            continue
        parts = line.split(None, 1)
        mn = parts[0].upper()
        if mn not in OP:
            raise ValueError(f"line {i}: unknown mnemonic {mn!r}")
        args = parts[1].strip() if len(parts) > 1 else ""
        op = OP[mn]

        if mn == "MOV":
            # MOV rd, rs (2-operand form; rt field = 0)
            a, b = [x.strip() for x in args.split(",")]
            words.append(_word(op, _reg(a), _reg(b), 0))
        elif mn in ("ADDS", "SUBS", "AND", "OR", "XOR", "SHL", "SHR"):
            a, b, c = [x.strip() for x in args.split(",")]
            rd, rs = _reg(a), _reg(b)
            if mn in ("SHL", "SHR"):
                rt = _imm(c, 3)
            else:
                rt = _reg(c)
            words.append(_word(op, rd, rs, rt))
        elif mn == "LDR":
            a, b = args.split(",")
            words.append(_word(op, _reg(a), 0, 0, _imm(b)))
        elif mn in ("JZ", "JNZ", "JP", "JN"):
            tgt = args.strip().lstrip("+-")
            if tgt not in labels:
                raise ValueError(f"line {i}: unknown label {tgt!r}")
            rel = labels[tgt] - (i + 1)
            if rel < -256 or rel > 255:
                raise ValueError(f"line {i}: branch offset {rel} out of rel9 range")
            words.append(_word(op, rel=rel))
        elif mn == "JMP":
            t = args.strip()
            if t in labels:
                v = labels[t]
            else:
                v = int(t, 0)
            if v >= 2048:
                raise ValueError(f"line {i}: JMP target {v} out of imm11 range")
            words.append(_word(op, abs11=v))
        elif mn in ("PIN_W", "PIN_R", "CNT_R", "INCR", "DECR", "IN_RD",
                    "SHIFT_OUT", "WAIT_CMP", "MEM_W"):
            # I-format: register field at b10..b8
            words.append(_word(op, _reg(args)))
        elif mn == "OE_SET" or mn == "OE_CLR":
            words.append(_word(op, 0, 0, 0, _imm(args)))
        elif mn == "WAIT_EDGE":
            words.append(_word(op, 0, 0, 0, _imm(args)))
        elif mn in ("NOP", "CALL", "RET", "SHIFT_IN"):
            words.append(_word(op))
        else:
            raise ValueError(f"line {i}: bad args {args!r} for {mn}")

    if len(words) > max_words:
        raise ValueError(f"program too long: {len(words)} > {max_words}")
    return words


def crc8(data, poly=0x07, init=0x00):
    """CRC-8, poly 0x07 (x^8+x^2+x+1), init 0, no reflection (matches bootloader)."""
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
    return crc


def bootloader_words():
    with open(__file__.replace("assembler.py", "bootloader.s"), "r") as f:
        return assemble(f.read(), max_words=128)


if __name__ == "__main__":
    import os
    words = bootloader_words()
    assert len(words) <= 128, f"bootloader too long: {len(words)}/128"
    out = os.path.join(os.path.dirname(__file__), "..", "src",
                       "tt_um_msf_bootrom_data.v")
    with open(out, "w") as f:
        f.write("// Auto-generated by test/assembler.py -- do not edit.\n")
        f.write("// Fills `rom[]` of tt_um_msf_bootrom (128 x 16).\n")
        f.write("initial begin\n")
        for i in range(128):
            w = words[i] if i < len(words) else 0
            f.write(f"  rom[{i}] = 16'h{w:04x};\n")
        f.write("end\n")
    print(f"bootloader: {len(words)} words -> {out}")
