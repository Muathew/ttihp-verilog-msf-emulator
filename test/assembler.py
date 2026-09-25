# SPDX-License-Identifier: Apache-2.0
# msfcore micro-assembler (16-bit ISA, isa.md v1.0) + bootloader ROM emitter.

"""
Micro-assembler for the msfcore 16-bit ISA (see docs/sam/isa.md v1.0).

Encoding (16-bit words, b15..b11 = opcode):
  R: b10..b8=rd, b7..b5=rs, b4..b2=rt, b1..b0=0
  I: b10..b8=rd, b7..b0=imm8
  B: b10..b2=rel9 (signed), b1..b0=0
  A: b10..b0=imm11

Also provides:
  bootloader_words()    -> list of 16-bit words for bootloader ROM
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
    w = (op & 0x1F) << 11
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


def assemble(text, max_words=512, base_addr=0):
    """Assemble an msfcore assembly string into a list of 16-bit machine words."""
    # Pass 1: strip comments, collect labels and instructions with word addresses
    labels = {}
    instructions = []  # list of (pc, mnemonic, args, line_num)

    for line_num, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue

        # Handle multiple labels on a line or inline labels
        while ":" in line:
            prefix, remainder = line.split(":", 1)
            prefix = prefix.strip()
            # If prefix contains whitespace, it's not a sole label token
            if " " in prefix or "\t" in prefix:
                break
            labels[prefix] = base_addr + len(instructions)
            line = remainder.strip()
            if not line:
                break

        if line:
            parts = line.split(None, 1)
            mn = parts[0].upper()
            args = parts[1].strip() if len(parts) > 1 else ""
            instructions.append((base_addr + len(instructions), mn, args, line_num))

    # Pass 2: encode instructions
    words = []
    for pc, mn, args, line_num in instructions:
        if mn not in OP:
            raise ValueError(f"line {line_num}: unknown mnemonic {mn!r}")
        op = OP[mn]

        if mn == "MOV":
            # MOV rd, rs
            parts = [x.strip() for x in args.split(",")]
            if len(parts) != 2:
                raise ValueError(f"line {line_num}: MOV requires rd, rs; got {args!r}")
            words.append(_word(op, rd=_reg(parts[0]), rs=_reg(parts[1]), rt=0))

        elif mn in ("ADDS", "SUBS", "AND", "OR", "XOR"):
            parts = [x.strip() for x in args.split(",")]
            if len(parts) == 3:
                rd, rs, rt = _reg(parts[0]), _reg(parts[1]), _reg(parts[2])
            elif len(parts) == 2:
                rd, rs, rt = _reg(parts[0]), _reg(parts[0]), _reg(parts[1])
            else:
                raise ValueError(f"line {line_num}: {mn} requires 2 or 3 args; got {args!r}")
            words.append(_word(op, rd=rd, rs=rs, rt=rt))

        elif mn in ("SHL", "SHR"):
            parts = [x.strip() for x in args.split(",")]
            if len(parts) == 3:
                rd, rs = _reg(parts[0]), _reg(parts[1])
                rt = _imm(parts[2], 3)
            elif len(parts) == 2:
                rd, rs = _reg(parts[0]), _reg(parts[0])
                rt = _imm(parts[1], 3)
            else:
                raise ValueError(f"line {line_num}: {mn} requires 2 or 3 args; got {args!r}")
            words.append(_word(op, rd=rd, rs=rs, rt=rt))

        elif mn == "LDR":
            parts = [x.strip() for x in args.split(",")]
            if len(parts) != 2:
                raise ValueError(f"line {line_num}: LDR requires rd, #imm8; got {args!r}")
            words.append(_word(op, rd=_reg(parts[0]), imm=_imm(parts[1], 8)))

        elif mn in ("JZ", "JNZ", "JP", "JN"):
            tgt = args.strip().lstrip("+-")
            if tgt in labels:
                target_pc = labels[tgt]
            else:
                target_pc = int(tgt, 0)
            rel = target_pc - (pc + 1)
            if rel < -256 or rel > 255:
                raise ValueError(f"line {line_num}: branch offset {rel} out of 9-bit range (-256..255)")
            words.append(_word(op, rel=rel))

        elif mn == "JMP":
            t = args.strip()
            if t in labels:
                v = labels[t]
            else:
                v = int(t, 0)
            if v < 0 or v >= 2048:
                raise ValueError(f"line {line_num}: JMP target {v} out of imm11 range (0..2047)")
            words.append(_word(op, abs11=v))

        elif mn in ("PIN_W", "PIN_R", "CNT_R", "INCR", "DECR", "IN_RD",
                    "SHIFT_OUT", "WAIT_CMP", "MEM_W"):
            words.append(_word(op, rd=_reg(args)))

        elif mn in ("OE_SET", "OE_CLR", "WAIT_EDGE"):
            words.append(_word(op, imm=_imm(args, 8)))

        elif mn in ("NOP", "CALL", "RET", "SHIFT_IN"):
            words.append(_word(op))

        else:
            raise ValueError(f"line {line_num}: unhandled mnemonic {mn!r}")

    if len(words) > max_words:
        raise ValueError(f"program too long: {len(words)} > {max_words}")
    return words


def crc8(data, poly=0x07, init=0x00):
    """CRC-8, poly 0x07 (x^8+x^2+x+1), init 0, no reflection."""
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
    return crc


def bootloader_words():
    import os
    bootloader_path = os.path.join(os.path.dirname(__file__), "bootloader.s")
    with open(bootloader_path, "r") as f:
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
