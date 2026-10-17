# msfcore v2 — Implementation Plan

> **Status 2026-10-17: phases 0-10 implemented.** Deviations from the plan
> that came out of verification:
> * TX `release` (cfg bit 14) became "engine owns its pins only while a symbol
>   is on the line" instead of "auto-disable at end": the 10BASE-T test showed
>   an enabled-but-idle differential pair still drives TD- before the
>   preamble.
> * The parallel boot loader uses a two-phase *level* handshake instead of
>   strobe edges: the boot test found a race (an edge arriving before the
>   loader re-enters its wait was lost).
> * `CLOCK_PERIOD` relaxed from 20 to 40 ns: the slow-corner SRAM clk->q
>   (6.4 ns) plus ~70 gate levels would not close at 50 MHz, and no use case
>   needs more than 20 MHz.
> * Formal runs on Yosys' built-in SAT engine (`formal/run_formal.py`):
>   SymbiYosys needs external solvers that are not available as Python
>   wheels on Windows.

> Written 2026-10-16 after the full repo review (see "Review findings" below).
> Scope: **every fix and every improvement** from that review. Each phase ends with
> a measurable gate; nothing moves on until the gate is green. The ISA contract for
> all of this is `docs/sam/isa.md` (v2.0), which is written first and is the single
> source of truth for the RTL, the assembler and the reference model.

## 0. Review findings this plan addresses

| ID | Finding | Kind |
|---|---|---|
| B1 | Instruction after any `WAIT_*` executes twice; first copy sees `pc_p` one low (branch lands at target-1) | RTL bug (reproduced) |
| B2 | `WAIT_EDGE` also released by `cnt == r[rd]`; `WAIT_CMP` also released by edges on `uio_in[imm8[2:0]]` | RTL bug (reproduced) |
| B3 | ROM/SRAM fetch mux uses the *current* address but data is registered → word 127 fetched from SRAM | RTL bug (reproduced) |
| B4 | Bootloader corrupts back-to-back UART bytes (stop-bit delay overshoots) | FW bug (reproduced) |
| B5 | Bootloader ignores WAIT timeout → garbage if host starts > 5.5 ms after reset | FW bug (reproduced) |
| B6 | No input synchronisers (`uio_in`/`ui_in` feed regfile, edge detect and PC logic directly) | Silicon risk |
| B7 | Bootloader terminator `0x0000` == `LDR R0,#0`; CRC-8 promised but absent; no ACK | FW/protocol |
| B8 | Golden model mirrors the RTL (incl. B1); CALL masks to 8 bits; WAIT_CMP compare off by one | Verification |
| B9 | GL SRAM model forces `DOUT=0` when idle; real IHP macro holds | Model bug |
| G1 | Shift register (OSR) can't be loaded or read; SHIFT_IN data is lost | ISA gap |
| G2 | `uo_out` tied 0 (8 pins wasted) | ISA gap |
| G3 | No per-bit pin set/clear/toggle; no upper-byte load; CALL doesn't jump; no indirect jump | ISA gap |
| G4 | SRAM words 0–127 shadowed by the ROM and unused; no data memory | ISA gap |
| G5 | USB/Ethernet demos are unrolled waveform playback, no CRC, not real frames | Feature gap |
| G6 | ~85 % of the logic area unused | Opportunity |
| V1 | "formal" tests are directed sims; no SBY; lockstep covers 60 straight-line cycles | Verification |
| V2 | I2C test asserts only START; SPI never checks MISO | Verification |
| H1 | Stale docs/headers/numbers, dead files, 70 MB stale GDS/renders in git, info.yaml gaps, run_tests `--all` misses modules, venv DLL issue | Housekeeping |

## 1. Target architecture (v2) — summary

Full detail in `isa.md` v2.0. Backward compatible with v1 encodings **except CALL**
(v1 `CALL` saved PC+2 and fell through; v2 `CALL imm11` jumps — every v1 user was
`CALL ; JMP sub`, which becomes `CALL sub`).

```
            ui_in ──2FF──┐            uio_in ──2FF──┐
                         ▼                          ▼
   ┌──────────── tt_um_msf_ctrl (2 hardware threads, barrel-interleaved) ───────────┐
   │ fetch (registered IMEM, registered bank select) → decode → regfile T0/T1 → ALU │
   │ unified blocking-op stall FSM per thread (WAIT_EDGE/CMP, LD, TX/RX/CRC ops)    │
   │ shift register SR (load/read, XFER, configurable pins, MSB/LSB)                │
   └──┬───────────────┬─────────────────────┬──────────────────────┬────────────────┘
      │ port A fetch  │ port B LD/ST/MEM_W  │ peripheral bus       │ pin writes
      ▼               ▼                     ▼                      ▼
   ROM 0-127 + SRAM 512x16 (2P macro)   serdes (TX+RX)  crc32    pio (out/oe/uo + TX override)
```

* **Fixes:** B1–B3 in ctrl/top, B6 synchronisers in top, B9 in the GL model.
* **ISA v2** (opcodes `0x0F` = EXT group with 5-bit sub-op, `0x1F` = `LDH`,
  `0x12`/`0x13` gain a selector in their unused `b10..8` field, `WAIT_EDGE` gains
  bank/condition bits in its unused imm bits): pin SET/CLR/TGL/write-immediate,
  `uo_out` writes, `LDH`, `SWAP`, `JR`, `CALL imm11`, SR load/read/XFER/config,
  `MEM_A` + 9-bit wrap, `LD`/`ST` data RAM on SRAM port B, programmable WAIT
  timeout, status register.
* **Peripherals:** generic SERDES (TX: NRZ/NRZI/Manchester, bit-stuffing,
  differential pair, programmable bit period, double-buffered; RX: NRZ/NRZI,
  de-stuffing, edge resync, continuous or async-character start) and a
  configurable bit-serial CRC unit (1–32 bits, normal or reflected).
* **Concurrency:** 2 deterministic hardware threads (XMOS-style barrel): in dual
  mode T0/T1 alternate cycles, each with its own PC, registers, flags and stall
  state; branches cost nothing extra; timing of each thread is independent of
  the other. Single-thread mode = v1 timing exactly.
* **Boot ROM v2:** framed protocol (`A5 cmd addrL addrH lenL lenH words… crc8`),
  CRC-8 via the CRC unit, ACK/NAK on UART TX, status on `uo_out`, UART path
  (RX engine) **and** a parallel path over `ui_in` + strobe for the demo board's
  RP2040, load to any address (code or data RAM), `G` command to run.

## 2. Phases

Every phase: RTL change → assembler/model update → new tests → **full regression
(`python test/run_tests.py --all`) green** → synthesis sanity (yowasp) where RTL
changed. Gates are listed per phase.

### Phase 0 — Infrastructure (no RTL change)
1. `run_tests.py`: read the source list from `info.yaml` (single source of truth),
   `--all` runs every module, add the base interpreter dir to `PATH` so cocotb
   loads `python3xx.dll` from a conda-based venv, accept `-m` lists.
2. Port the 12 review probes into `test/test_regressions.py` (B1–B5 + guards).
   They must FAIL on the current RTL (proves they detect the bugs).
* **Gate:** existing 36 pass; new regressions fail exactly on B1–B5.

### Phase 1 — Bug fixes B1–B5, B8 (model), B9
1. ctrl: advance `pc_p` on stall release, drop `wait_done`, latch WAIT kind.
2. top: registered bank select.
3. bootloader (interim, v1 protocol): `JZ retry` after WAIT_EDGE, drop stop-bit
   delays, `SHL #7/#1`. (Replaced in Phase 6, but keeps the suite honest now.)
4. golden model: same fixes + CALL 9-bit + WAIT_CMP compare on pre-edge `cnt`.
5. GL SRAM model: hold `DOUT` when not enabled; registered port-B read.
* **Gate:** all regressions + 36 existing green.

### Phase 2 — Input synchronisers (B6)
1. 2-FF synchronisers (no reset, so they track inputs during reset) on `ui_in`
   and `uio_in` in the top level; everything downstream uses synced values.
2. Update model/tests for the +2-cycle input latency; document it in isa.md.
* **Gate:** full regression green; latency test (pad edge → PIN_R sees it at +2).

### Phase 3 — ISA v2 core (no new peripherals yet) + ISA reference model
1. ctrl rewrite with the v2 decode, structured per-thread from the start
   (T1 tied off until Phase 5): EXT group, LDH, SETM/CLRM selectors, WAIT_EDGE
   bank/condition, CALL imm11, JR, SWAP, SR ops (SR_LD/SR_RD/XFER/SER_CFG, SR
   writes go into the output stage bit so PIN_W/serial share one register),
   OUT_W/UO ops, MEM_A, LD/ST via SRAM port B, TMO, STAT, unified blocking-op FSM.
2. pio: generic write-enable/value ports for out/oe/uo + TX-engine override.
3. sram: port B read/write.
4. assembler v2: all new mnemonics, `.org`/`.word`/`.equ`, char literals,
   `#label` immediates, `LDI` pseudo (LDR+LDH), reserved-bit checks, disassembler.
5. **ISA reference model** (`test/isa_model.py`) written from isa.md, *not*
   from the RTL: instruction-level semantics + the documented cycle-cost rules.
6. **Random differential test** (`test/test_random_diff.py`): constrained-random
   programs (ALU, LDR/LDH, branches, CALL/RET/JR, pins, SR, LD/ST, MEM_A/MEM_W,
   WAIT_CMP, WAIT_EDGE with random input activity), RTL vs model compared at
   every retirement (thread, PC, cycle, regs, flags, pins). Seeds reproducible.
7. Update existing tests that used v1 CALL.
* **Gate:** full regression + ≥200 random programs × 2k cycles match the model.

### Phase 4 — Peripherals: CRC unit + SERDES
1. `tt_um_msf_crc.v`: 32-bit CRC/poly registers, bit-serial engine, normal
   (left-aligned) and reflected modes, 1–16 bits per `CRC_W`, blocking reads.
2. `tt_um_msf_serdes.v`: TX and RX engines as specified in isa.md §6.
3. ctrl: peripheral bus + blocking ops TX_PUSH/TX_WAIT/RX_POP/CRC_W/CRC_RDL.
4. Model: CRC functional + timing; TX/RX validated by protocol decoders instead.
5. Tests: CRC-8/CRC-16-USB/CRC-32-Ethernet/CRC-5-USB vs Python, TX in each mode
   (decoded from pins), RX in each mode (driven from the testbench), TX→RX
   loopback, bit-stuffing edge cases (exactly six 1s, stuffing across words).
* **Gate:** full regression + peripheral tests + random diff incl. CRC ops.

### Phase 5 — Hardware threads
1. ctrl: dual mode (barrel interleave), TSTART/TSTOP/TID, per-thread regfile,
   flags, stall state, LD hold registers; mode transitions per isa.md §7.
2. Model: slot-level thread scheduling per isa.md.
3. Tests: thread start/stop/restart from both threads, per-thread determinism
   (T0 timing identical whatever T1 does), blocking ops in both threads,
   random diff with two threads.
* **Gate:** full regression + thread tests + dual-thread random diff.

### Phase 6 — Boot ROM v2 + host tool
1. `bootloader.s` v2 (framed, CRC-8, ACK/NAK, status on `uo_out`, UART via RX
   engine, parallel via `ui_in` + strobe, `W`rite/`G`o commands); direct-boot
   path keeps v1 timing (ui_in[0]=1 → JMP 128 at the same cycle).
2. `tools/msf_boot.py`: frame builder + serial loader (pyserial optional), used
   by the tests to build frames.
3. Tests: UART load + run (back-to-back bytes, late host, CRC error → NAK and
   retry, data-RAM load), parallel load + run, 'G' to arbitrary address.
* **Gate:** all boot tests green; ROM ≤ 128 words.

### Phase 7 — Firmware library + real protocol demos
`firmware/*.s`, each with a cocotb test that decodes the pins independently:
1. UART TX/RX with drift-free `WAIT_CMP` scheduling (and via SERDES).
2. SPI master full duplex (XFER, MOSI+MISO checked against a slave model, modes 0–3).
3. I2C master with a cocotb slave model: address ACK/NACK, write, read, STOP;
   constrained-random addresses/data.
4. USB low-speed TX: real packet (SYNC, PID, payload, CRC16, EOP) via SERDES
   NRZI + stuffing + differential; RX: NRZI/de-stuff receive + CRC16 residual check.
5. 10BASE-T TX @ 20 MHz: preamble/SFD, 64-byte frame from data RAM, CRC-32 FCS
   via the CRC unit, TP_IDL, NLP link pulses.
6. Dual-thread demos: deterministic blink on T0 while T1 runs a UART echo.
* **Gate:** all demos decode correctly (Ethernet FCS == zlib.crc32, USB CRC16 ok).

### Phase 8 — Formal verification (real)
1. `ifdef FORMAL` properties in ctrl with ghost state: fetch-address invariant
   (catches B1), bank-select consistency (B3), WAIT release causes (B2), flag
   write discipline, pin write discipline, per-thread isolation, bounded
   liveness of every stall.
2. `formal/` SBY scripts runnable with yowasp-sby locally and in CI
   (new `.github/workflows/formal.yaml`). Rename `test_crv_formal.py` →
   `test_crv_invariants.py` (they are directed sims).
3. Prove the properties FAIL on the v1 RTL (mutation check) and PASS on v2.
* **Gate:** BMC/induction green; mutation check documented.

### Phase 9 — Area / timing sanity
1. yowasp synthesis against the IHP `sg13g2_stdcell` liberty: cell count, area,
   ABC delay estimate at slow corner; SRAM macro timing from the macro .lib.
2. Budget check vs 24 tiles (~0.7 mm² nominal); trim if needed.
* **Gate:** mapped area well under budget; estimated delay < 20 ns.

### Phase 10 — Docs & housekeeping
1. `isa.md` v2 final, `docs/info.md` datasheet (pinout, boot protocol, how to
   test with the demo board), `info.yaml` (pinout, language, clock, sources),
   `README.md`, `golden-model.md` → reference-model doc, `TEST_PLAN`,
   `PROGRESS`, `ethernet-impl.md`, `drc-analysis.md` numbers corrected.
2. RTL headers rewritten; `pdn_cfg.tcl` comment; remove dead files
   (`src/tt_um_msf_sram-dd.v`, `corrections/`, `test/test.py`); stop tracking
   generated GDS/renders (`.gitignore`); Makefile `PROJECT_SOURCES` in sync.
* **Gate:** docs consistent with RTL; `run_tests.py --all` green from clean.

## 3. Out of scope (documented, not done)
* 10BASE-T **RX** (needs ≥4× oversampling of a 10 MHz Manchester stream = 80 MHz).
* Full USB device stack (enumeration) — 384 words of IMEM; TX/RX + CRC primitives
  are demonstrated, a stack is firmware work for later.
* Running LibreLane/OpenROAD locally (no PDK install here) — the GDS action in CI
  is the hardening gate; Phase 9 gives a pre-check.

## 4. Risks
| Risk | Mitigation |
|---|---|
| ctrl rewrite regresses v1 behaviour | Phase 3 keeps every v1 test + random diff vs an independent model |
| Thread mode subtle pipeline bugs | slot-level model, formal per-thread properties, random dual-thread diff |
| Timing at 20 MHz with more logic | Phase 9 estimate; constraint stays 20 ns (50 MHz) in config.json |
| Boot ROM is the only way in | two independent load paths (RX engine UART + CPU-only parallel) |
| Area | 15 % used today; each phase re-synthesises |
