# Deep Step-by-Step Plan — Protocol Emulator ASIC (private)

> Working plan. Not part of the open-source submission.
> Baseline assumptions (from `docs/sam/arch-compare.md`): **PRU-style RISC core, F1 footprint**
> (8 regs, 16-bit instr, 256-word SRAM), **~12 MHz clock**, IHP 130nm, 6x4 tiles (~24K cells).
> If the model/footprint decision changes, steps in M1–M2 adapt; M3–M4 are unaffected.

## Conventions
- Every step ends with a **gate** (a measurable check). No step is "done" until its gate passes.
- After any RTL change: the cocotb suite must pass.
  - Linux/macOS: `cd test; make -B`
  - Windows (this machine): `cd test; ..\.venv\Scripts\python.exe run_tests.py`
    (Git-for-Windows `sh` fork limits + old WinAVR make break `make`;
    `run_tests.py` uses the cocotb_tools Python runner + iverilog directly).
  - Single test: `python run_tests.py -t <name>` (e.g. `-t wait_cmp`).
- Private working docs live in `docs/sam/`; shared/collaborator docs live in `docs/shared/`.
- Public submission content: `src/*.v`, `test/*`, `docs/info.md`, `info.yaml`, `README.md`.

## M0 — Foundations (done)
- [x] Toolchain verified: iverilog (C:\iverilog), cocotb 2.0.1 venv, `run_tests.py` runner
- [x] Template test replaced by `test/test_core.py` (9-test suite)

## M2 status snapshot (25 Sep 2026)
- M2.7 gate met: **9/9 cocotb tests green** (`test_core.py`): blink smoke,
  ALU/flags + flag preservation, backward branch loop, CALL/RET, cycle
  counter, shift datapath, pin I/O + OE, WAIT_EDGE, WAIT_CMP.
- Key RTL fix in `ctrl.v`: WAIT_* stall entry must **hold pc_p** (frozen) so
  the instruction after the WAIT re-executes; stall match uses latched
  fields (`wait_sel`/`wait_tgt`), not the live `ir` (which carries the
  *next* instruction during a stall).
- Boot ROM regenerated: 94-word UART-RX bootloader (`bootloader.s` →
  `tt_um_msf_bootrom_data.v` via `assembler.py`).
- **Known test gap:** all 9 tests preload firmware by writing `u_sram.mem`
  directly (`load_sram()` in `test_core.py`), bypassing the `MEM_W` boot
  path. No test exercises the ROM bootloader *loading* firmware into SRAM
  → tracked as M3.0 below.
- `MEM_W` auto-increments SRAM addr 128→511 and wraps; firmware image size
  is implicit (author must not exceed 384 words).

---

## M1 — ISA & architecture (design on paper first)

### 1.1 Write the ISA spec → `docs/sam/isa.md` (private)
- Instruction set:
  - Data: `ADD/SUB/AND/OR/XOR/SHL/SHR` (reg-reg and reg-imm)
  - Control: `JMP`, `JEQ/JNE/JLT/JGE` (cond on flags), `CALL`/`RET` (1-level subr, or just JMP)
  - Pin I/O: `PIN_R` (read `uio_in` → reg), `PIN_W` (reg → `uio_out`), `PIN_OE` (set/clear `uio_oe` bits)
  - Timing: `CNT_R` (read cycle counter → reg), `CMP_BR` (branch if counter == imm), `NOP`
  - Load: `LDR` (imm → reg)
- 16-bit encoding table (opcode fields, register fields, immediate fields).
- Register map: R0–R7, flags register, PC, cycle counter (free-running, 16-bit).
- Pin I/O model: exactly how `uio_oe`/`uio_out`/`uio_in` map to emulator GPIO; define the
  "cycle-accurate output register" (outputs update on posedge, one cycle after `PIN_W`).
- **Gate:** spec is self-consistent — every instruction has an encoding, no field overlaps,
  and a worked example (blink pseudocode) encodes cleanly.

### 1.2 Block diagram + area budget → `docs/sam/isa.md` (append)
- Blocks: fetch unit, decode, ALU, register file, pin I/O, cycle counter, instruction SRAM.
- Per-block cell estimate (use Tiny Tapeout SRAM examples for the 256x16 SRAM).
- **Gate:** total estimate < 18K cells (leaves ~6K headroom for clock tree + routing).

### 1.3 Firmware plan (paper)
- Pseudocode for: blink, UART TX, UART RX, SPI master, I2C master — with cycle budgets
  (e.g. UART 115200 @ 12MHz = 104 cycles/bit; SPI/I2C bit-times).
- **Gate:** each protocol's bit-time is an integer number of cycles at 12 MHz (or document the
  fractional handling / clock-divisor scheme).

---

## M2 — Core RTL (one module per file, in `src/`)

### 2.1 `src/tt_um_msf_core.v` — top wrapper
- Tiny Tapeout pin interface (`ui_in`, `uo_out`, `uio_*`, `ena`, `clk`, `rst_n`).
- Instantiates sub-blocks; assigns all outputs (tie unused to 0); `_unused` reduction.
- **Gate:** compiles clean with iverilog (no warnings), `make -B` passes with a stub.

### 2.2 `src/tt_um_msf_regfile.v` — register file
- 8 x 16-bit regs, 2 read ports, 1 write port, async reset.
- **Gate:** unit cocotb test: write/read all regs, verify port independence.

### 2.3 `src/tt_um_msf_alu.v` — ALU + flags
- Ops from 1.1; sets Z/N flags.
- **Gate:** unit cocotb test: exhaustive-ish random op vectors vs Python reference model.

### 2.4 `src/tt_um_msf_ctrl.v` — fetch/decode/execute control
- PC, instruction fetch from SRAM, decode, single-cycle execute, branch logic.
- **Gate:** unit cocotb test: run a hand-assembled 10-instruction program, check PC trace + regs.

### 2.5 `src/tt_um_msf_sram.v` — 256x16 instruction SRAM
- Follow Tiny Tapeout SRAM style (FF-based if SRAM macro unavailable in flow).
- **Gate:** unit cocotb test: write/read all addresses, verify no aliasing.

### 2.6 `src/tt_um_msf_pio.v` — pin I/O block
- Cycle-accurate output register: `PIN_W` latches to `uio_out` on next posedge; `uio_oe` control.
- **Gate:** unit cocotb test: verify 1-cycle latency and OE behavior.

### 2.7 Integrate + smoke test
- Wire blocks into top; write `test/test_core.py`:
  - Load a "blink" firmware (via Python assembler) into SRAM, run, observe `uio_out` pattern.
- **Gate:** blink test passes; waveform in `gtkwave tb.fst tb.gtkw` shows expected pattern.

### 2.8 Python assembler → `test/assembler.py`
- Assembles the ISA (from 1.1) to 16-bit words; loads into SRAM in tests.
- **Gate:** assembler round-trips the ISA spec examples; used by all M3 tests.

---

## M3 — Protocol firmware + verification (the scored part)

### 3.0 Boot-path verification (closes the M2 test gap)
- End-to-end test: reset → ROM bootloader runs → drive a UART stream on
  `uio_in` → bootloader writes image words via `MEM_W` (auto-increment
  128→511) → core hands off to PC 128 → firmware runs and is observed on
  `uio_out`.
- Replaces `load_sram()` (direct `u_sram.mem` writes) for at least one
  protocol test, proving the "reprogrammable after fab" claim in sim.
- **Gate:** a firmware image loaded *only* through the UART-RX boot path
  executes correctly; a corrupt checksum (if added) is rejected.

### 3.1 UART TX firmware + checker
- Firmware: bit-bang 115200 (104 cycles/bit) using `PIN_W` + cycle counters.
- cocotb checker: reconstruct bytes from `uio_out` timing, bit-accurate framing check.
- **Gate:** TX test passes with known payload; checker catches a 1-bit corruption (negative test).

### 3.2 UART RX firmware + checker
- Firmware: sample `uio_in` mid-bit using cycle counter; assemble byte.
- cocotb: drive a UART TX stream on `uio_in`, verify received bytes.
- **Gate:** RX test passes; test offset sampling by ±1 cycle to confirm robustness.

### 3.3 SPI master firmware + checker
- Firmware: clock `uio_out` (SCK), shift data in/out (MOSI/MISO on `uio_in`).
- cocotb: model a SPI slave, verify frame + timing.
- **Gate:** SPI master test passes for CPOL/CPHA modes used.

### 3.4 I2C master firmware + checker
- Firmware: open-drain bit-bang (use `uio_oe` to release lines), START/STOP/ACK.
- cocotb: model an I2C slave, verify address + data + ACK.
- **Gate:** I2C master test passes; verify ACK/NACK handling.

### 3.5 Constrained-random suite
- Randomize: payload bytes, protocol params (baud within range, SPI mode, I2C addr).
- Deterministic seed for regression; random seed for stress.
- **Gate:** 1000-seed stress run passes; failures (if any) are reproducible by seed.

### 3.6 (Stretch) USB 1.0 low-speed / 10Mbit Ethernet
- Only if M4 area/timing has headroom. Document as stretch in `docs/info.md`.
- **Gate:** (stretch) at least one stretch protocol demo passes.

---

## M4 — Hardening (Tiny Tapeout flow)

### 4.1 Synthesis + area check
- Yosys: map to IHP 130nm lib, count cells vs 24K budget.
- **Gate:** mapped area < 24K with ≥10% headroom. If over: trim (smaller SRAM, fewer regs) and re-run.

### 4.2 Full P&R + timing closure
- OpenLane/LibreLane: place & route, check timing at 12 MHz.
- **Gate:** timing closes (setup/hold met) at 12 MHz; no DRC violations.

### 4.3 Gate-level simulation
- `make -B GATES=yes` with the routed netlist.
- **Gate:** all M3 tests pass on the gate-level netlist (not just RTL).

### 4.4 Submission
- Fill `docs/info.md` (how it works / how to test / external hardware).
- `info.yaml`: `tiles: "6x4"`, `source_files` in sync with `test/Makefile` `PROJECT_SOURCES`.
- Open-source check: no private artifacts in the repo.
- **Gate:** clean `git status` (only intended files), submission package builds, GDS generated.
- **Deadline: 18 Jan 2027.**

---

## Decisions
- **Clock target: ~12 MHz** (chosen 2026-09-23). Keeps the USB 1.0 low-speed (1.5 Mbit/s) stretch goal alive; very achievable at 130nm. UART/SPI/I2C are comfortably within budget at this clock.
- **HDL: SystemVerilog** (Muath, 2026-09-24). Develop the design in SV *first* for speed. **Hardcaml (OCaml) is a later, optional conversion** — only if comfortable; not a blocker. (Hardcaml is Jane Street's own HCL, so a conversion is a submission plus, but don't gate on it.)
- **ISA must be locked before control logic** (Muath, 2026-09-24). The instruction set for the control logic is the gating item — do M1.1 (`docs/sam/isa.md`) to a full stop before any M2 RTL.
- **Architecture model: DECIDED — HYBRID** (RISC brain + 16-bit serial shift datapath +
  event-driven stalls `WAIT_EDGE`/`WAIT_CMP rs` + open-drain I/O + ROM UART-RX bootloader).
  (Sam, 24 Sep 2026; all `design-questions.md` §J answered.) See
  `docs/sam/architecture-research.md` (private) for the full report + ~8 K cell budget.
- **Core footprint: DECIDED — F1.5** (8 regs, 16-bit instr, **512-word** IMEM on the
  **Tiny Tapeout SRAM macro** (⚠️ registered read), 16-bit shift register, 16-bit
  free-running counter, 9-bit PC). (Sam, 24 Sep 2026.)
- **Reprogrammability claim (the competition's explicit goal):** chip is reprogrammable for any
  bit-bangable single/dual-wire protocol within 12 MHz / 8 pins, via firmware + a serial bootloader.
  Prove it by adding JTAG or SWD as firmware-only (no RTL change).

## Cross-cutting
- **(Optional) Hardcaml conversion (after M3):** if the team is comfortable, port the SV design
  to Hardcaml (OCaml, Jane Street's HCL) — their own tool, strong verification story, submission plus.
  Setup is light (opam + `opam install hardcaml hardcaml_waveterm ppx_hardcaml core utop dune`),
  best on the Ubuntu PC (native; WSL2 stays off). No impact on the Tiny Tapeout flow:
  Hardcaml emits the same Verilog that M4 consumes. Do NOT let this gate M2–M4.
- **FPGA pre-check (friend's board):** after M2.7, port the core to the friend's FPGA,
  run the blink + UART tests there before committing to ASIC. Catches integration bugs cheaply.
- **Collaborator split (TBD):** e.g. one on M2 RTL, one on M3 firmware/verification.
  Keep AI/planning artifacts private (`.gitignore`).
- **Risk watch:** SRAM macro availability in the flow (fallback: FF-based memory);
  12 MHz timing closure (fallback: 8 MHz, drops USB stretch); area over budget (fallback: F1→smaller).

## Open questions
- Friend has FPGA access — plan an FPGA test flow for RTL before ASIC (blog recommends it).
  → **Decided: FPGA pre-check on the AUP_ZU3 (Zynq UltraScale+ ZU3)** (design-questions Q45).
- Team split with collaborator: TBD (keep AI/planning artifacts private — see .gitignore).
  → **Decided: paired programming, work together** (design-questions Q55).

## Current gate
- ~~Blocking M2: finalize `docs/sam/isa.md`~~ → **DONE (24 Sep 2026).** All
  `design-questions.md` items answered; decisions folded into `docs/sam/isa.md` v1.0
  (imm8, reg-based `WAIT_CMP rs`, 512-word IMEM + SRAM macro, shift datapath,
  `WAIT_EDGE`/`WAIT_CMP` stalls, 128-word ROM bootloader, JTAG firmware demo).
  Remaining `isa.md` §8 items are minor (serial-pin select, CRC width, optional delay field).
- **M2 (core RTL in SystemVerilog) is now unblocked.**
