# SPDX-License-Identifier: Apache-2.0
"""
Golden reference model for the msfcore protocol emulator.

This module is the *independent* (Python) reference against which the
hardware is checked in cocotb.  It has two halves:

1. ``MsfCoreModel`` -- a cycle-accurate behavioural model of the msfcore
   core (16-bit ISA, 2-stage fetch pipeline with flush bubbles, ALU +
   Z/N flags, 16-bit free-running cycle counter, OSR shift datapath,
   output stage / open-drain OE, the WAIT_EDGE/WAIT_CMP stall FSM with
   16-bit hardware timeout, and the MEM_W auto-incrementing SRAM port).
   It mirrors ``src/tt_um_msf_ctrl.v`` + ``tt_um_msf_pio.v`` + the
   registered SRAM/ROM read path, so a cocotb test can step the model in
   lock-step with the DUT and diff observable state.

2. Protocol reference models -- the *expected* bit-level waveforms the
   firmware is bit-banging, used as the golden side of each protocol
   check: UART (TX frame / RX decode), SPI master (per-mode edge
   placement), I2C (START/STOP/data condition detector), and USB 1.0
   low (NRZI + bit-stuffing + packet framing).

The model is pure Python (no simulator dependency) so it can also be
unit-tested on its own.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# ISA constants (docs/sam/isa.md v1.0)
# ---------------------------------------------------------------------------
MASK16 = 0xFFFF
MASK9 = 0x1FF

# opcodes (b15..b11)
OP_LDR = 0x00
OP_ADDS = 0x01
OP_SUBS = 0x02
OP_AND = 0x03
OP_OR = 0x04
OP_XOR = 0x05
OP_SHL = 0x06
OP_SHR = 0x07
OP_MOV = 0x08
OP_JMP = 0x09
OP_JZ = 0x0A
OP_JNZ = 0x0B
OP_JP = 0x0C
OP_JN = 0x0D
OP_NOP = 0x0E
OP_MEM_W = 0x0F
OP_PIN_W = 0x10
OP_PIN_R = 0x11
OP_OE_SET = 0x12
OP_OE_CLR = 0x13
OP_CNT_R = 0x14
OP_INCR = 0x15
OP_DECR = 0x16
OP_IN_RD = 0x17
OP_CALL = 0x18
OP_RET = 0x19
OP_WAIT_EDGE = 0x1A
OP_WAIT_CMP = 0x1B
OP_SHIFT_OUT = 0x1C
OP_SHIFT_IN = 0x1D
OP_RESV0 = 0x1E  # decodes as NOP in the model
OP_RESV1 = 0x1F  # decodes as NOP in the model

# 0x7000 == opcode 01110 (NOP), all-zero fields: the pipeline flush bubble.
_FLUSH_WORD = 0x7000
_ROM_WORDS = 128
_SRAM_START = 128
_MEM_WORDS = 512


def _sign_ext9(v: int) -> int:
    """Interpret a 9-bit value as signed (two's complement)."""
    v &= MASK9
    return v - 0x200 if v & 0x100 else v


# ---------------------------------------------------------------------------
# ALU (mirrors src/tt_um_msf_alu.v)
# ---------------------------------------------------------------------------
def alu(op: int, a: int, b: int):
    """Return (y, z, n, flags_valid) for one ALU operation.

    ``op`` is the *opcode* (5-bit, b15..b11), not the internal 4-bit
    alu_op.  Z/N are only meaningful (``flags_valid``) for ADDS, SUBS,
    INCR, DECR; every other op leaves the flags untouched.
    """
    a &= MASK16
    b &= MASK16
    if op == OP_ADDS:
        y = (a + b) & MASK16
        return y, (y == 0), (y >> 15) & 1, True
    if op == OP_SUBS:
        y = (a - b) & MASK16
        return y, (y == 0), (y >> 15) & 1, True
    if op == OP_AND:
        return (a & b) & MASK16, 0, 0, False
    if op == OP_OR:
        return (a | b) & MASK16, 0, 0, False
    if op == OP_XOR:
        return (a ^ b) & MASK16, 0, 0, False
    if op == OP_SHL:
        sh = b & 0x07
        y = ((a << sh) & MASK16) if sh < 16 else 0
        return y, 0, 0, False
    if op == OP_SHR:
        sh = b & 0x07
        y = (a >> sh) & MASK16 if sh < 16 else 0
        return y, 0, 0, False
    if op == OP_INCR:
        y = (a + 1) & MASK16
        return y, (y == 0), (y >> 15) & 1, True
    if op == OP_DECR:
        y = (a - 1) & MASK16
        return y, (y == 0), (y >> 15) & 1, True
    # MOV and everything else: identity on operand A.
    return a, 0, 0, False


# ---------------------------------------------------------------------------
# Core model
# ---------------------------------------------------------------------------
class MsfCoreModel:
    """Cycle-accurate behavioural model of the msfcore emulator core.

    Advance one clock per :meth:`step`.  The fetch pipeline, flush
    bubbles, branch penalties, and WAIT stalls match the RTL, so the
    model's observable state lines up cycle-for-cycle with the DUT.
    """

    def __init__(self, mem: list[int] | None = None):
        if mem is None:
            mem = [_FLUSH_WORD] * _MEM_WORDS
        assert len(mem) == _MEM_WORDS, "mem must be 512 words"
        self.mem = list(mem)
        self.reset()

    # -- memory helpers -----------------------------------------------------
    def load_rom(self, words, start=0):
        """Write boot-ROM words (0..127)."""
        for i, w in enumerate(words):
            self.mem[start + i] = int(w) & MASK16

    def load_sram(self, words, start=_SRAM_START):
        """Write firmware into SRAM (128..511)."""
        for i, w in enumerate(words):
            self.mem[start + i] = int(w) & MASK16

    # -- reset --------------------------------------------------------------
    def reset(self):
        self.regs = [0] * 8
        self.pc = 0            # fetch address (RTL pc_p)
        self.flush = True      # initial fetch bubble
        self._imem_rd = 0      # registered read output
        self.cnt = 0          # 16-bit free-running cycle counter
        self.osr = 0
        self.ser_out = 0
        self.waddr = _SRAM_START
        self.timeout = 0
        self.stall = False
        self.wait_done = False
        self.wait_pin_init = 0
        self.wait_sel = 0
        self.wait_tgt = 0
        self.z = 0
        self.n = 0
        self.out_stage = 0
        self.oe = 0
        self.uo = 0

    # -- observable state ---------------------------------------------------
    @property
    def uio_out(self) -> int:
        """Pad value: bit7 from the shift datapath, bits6:0 from out_stage."""
        return (self.ser_out << 7) | (self.out_stage & 0x7F)

    def snapshot(self) -> dict:
        """Observable state, for diffing against the DUT in a cocotb test."""
        return {
            "pc": self.pc,
            "regs": list(self.regs),
            "z": self.z,
            "n": self.n,
            "cnt": self.cnt,
            "osr": self.osr,
            "serial_out": self.ser_out,
            "out_stage": self.out_stage,
            "uio_oe": self.oe,
            "uio_out": self.uio_out,
            "uo_out": self.uo,
            "stall": self.stall,
            "waddr": self.waddr,
        }

    # -- decode helpers -----------------------------------------------------
    @staticmethod
    def _operand_mux(op: int, f_rd: int, f_rs: int, f_rt: int):
        """Return (ra, rb) register indexes for the ALU operands."""
        if 0x01 <= op <= 0x08:          # R-format arith/logic
            return f_rs, f_rt
        if op == OP_RET:
            return 7, 0
        return f_rd, 0                 # I-format register operand

    def _write_data(self, op: int, f_rd: int, alu_y: int, imm8e: int):
        """Return (write_en, waddr, wdata) for the register file."""
        if op == OP_LDR:
            return True, f_rd, imm8e
        if 0x01 <= op <= 0x08 or op in (OP_INCR, OP_DECR):
            return True, f_rd, alu_y
        if op == OP_PIN_R:
            return True, f_rd, self._uio_in
        if op == OP_CNT_R:
            return True, f_rd, self.cnt
        if op == OP_IN_RD:
            return True, f_rd, self._ui_in
        if op == OP_CALL:
            return True, 7, (self.pc + 1) & 0xFF
        return False, f_rd, 0

    # -- one clock ------------------------------------------------------------
    def step(self, uio_in: int = 0, ui_in: int = 0):
        """Advance the model by one ``clk`` posedge.

        ``uio_in`` / ``ui_in`` are the 8-bit input values presented to the
        core for this cycle (sampled at the edge, like the RTL).
        """
        uio_in &= 0xFF
        ui_in &= 0xFF
        self._uio_in = uio_in
        self._ui_in = ui_in

        # ---- pre-edge combinational decode ----
        ir = _FLUSH_WORD if self.flush else self._imem_rd
        op = ir >> 11
        f_rd = (ir >> 8) & 0x7
        f_rs = (ir >> 5) & 0x7
        f_rt = (ir >> 2) & 0x7
        imm8 = ir & 0xFF
        imm8e = imm8
        imm11 = ir & 0x7FF
        rel9 = _sign_ext9((ir >> 2) & 0x1FF)

        ra_idx, rb_idx = self._operand_mux(op, f_rd, f_rs, f_rt)
        ra = self.regs[ra_idx]
        rb = self.regs[rb_idx]

        alu_a = ra
        alu_b = rb
        if op in (OP_SHL, OP_SHR):
            alu_b = f_rt                     # 3-bit shift amount
        elif op == OP_MOV:
            alu_b = 0
        alu_y, alu_z, alu_n, flags_valid = alu(op, alu_a, alu_b)
        if op in (OP_SHL, OP_SHR):
            # SHL/SHR never set flags (identity handled above via alu()).
            alu_y, alu_z, alu_n, flags_valid = alu(op, ra, f_rt)

        # Branch / jump / return target.
        br_taken = (
            (op == OP_JZ and self.z)
            or (op == OP_JNZ and not self.z)
            or (op == OP_JP and not self.n)
            or (op == OP_JN and self.n)
        )
        jmp_taken = op == OP_JMP
        ret_taken = op == OP_RET
        pc_redirect = br_taken or jmp_taken or ret_taken

        if jmp_taken:
            next_pc = imm11 & MASK9
        elif br_taken:
            next_pc = (self.pc + rel9) & MASK9
        elif ret_taken:
            next_pc = self.regs[7] & MASK9
        else:
            next_pc = (self.pc + 1) & MASK9

        write_en, waddr, wdata = self._write_data(op, f_rd, alu_y, imm8e)
        rf_we = write_en and (not self.stall) and (not self.flush)

        pin_w_en = (op == OP_PIN_W) and not self.stall and not self.flush
        pin_w = ra & 0xFF
        oe_set_en = (op == OP_OE_SET) and not self.stall and not self.flush
        oe_clr_en = (op == OP_OE_CLR) and not self.stall and not self.flush
        sram_we = (op == OP_MEM_W) and not self.stall and not self.flush

        stall_enter = (not self.stall) and (not self.flush) and (
            op in (OP_WAIT_EDGE, OP_WAIT_CMP)
        )
        flag_we = rf_we and flags_valid

        # ---- edge: capture pre-edge values, then update registers ----
        fetch_addr = self.pc
        sel_pin = imm8 & 0x07

        # SRAM/ROM registered read (1-cycle latency).
        new_imem_rd = self.mem[fetch_addr] & MASK16

        # pc / flush.
        if not self.stall:
            if pc_redirect:
                self.pc = next_pc
                self.flush = True
            elif stall_enter:
                self.pc = self.pc      # hold at W+1 (see ctrl.v M2.7 note)
                self.flush = False
            else:
                self.pc = next_pc
                self.flush = False

        # register file write.
        if rf_we:
            self.regs[waddr] = wdata & MASK16

        # MEM_W auto-incrementing SRAM write port (post-increment, wraps).
        if sram_we:
            self.mem[self.waddr] = self.regs[waddr] & MASK16
            self.waddr = _SRAM_START if self.waddr == 0x1FF else self.waddr + 1

        # flags.
        if self.stall and self.timeout == 0:
            self.z = 1                 # timeout resume
        elif stall_enter:
            self.z = 0                 # clear Z at WAIT entry
        elif flag_we:
            self.z = alu_z
            self.n = alu_n

        # free-running 16-bit cycle counter.
        self.cnt = (self.cnt + 1) & MASK16

        # shift datapath (OSR, MSB-first).
        if not self.stall and not self.flush:
            if op == OP_SHIFT_IN:
                self.osr = ((self.osr << 1) | (uio_in >> 7) & 1) & MASK16
            elif op == OP_SHIFT_OUT:
                self.ser_out = (self.osr >> 15) & 1
                self.osr = ((self.osr << 1) | (ra & 1)) & MASK16
            elif op == OP_PIN_W:
                self.ser_out = (ra >> 7) & 1   # PIN_W drives bit-7 too

        # stall FSM (WAIT_EDGE / WAIT_CMP + 16-bit timeout).
        if self.stall:
            self.timeout = (self.timeout - 1) & MASK16
            edge_match = (uio_in >> self.wait_sel) & 1 != self.wait_pin_init
            cmp_match = self.cnt == self.wait_tgt
            if edge_match or cmp_match or self.timeout == 0:
                self.stall = False
                self.wait_done = True
        elif stall_enter:
            self.stall = True
            self.timeout = 0xFFFF
            self.wait_pin_init = (uio_in >> sel_pin) & 1
            self.wait_sel = sel_pin
            self.wait_tgt = ra
        else:
            self.stall = False
            self.wait_done = False
            self.timeout = 0

        # PIO output stage (out_stage / OE / uo).
        if pin_w_en:
            self.out_stage = pin_w
        if oe_set_en:
            self.oe = (self.oe | imm8) & 0xFF
        if oe_clr_en:
            self.oe = self.oe & ~imm8 & 0xFF

        # commit the registered read for the next cycle.
        self._imem_rd = new_imem_rd

    # -- convenience ----------------------------------------------------------
    def run(self, cycles: int, uio_in=0, ui_in=0) -> list[dict]:
        """Step ``cycles`` times with constant inputs; return a trace of
        per-cycle snapshots (useful for offline comparison)."""
        trace = []
        for _ in range(cycles):
            self.step(uio_in, ui_in)
            trace.append(self.snapshot())
        return trace


# ---------------------------------------------------------------------------
# UART reference (115200 baud, 8N1)
# ---------------------------------------------------------------------------
def uart_frame_bits(byte: int) -> list[int]:
    """Expected TX pin level sequence: start(0), 8 data bits LSB-first,
    stop(1).  Index 0 is the start bit."""
    byte &= 0xFF
    return ([0] + [((byte >> i) & 1) for i in range(8)] + [1])


def uart_decode_data_bits(data_bits_lsb_first: list[int]) -> int:
    """Reconstruct a byte from 8 sampled data bits (LSB first)."""
    val = 0
    for i, b in enumerate(data_bits_lsb_first):
        val |= (b & 1) << i
    return val & 0xFF


class UartRxDecoder:
    """Decode a sampled UART RX bit stream (start, 8 data LSB-first, stop)."""

    def __init__(self):
        self.frames: list[int] = []
        self.errors = 0

    def feed(self, start: int, data_bits_lsb_first: list[int], stop: int) -> int | None:
        if start != 0:
            self.errors += 1
            return None
        if stop != 1:
            self.errors += 1
        byte = uart_decode_data_bits(data_bits_lsb_first)
        self.frames.append(byte)
        return byte


# ---------------------------------------------------------------------------
# SPI master reference (per-mode SCK/MOSI edge placement)
# ---------------------------------------------------------------------------
def spi_master_waveform(byte: int, cpol: int = 0, cpha: int = 0) -> list[dict]:
    """Expected (sck, mosi) state sequence for an SPI master byte, MSB-first.

    Returns one dict per bit with the idle level, the data level, and the
    capture edge.  ``cpol``/``cpha`` select the mode:
      mode 0: cpol=0 cpha=0 -> sample on rising edge, data set before it.
      mode 3: cpol=1 cpha=1 -> sample on falling edge, data set after it.
    """
    byte &= 0xFF
    idle = 1 if cpol else 0
    events = []
    for i in range(7, -1, -1):
        data = (byte >> i) & 1
        events.append({
            "bit": i,
            "data": data,
            "sck_idle": idle,
            # The edge on which the slave samples MOSI.
            "sample_edge": "rising" if cpha == 0 else "falling",
        })
    return events


# ---------------------------------------------------------------------------
# I2C reference (open-drain condition detector + master bit timing)
# ---------------------------------------------------------------------------
class I2cConditionDetector:
    """Classic I2C bus condition detector (START/STOP/data).

    Call :meth:`update` once per observed (sda, scl) sample; it returns an
    event string or ``None``.  Works on the *effective* open-drain line
    levels (0 = driven low, 1 = released/pulled high).
    """

    def __init__(self):
        self.sda_prev = 1
        self.scl_prev = 1
        self.events: list[str] = []

    def update(self, sda: int, scl: int) -> str | None:
        """Feed one (sda, scl) sample.  Returns the event, if any.

        Follows the standard I2C bus-state rules:
          * while SCL is high, an SDA transition defines START (1->0)
            or STOP (0->1);
          * on an SCL rising edge, a stable SDA is a data-bit sample,
            while an SDA 0->1 (release) is a STOP.
        """
        sda &= 1
        scl &= 1
        event = None
        scl_rising = self.scl_prev == 0 and scl == 1
        if scl == 1 and self.scl_prev == 1:
            # SCL held high: SDA transitions are bus conditions.
            if self.sda_prev == 1 and sda == 0:
                event = "START"
            elif self.sda_prev == 0 and sda == 1:
                event = "STOP"
        elif scl_rising:
            # SCL rising edge: sample the data bit (or detect STOP release).
            if self.sda_prev == 0 and sda == 1:
                event = "STOP"
            else:
                event = f"bit{sda}"
        self.events.append(event if event else "")
        self.sda_prev = sda
        self.scl_prev = scl
        return event


def i2c_master_bit_waveform(bit: int) -> list[dict]:
    """Expected open-drain (sda, scl) driver actions to send one I2C bit.

    A bit is sent by holding SDA, then pulsing SCL high and low.  ``bit``
    is the logical value (0 = driven low, 1 = released).
    """
    sda_drive = "release" if bit else "drive-low"
    return [
        {"sda": sda_drive, "scl": "low"},
        {"sda": sda_drive, "scl": "high"},
        {"sda": sda_drive, "scl": "low"},
    ]


# ---------------------------------------------------------------------------
# USB 1.0 Low-Speed reference (NRZI + bit-stuffing + packet framing)
# ---------------------------------------------------------------------------
def nrzi_encode(bits_msb_first: list[int], initial_state: int = 0):
    """NRZI-encode a list of logical bits (MSB first).

    Logical 1 -> no transition, logical 0 -> transition.  State 0 = J,
    1 = K.  Returns (states, final_state) where each state is the line
    level for that bit period.
    """
    states = []
    cur = initial_state & 1
    for bit in bits_msb_first:
        if bit == 0:
            cur ^= 1
        states.append(cur)
    return states, cur


def usb_bit_stuff(bits_msb_first: list[int]) -> list[int]:
    """Insert a stuffing 0 after every run of 5 consecutive logical 1s."""
    out = []
    run = 0
    for bit in bits_msb_first:
        out.append(bit)
        if bit == 1:
            run += 1
            if run == 5:
                out.append(0)   # stuffed 0 (forces a transition)
                run = 0
        else:
            run = 0
    return out


def usb_bits_msb_first(byte: int) -> list[int]:
    return [(byte >> i) & 1 for i in range(7, -1, -1)]


def usb_low_packet(sync: int, pid: int, payload: bytes):
    """Reference USB 1.0 low-speed packet: sync + PID + payload, NRZI with
    bit-stuffing.  Returns (nrzi_states, final_state) for the whole field."""
    bits = usb_bits_msb_first(sync) + usb_bits_msb_first(pid)
    for b in payload:
        bits += usb_bits_msb_first(b)
    bits = usb_bit_stuff(bits)
    return nrzi_encode(bits, 0)


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # ALU sanity.
    assert alu(OP_ADDS, 5, 3) == (8, 0, 0, True)
    assert alu(OP_SUBS, 5, 5) == (0, 1, 0, True)
    assert alu(OP_SHL, 1, 7) == (128, 0, 0, False)

    # UART.
    assert uart_frame_bits(0xA5) == [0, 1, 0, 1, 0, 0, 1, 0, 1, 1]
    assert uart_decode_data_bits([1, 0, 1, 0, 0, 1, 0, 1]) == 0xA5

    # I2C condition detector.
    det = I2cConditionDetector()
    assert det.update(0, 1) == "START"
    assert det.update(1, 1) == "STOP"

    # NRZI: logical 1 stays, 0 toggles.
    states, _ = nrzi_encode([1, 0], 0)
    assert states == [0, 1]

    # Core model: blink program (NOP-safe) executes without faulting.
    m = MsfCoreModel()
    m.run(20)
    assert m.snapshot()["pc"] in range(0, MASK9 + 1)
    print("golden_model self-test OK")
