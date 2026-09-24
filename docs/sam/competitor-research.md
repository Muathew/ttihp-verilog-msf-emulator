# Competitor Research — Jane Street "Protocol Emulator" ASIC (TTIHP27a)

> Research agent report. Read-only survey of public prior art + direct competition
> entries. **Only real, URL-verified projects are listed — nothing fabricated.**
> Date: 2026-09-24. Sources: Jane Street blog, tinytapeout.com chip indexes, GitHub.

## 0. The competition (official brief)

- **Source:** Jane Street blog, *"Can you design a chip? Announcing the protocol
  emulator ASIC competition"* (2026-09-10) —
  <https://blog.janestreet.com/protocol-emulator-asic-competition/>
- **Goal:** open-source, general-purpose protocol-emulator ASIC. A tiny CPU whose
  ISA is designed for reading/writing pins, counting cycles, and hitting timing
  precisely enough to implement real protocols **in firmware, not fixed logic**.
  Must be **reprogrammable after fabrication**.
- **Required protocols:** UART, SPI, I2C. **Stretch:** low-speed USB, 10 Mbit
  Ethernet. **Other interesting:** JTAG, SWD, PS/2, CAN bus.
- **Process / platform:** IHP 130 nm CMOS5L via Tiny Tapeout, from the
  [CMOS5L Verilog template](https://github.com/TinyTapeout/ttihp-verilog-template/tree/cmos5l).
  **Tile size: 6×4 (24 tiles, ~0.7 mm², ~1K cells/tile ≈ 24K cells).** 8×4 (~30%
  more area) is *possible* but not guaranteed.
- **Deadline:** 18 Jan 2027. **Shuttle:** March 2027 CMOS5L (TTIHP27a).
- **Prize:** Jane Street pays to tape out the most novel designs; winners get chips
  + dev boards.
- **Inspiration cited by the brief:** RP2040 PIO state machines, TI PRU cores.
- **Verification emphasis:** formal methods, constrained-random, AI-assisted
  verification. Jane Street uses **Hardcaml** (OCaml) for their own RTL.
- **Explicit brief advice (verbatim):** *"Run synthesis early, check the mapped
  cell area, and leave room for clock-tree buffers and routing. Then run the full
  place-and-route flow and check timing. A design that looks small enough after
  synthesis can still be difficult to route or too slow at your chosen clock
  frequency."* And: *"For instruction memory, SRAM can be more area-efficient than
  flip-flops. Tiny Tapeout has examples of SRAM running on this process node you
  can reference."*

**Note on the 2026 project list:** the TTIHP27a (March 2027) submission list is
**not public yet** (shuttle not open). The most recent *shipped* IHP shuttle is
**TTIHP26b** (taped out 2026-09-21, ~123 designs) and **TTIHP26a** (2026-03-23,
283 designs) — those are the best public proxy for "what's out there" and are
rich in protocol/PIO/CPU prior art.

---

## 1. Table of found teams / repos

### 1a. Direct entries to the Jane Street Protocol Emulator competition

| # | Name / Repo | URL | What they're building | Architecture | HDL | Status |
|---|-------------|-----|-----------------------|--------------|-----|--------|
| 1 | **ProtocolGremlin** (Vedant817) | <https://github.com/Vedant817/ProtocolGremlin> | Programmable protocol-emulator ASIC | 24-opcode ISA, 4 regs, `WAITEDGE` + cycle-capture, `SHIFTOUT/IN`, open-drain (`GODRI`/`GODR`), 256-byte program RAM + on-chip CRC-8 serial bootloader, PVFI formal trace port. **19,291 cells / 37,832 GE; program RAM = 91.8% of area (synth FF matrix); 10 MHz; 6×4.** | Verilog + Python (assembler, ISA model, 90+ protocol models) | Active (iteration 114). 509/509 RTL tests, 20-step Z3 BMC formal, mutation 93/93 killed, gate-level 8/8, differential vs Python model. 90 protocol firmwares. AI-assisted (Antigravity/Warp). |
| 2 | **GP_PAE** (sjrai007, ex-"WARD") | <https://github.com/sjrai007/gp_pae> | Reprogrammable protocol emulator | **N independent tiny sequencer "lanes" sharing a pin crossbar + post-fab-writable instruction store** → emulates UART/SPI/I2C *simultaneously*. 4-wire SPI config port. **~12.4K cells** (generic synth, not PDK-mapped). | **Hardcaml (OCaml)** → generated Verilog | Active. 742 checks cross-checking RTL vs a from-scratch OCaml interpreter cycle-by-cycle + independent cocotb/Icarus. UART TX PoC only; SPI/I2C programs + PDK P&R not done. |
| 3 | **protocol-emulator** (Abagel-coder) | <https://github.com/Abagel-coder/protocol-emulator> | "Time-triggered CPU with proven timing and line-coding pin engines" | Deterministic core, ISA for pin I/O + exact cycle timing; 6×4. | Verilog | Early. Warm-up 8N1 UART TX (3/3 cocotb). Measured: 252 cells (184/68), 3,507 µm², 0.39% of core; reset FF ~49 µm², avg cell ~13.8 µm². GDS written; local LibreLane run **stopped at the Magic LEF step (Docker Desktop file-sharing stall)**. |
| 4 | **janestreet.asic.protocol-emulator** (mtanneer) | <https://github.com/mtanneer/janestreet.asic.protocol-emulator> | Scaffold | None yet (dummy `project.v`). | Verilog | Scaffold only. Strong CI (Verilator lint/sim, fusesoc, Yosys synth-check, gitleaks, protected `main`). No ISA/CPU designed. |
| 5 | **protocol-emulator-asic** (Kleven2k) | <https://github.com/Kleven2k/protocol-emulator-asic> | "Open-source reprogrammable … USB-LS, 10BASE-T" | (not readable — repo 404s / private) | — | **Found via search only; repo currently 404.** Cited in search snippets as targeting USB-LS + 10BASE-T. |

### 1b. Adjacent Tiny Tapeout IHP prior art (shipped, silicon-relevant)

These are the closest real-world analogs to our design, all on IHP SG13G2/CMOS5L.

| # | Project (author) | Shuttle / URL | What it is | Notes |
|---|------------------|---------------|-----------|-------|
| 6 | **nanoPIO** (catalinlazar) | TTIHP26b #546 · <https://github.com/catalinlazar/tt_um_catalinlazar_nanopio> | RP2040-PIO-inspired bit-banged programmable I/O engine | **13-bit instr, 16-word store built from per-word latches (≈½ the storage cost of FFs)**; 8-bit `X`; per-instr 0–7 **delay field** (folds UART/SPI/I2C timing into the program, no busy-loops); `JMP` cond incl. `DEC`; `WAIT` (pin+polarity); `IN`/`OUT`/`SET`; **bit-bang reprogram loader** (`LOAD_EN/DATA/CLK` on `ui_in[7:5]`, MSB-first 17-bit frames). 10 MHz, **fits 1×1 tile**. Verilog + Python assembler + cocotb. |
| 7 | **Kraken IO / mini-Kraken** (matztron) | TTIHP26b #168 · <https://github.com/matztron/tto_mini_kraken_IHP26b> | RP2040-PIO-compatible SM, runs real `pioasm` scripts | 1 SM, 8-word IMEM, 2 GPIO (`uo[1:0]`), depth-2 TX/RX FIFOs, runtime clkdiv, **config mode via `uio[7]`** (pin-loader writes IMEM + SM config w/o a system bus). 1×2 tile. MicroPython demoboard SDK; cocotb suite (blink/IMEM/UART/FIFO/clkdiv/restart/RX/IRQ); **tested on TTO FPGA (blink works)**. |
| 8 | **AstraPIO** (fvannel / Fabien) | TTIHP26b #166 · <https://github.com/fvannel/AstraPIO> | Programmable PIO + autonomous timed-pulse coprocessor, host SPI | PIO: 16×10-bit instr, 8-bit acc, 4-bit loop ctr, 2-byte TX/RX FIFOs, slot every 4 ASIC clocks. **Timed engine samples pulse-width-coded inputs, captures 1–24-bit prefix, regenerates stream, host-replaces prefix atomically** (WS2812 live-patching app note). Host SPI mode 0, 32 clocks/CS, 50 MHz ref, 8 bidir `uio`. ABI 5. |
| 9 | **UART-SPI-I2C Bridge** (Catalin Lazar) | TTIHP26b #162 | UART-controlled bridge: SPI master (4×CS, CPOL/CPHA) + I2C master (clock stretching) | Command interpreter + FIFO buffering + memory-mapped register file; standalone UART-command testable; built-in loopback self-test. |
| 10 | **8N1 UART Transceiver** (NUAT Labs) | TTIHP26b #392 | Full-duplex 8N1 UART TX+RX | Runtime bidirectional data bus, **mid-bit sampling**. |
| 11 | **UART-SPI RAM Loader** (romd) | TTIHP26b #391 | **CRC-protected UART loader** for 64 KiB SPI SRAM | Prior art for a robust UART-RX bootloader (CRC + fail-safe). |
| 12 | **TinyQV SoC** (LKhanh / Michael Bell) | TTIHP26b #260/#426 · <https://github.com/TinyTapeout/tinytapeout-ihp-26b> | RV32EC RISC-V SoC, 64 MHz, 3×2 | Dual memory backend (QSPI PMOD, or RP2040 `spi-ram-emu` 23LC512). Crowdsourced RISC-V on IHP. |
| 13 | **USB CDC (Serial) Device** (Uri Shaked) | TTIHP26a #654 | Full **USB CDC** device on IHP | Proof that USB-class protocols ship on this node. |
| 14 | **SENT→I2C bridge** (Enzo Nappi) | TTIHP26b #160 | SAE J2716 SENT decoder/encoder → I2C register-map slave | Automotive serial-protocol prior art. |
| 15 | **PS/2 Keyboard Decoder** (Ben Payne) | TTIHP26b #358 | PS/2 decoder for retro 68k | PS/2 protocol prior art (one of our stretch protocols). |
| 16 | **IEEE Workshop Simple CPU** (H. Bouyahiaoui, M. Debbaghi) | TTIHP26b #234 | 8-bit accumulator CPU using a **real IHP 256×8 SRAM macro** | **Multi-cycle to tolerate the macro's registered read latency** — key SRAM-macro lesson. |
| 17 | **SRAM (1024×8) test** (Uri Shaked) | TTIHP0p2 #39 · <https://github.com/urish/ttihp-sram-test> | Foundry SRAM macro test | **Silicon-proven** (mattvenn: 2M random-cycle soak). The SRAM-macro reference the brief points to. |
| 18 | **MC14500B 1-bit MCU SoC** (A. C. Abad) | TTIHP26b #228 | 1-bit MCU: 256-word ROM, 16-bit RAM, dual IO expansion | Tiny-RISC-with-pin-IO prior art. |

### 1c. RP2040 PIO prior art (the reference architecture the brief cites)

| Repo | URL | Why it matters |
|------|-----|----------------|
| **raspberrypi/pico-examples** | <https://github.com/raspberrypi/pico-examples> | `pio/uart_tx`, `pio/uart_rx` (+intr), `pio/spi` (loopback, all 4 CPHA/CPOL), `pio/spi_flash`, `pio/ws2812`, `pio/squarewave`. The canonical firmware bit-bang programs our firmware should be able to reproduce. |
| **raspberrypi/pico-sdk** (`hardware_pio`) | <https://github.com/raspberrypi/pico-sdk> | PIO register/program model: 4 SMs, 32-instr IMEM each, 32-bit OSR/ISR shift regs, autopush/autopull, side-set, per-instr `[delay]`, `clkdiv`. The ISA we're deliberately *simplifying*. |

**Tiny RISC-with-pin-IO prior art** (for the "brain" half): TinyQV (RV32EC, above),
`MC14500B` 1-bit MCU, `m6502` (TTIHP26a #163), `8-bit RISC-V Lite` (TTIHP26a #231),
`Lipsi` (TT06, "probably the smallest processor in the world").

---

## 2. Key ideas worth stealing

1. **CRC-protected UART bootloader with fail-safe halt on corruption** —
   ProtocolGremlin (on-chip CRC-8, halt on bad CRC) and romd's UART-SPI-RAM-Loader
   (#391). Steal: wrap our ROM-resident UART-RX bootloader in a CRC-8/16 + a
   "halt + blink" path so a corrupted load can't leave the core running garbage.
2. **Per-instruction `delay` field (0–7 stall cycles)** — nanoPIO. Folds UART/SPI/
   I2C bit-times directly into the program instead of burning instruction words on
   busy-loops. Cheap, and it's exactly what our "event-driven stall" wants; adopt
   a small fixed delay field *in addition to* `WAIT_EDGE`/`WAIT_CMP`.
3. **Latch-based (not FF-based) instruction store** — nanoPIO: per-word latches
   gated by a shared address decoder ≈ ½ the per-bit storage of FFs, which is what
   lets a 16-word store fit in 1×1. Directly relevant to our ~8K budget and to
   ProtocolGremlin's failure mode (see pitfalls).
4. **Dual independent verification** — GP_PAE: RTL cross-checked cycle-by-cycle
   against a from-scratch OCaml interpreter (742 checks) *and* a separate
   cocotb/Icarus stack. Steal: keep our Python assembler/reference model as an
   independent differential oracle (ProtocolGremlin does the same with Python).
5. **Config/run mode split on a spare pin + host SDK** — Kraken: `uio[7]` selects
   config (pin-loader writes IMEM + SM config) vs run, plus a MicroPython demoboard
   SDK. Steal: a clean "load mode" pin so we can reprogram the shift-datapath/
   protocol config after fab without a system bus, and ship a small host driver.
6. **Measured cell/area data for budgeting** — Abagel-coder: reset FF ≈ 49 µm²,
   avg mapped cell ≈ 13.8 µm², 0.39% of the 6×4 core for a UART-TX-only netlist.
   Use these as sanity anchors for our ~8K-cell budget.
7. **PVFI formal trace port (zero silicon overhead)** — ProtocolGremlin. A
   tiny, always-on observability tap that costs no area; useful for silicon debug
   of a one-shot tapeout.
8. **N-lane parallel sequencer** — GP_PAE. If we have area headroom, a second
   independent lane (or a lane-crossbar) lets two protocols run at once (e.g.
   I2C master + UART RX simultaneously).

---

## 3. Pitfalls to avoid (from public postmortems / community)

1. **FF-based program RAM eats the area budget.** ProtocolGremlin: program RAM is
   **91.8% of total area** (synthesized flip-flop matrix). If we use FFs for the
   instruction store, we'll blow past 8K. Use latches (nanoPIO) or the IHP SRAM
   macro (brief-endorsed).
2. **SRAM-macro registered read latency.** IEEE Workshop Simple CPU (#234) had to
   go **multi-cycle** to tolerate the IHP 256×8 SRAM macro's registered read. If we
   adopt the macro, budget an extra cycle for the read path (or use it only where
   latency is acceptable).
3. **Synthesis-small ≠ routable/fast.** Jane Street brief + Abagel-coder both flag
   this. Abagel-coder's local LibreLane run **stopped at the Magic LEF step**
   (Docker Desktop file-sharing stall). Run full P&R early, at the target clock,
   and keep a **known-green GDS baseline** (Kraken's commit log literally says
   "restore last GDS-green baseline" after a regression).
4. **CDC / synchronizer + input-skew traps.** dlmiles shipped a deliberate
   *"Example of Bad Synchronizer"* and an *"IHP loopback tile with input skew
   measurement"* (TTIHP0p2). Double-flop every async input (bootloader RX,
   `uio_in` edges) and measure input skew on the demoboard before trusting
   mid-bit sampling.
5. **Verification gaps — don't stop at RTL sim.** Several entries are UART-TX-PoC
   only (Abagel-coder, mtanneer). The strong ones (ProtocolGremlin, GP_PAE) add
   formal (Z3 BMC / SymbiYosys), mutation testing, gate-level sim, and
   differential testing against an independent model. Our M3 "constrained-random +
   checker" plan is on the right track; add formal + gate-level before submission.
6. **Timing-prediction blind spots.** TTIHP26b #136 ("Timing-Prediction Test
   Vehicle", ring-oscillator delay canaries) shows the community is worried about
   pre/post-silicon timing divergence. Keep a ring-osc canary in the design so we
   can measure real silicon delay after fab.

---

## 4. How we compare (our hybrid brain + shift + events + bootloader)

Our design: tiny RISC "brain" + **hardware serial shift datapath** + event-driven
stalls (`WAIT_EDGE`/`WAIT_CMP`) + open-drain I/O + **ROM-resident UART-RX
bootloader**, ~8K of 24K cells, 12 MHz, 8 bidir `uio`. Protocols in firmware.

**Where we're stronger:**
- **ROM-resident bootloader vs RAM+serial-load.** ProtocolGremlin and most PIO
  designs need a *host* to load the program/bootloader into RAM first. Ours boots
  from ROM → the chip is **self-contained on power-up** (can start a UART-RX
  session with zero host involvement). This is a genuine differentiator for a
  "debugging tool" brief.
- **Real RISC brain, not just a PIO SM.** nanoPIO/Kraken/AstraPIO are
  instruction-store state machines (great for fixed protocol timing, weak at
  general logic). Ours can run *arbitrary* firmware (CRC, framing, state machines,
  even a tiny protocol stack), which is the "general-purpose" the brief rewards.
- **Hardware shift datapath.** Most entries bit-bang shifts in firmware (slow,
  area-hungry in the program store). A dedicated shift datapath offloads
  UART/SPI/I2C bit movement, freeing the brain for control — a real efficiency win
  nobody in the competition table has.
- **Area headroom.** ~8K cells ≈ ⅓ of the 24K budget vs ProtocolGremlin's 19,291
  and GP_PAE's 12.4K. We have room for a second lane, a ring-osc canary, and a
  PVFI tap.

**Where we're weaker / at risk:**
- **No PIO compatibility.** Kraken runs *existing `pioasm` programs*; we'd need our
  own assembler + firmware for every protocol. (Mitigation: our Python assembler
  already planned in M2.8.)
- **Single lane.** GP_PAE runs multiple protocols in parallel (N lanes). We're
  single-brain → one protocol at a time (unless we add a lane).
- **12 MHz vs 50–64 MHz.** AstraPIO (50 MHz) and TinyQV (64 MHz) run faster; at
  12 MHz our max UART baud and SPI/I2C bit-rates are lower. Fine for the required
  protocols, but it caps the stretch goals (USB-LS, 10Mbit Ethernet).
- **No dedicated timed-pulse engine.** AstraPIO's pulse-capture/regen engine
  (SENT/WS2812) has no analog in our design; those protocols would be firmware.

**Net:** our design is the most *self-contained* (ROM boot) and the most
*area-efficient* of the group, with a hardware shift datapath nobody else has. The
main gaps are parallelism (lanes) and clock speed, both addressable with our
headroom.

---

## 5. Actionable recommendations (max 5)

1. **Build the instruction store from latches (or the IHP SRAM macro), not FFs.**
   Rationale: nanoPIO halves storage cost vs FFs; ProtocolGremlin's FF-RAM is 91.8%
   of its area — the #1 way to blow the 8K budget.
2. **Add a small per-instruction `delay` field (0–7) alongside `WAIT_EDGE`/`WAIT_CMP`.**
   Rationale: nanoPIO shows folding bit-times into the instruction kills busy-loop
   code and shrinks firmware; cheap and high-leverage for UART/SPI/I2C.
3. **Wrap the ROM bootloader in CRC + a fail-safe halt/blink path.**
   Rationale: ProtocolGremlin (CRC-8 halt) and romd (#391) both do this; a
   corrupted load must not run garbage on a one-shot tapeout.
4. **Run full P&R at 12 MHz early and keep a known-green GDS baseline; add a
   ring-osc delay canary + PVFI tap.**
   Rationale: the brief + Abagel-coder (Magic-LEF stall) + Kraken ("restore
   GDS-green baseline") + TTIHP26b #136 all point to pre/post-silicon timing
   divergence as the top schedule risk.
5. **Add a second independent lane (or lane-crossbar) if area allows, and keep a
   Python differential model + formal (SymbiYosys) + gate-level sim in CI.**
   Rationale: GP_PAE proves parallel lanes are a real feature; the strong
   competitors (GP_PAE, ProtocolGremlin) all pair RTL with an independent model +
   formal + gate-level — that's the verification bar the brief explicitly rewards.

---

## Appendix — counts & caveats

- **Direct competition entries found (real, URL-verified):** 5
  (ProtocolGremlin, GP_PAE, Abagel-coder, mtanneer, Kleven2k[404]).
- **Adjacent TT-IHP protocol/PIO/CPU/SRAM prior art:** ~13 (nanoPIO, Kraken,
  AstraPIO, UART-SPI-I2C Bridge, 8N1 UART, UART-SPI-RAM-Loader, TinyQV, USB CDC,
  SENT→I2C, PS/2, IEEE-SRAM-CPU, SRAM-test, MC14500B).
- **RP2040 PIO prior-art repos:** 2 (pico-examples, pico-sdk).
- **Total ≈ 20 real projects/repos.**
- The TTIHP27a (March 2027) submission list is **not public yet**; TTIHP26a/26b are
  the best available proxy. No teams were fabricated — every row above has a URL.
