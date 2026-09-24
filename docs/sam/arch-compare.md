# Architecture Comparison — Protocol Emulator Core (private)

> Decision doc for Phase 1. Not part of the open-source submission.
> Clock target is fixed at **~12 MHz** (2026-09-23). Two calls remain: **model** and **footprint**.

## The three models

### A. PRU-style RISC (recommended)
- 8–16 general registers, 16-bit instructions, single-cycle fetch/decode/execute.
- ALU: add/sub/and/or/xor/shift/compare; branches (cond + unconditional); immediate ops.
- Pin I/O: `PIN_READ` / `PIN_WRITE` / `PIN_OE` instructions hitting a cycle-accurate output register.
- Cycle counters: a few free-running counters + compare-and-branch for protocol timing.
- **Pros:** firmware is ordinary assembly (easy to write UART/SPI/I2C), easy to verify with a Python assembler + cocotb, well-understood datapath, ~3–6K cells.
- **Cons:** protocol timing is "soft" (loop delays) — needs cycle-accurate counters to be bit-exact.

### B. PIO-style state machines
- 4–8 shift-register state machines, each with programmable input/output state machines, per-bit cycle control.
- **Pros:** extremely area-efficient, inherently cycle-accurate, RP2040-proven for UART/SPI/I2C.
- **Cons:** awkward to program general logic; each protocol needs a dedicated SM config; harder to verify; less "CPU-like" (competition wants a *programmable processor*).

### C. Hybrid (RISC + shift engines)
- Small RISC core + 1–2 PIO-style shift engines for protocol timing.
- **Pros:** most capable.
- **Cons:** most complex, most area, two verification stories. Risky against 24K budget with P&R headroom.

## Footprint options (for model A)

| Option | Regs | Instr | Instr SRAM | Est. core area | Notes |
|---|---|---|---|---|---|
| **F1 (recommended)** | 8 | 16-bit | 256 words | ~3–4K cells | Compact, PRU-like, big headroom |
| F2 | 16 | 16-bit | 512 words | ~5–7K cells | More general firmware |
| F3 | 16 | 32-bit | 512 words | ~8–10K cells | Most general, riskier vs 24K |

All options leave room for UART/SPI/I2C firmware + verification. F1 is the safe start; can grow to F2 later without changing the ISA shape.

## Recommendation
- **Model A (PRU-style RISC)** + **F1 footprint** as the first pass.
- Rationale: the competition explicitly wants a *programmable processor* whose ISA is designed for pin R/W + cycle-accurate timing. A small RISC core with cycle counters hits that brief directly, is the easiest to verify (scored), and leaves area headroom for the P&R flow.
- Keep the hybrid (C) in mind as a stretch if area budget allows after synthesis.

## Next step once decided
1. Write the ISA spec (instruction encodings, register map, pin I/O model, cycle counters) → `docs/sam/isa.md` (private).
2. Block diagram + per-block area budget.
3. Phase 2: core RTL + Python assembler + "blink a pin" smoke test.
