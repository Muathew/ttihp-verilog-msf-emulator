# Design Questions — Protocol Emulator ASIC (private)

> For discussion with collaborator. `[BLOCKING]` = answer before M2 RTL starts.
> Bracket the agreed answer, e.g. `→ Decided: 8 regs (Sam, 23 Sep 2026)`.
>
> Legend: **S** = Sam, **F** = Friend, **A** = AI suggestion (adopt/reject).

## A. Architecture model

1. **[BLOCKING] Core model: PRU-style RISC (A: yes), PIO-style state machines, or hybrid RISC+shift engines?**  
   → Research recommends **hybrid** (RISC brain + 16-bit serial shift datapath + event stalls) — see §J Q67. Resolving Q67 closes this.
2. If PIO/hybrid: how many state machines / shift engines (4? 8?) and how much control logic?  
   → Research: **one** serial shift datapath (not N PIO SMs) + one RISC brain. No fixed SM count. Will try one and see if more is needed later on.
3. Which reference should we study first: RP2040 PIO datasheet sections or TI PRU (C6615/AM6P) spec? Who reads which?  
   → Decided: **RP2040 PIO** (Sam, 24 Sep 2026).
4. Is "programmable processor with cycle counters" (RISC) the right read of the competition brief, or do we chase pure cycle-accuracy (PIO)?  
   → Decided: **cycle-accurate** (Sam, 24 Sep 2026).

## B. Core & ISA

5. **[BLOCKING] Register count: 8 (A) or 16?**  
   → Research: **8** (16 is overkill for 8 pins; the shift datapath + ALU do the work).
6. **[BLOCKING] Instruction width: 16-bit (A) or 32-bit?**  
   → Research: **16-bit** (32-bit is overkill for a 16-bit datapath; keeps IMEM small).
7. Instruction memory: 256 words (A) or 512? (256 enforces tight firmware; 512 is safer for 3 protocols.)  
   → Research says **512** (256 too tight for bootloader + 3 protocols) — see §J Q70. Resolving Q70 closes this.
8. Immediate width: `LDR` imm6 (A, loops for long delays) vs imm9/imm12?  
   → **A: imm8** — widen the I-format immediate to `b7..b0` (imm8), and make `WAIT_CMP` **register-based** (`WAIT_CMP rs`: stall until 16-bit counter == r[rs]) so it needs no immediate at all.
   - imm8 is the smallest widening that kills both conflict drivers: `OE_SET/OE_CLR #imm8` gets the full 8-bit pin mask (imm6 can't reach bit 6 = SCL / bit 7 = stretch), and `LDR #imm8` (0–255) covers every required bit time as a single load (UART 104, SPI 12, I2C 120/30, JTAG/SWD 12, USB-LS 8).
   - imm9 doesn't fit a reg+imm field (5+3+9 = 17 > 16 bits) — only register-less ops, forcing a mixed encoding; imm12 is impossible (5+12 = 17 > 16).
   - `WAIT_CMP rs` removes the "9-bit counter compare" requirement entirely: firmware computes the target (`CNT_R` + `ADDS #n`), full 16-bit range, no new immediate width. `WAIT_EDGE #imm8` (pin = low 3 bits) keeps the encoding uniform.
   - Zero RTL cost: `b1..b0` were reserved-0 and become the low immediate bits — assembler/decode change only.
   - PS/2 (1200 cycles) and other >255 waits stay `DECR`/`JNZ` loops (stretch protocol).
   **Decided: imm8 + register-based `WAIT_CMP rs` (Sam, 24 Sep 2026).**
9. Add `CALL`/`RET` now (1-level, fixed reg R7) — cheap, recommended. Adopt?  
   → Research: **adopt now** (cheap, R7 as return reg).
10. Flags: Z + N only (A), or add V (carry/overflow)? V costs 1 ALU bit + flag logic.  
   → Research: **Z + N only** (V not needed for protocol firmware; CRC uses XOR, not carry).
11. All single-cycle execute (A)? Or allow a 2-cycle op (e.g. 16-bit multiply) if area allows?  
   → Research: **all single-cycle, no multiply** (keeps the core ~3–4 K; multiply is a stretch, not in the base ISA).
12. Invalid/reserved opcodes = NOP (A). Agree?  
   → **Adopt** (trivial default; safe on power-up / partial images).
13. PC width tied to SRAM size (8-bit @ 256 words). Words per program vs multiple programs? One big blob (A)?  
   → Research: **one big blob**; PC is now **9-bit** (512 words, per Q7).
14. How does the testbench load instruction SRAM (JB/JL style before run, or a frozen `rst_n` write phase)?  
   → Decided: **load during reset, then deassert** (Sam, 24 Sep 2026).
15. Is there a halt state / idle mode to lower power, or run-forever? (P&R power reports: do we care?)  
   → Decided: **run-forever, no halt state** (Sam, 24 Sep 2026).
16. Counter width: 16-bit free-running cycle counter? (max ~0.18 s @ 12 MHz)  
   → Research confirms **16-bit free-running** (no fractional divider at 12 MHz).

## C. Pin I/O & GPIO map

17. Whole-word 8-bit GPIO + OE mask (A draft in `isa.md` §4) vs per-pin enableFSR? (A: whole-word + mask is simpler.)  
   → Research: **whole-word + `OE_SET`/`OE_CLR` mask** (matches the `isa.md` §4 draft).
18. 1-cycle output latency (pad register) accepted as the timing contract?  
   → Research: **yes, 1-cycle pad latency is the timing contract** (latched on next posedge).
19. Confirm the draft bit map: bit0=TX, bit1=RX, 2=SCK, 3=MOSI, 4=MISO, 5=SDA, 6=SCL, 7=stretch. Conflict with your plans?  
   → Research **confirms this exact I/O map** (bit7 = stretch for USB/SWD/PS2).
20. Do `ui_in` (dedicated inputs) get used for anything? (A: leave unused, tie into `IN_RD` only.)  
   → Research: **`IN_RD` reads dedicated `ui_in`** (kept as a separate input path from `uio`).
21. Do we use `uo_out` at all (e.g. LED status / done flag)? Competition needs `uio` GPIOs, `uo` is free.  
   → Decided: **tie `uo_out` to a done/status flag** (Sam, 24 Sep 2026).
22. Open-drain (I2C) release convention: `OE_CLR` + pull-up model in TB. Agree?  
   → Research: **yes** (open-drain via `OE_SET`/`OE_CLR` + pull-up; non-negotiable for I2C/USB/SWD). Same as §J Q75.
23. Pin reuse across protocols (UART/SPI/I2C share 8 pins, firmware picks mode) vs fixed-per-protocol? (A: shared, firmware-driven.)  
   → Research: **shared, firmware-driven** (the whole point of a reprogrammable core).

## D. Clock & timing

24. 12 MHz target confirmed (A, chosen by Sam)? Friend concurs?  
   → Research: **12 MHz confirmed** (single clock, free-running counter, no fractional divider).
25. Fallback 8 MHz acceptable if 12 fails timing (drops USB stretch)?  
   → Decided: **yes, 8 MHz fallback acceptable** (confirmed with friend, 24 Sep 2026).
26. UART 115200 @ 12 MHz = 104 cycles/bit (0.17% drift). Acceptable? Or pick a baud like 9600 (1250 cycles/bit, cleaner)?  
   → Research: **accept the 0.17 % drift** (document it; no fractional divider needed).
27. UART: 8N1 only (A)? Any parity/stop-bit support needed?  
   → Decided: **8N1 default; parity/stop-bit as a firmware option** (Sam, 24 Sep 2026).
28. I2C: 100 kHz standard only (A), or 400 kHz too?  
   → Research: **100 kHz** (120 cycles/bit); 400 kHz (30 cycles/bit) is feasible in firmware if wanted.
29. SPI: target 1 Mbit/s max? (12 cycles/bit). Which CPOL/CPHA modes must we support — all 4 or just 0,0?  
   → Research: **1 Mbit/s (12 cycles/bit)**; CPOL/CPHA are **firmware** (set the SCK idle level via `OE`/`PIN_W`), so all 4 are possible in firmware.
30. USB 1.0 low-speed (1.5 Mbit/s = 8 cycles/bit @ 12 MHz): commit to stretch, or defer?  
   → Research: **keep as stretch** (8 cycles/bit, alive; optional bit-stuffing block if area allows).
31. 10 Mbit Ethernet @ 12 MHz is not feasible (1.2 cycles/bit). Drop the stretch, or dual-clock 12.5 MHz subdomain (area cost)?  
   → Research: **drop** (document as out-of-scope; no dual-clock subdomain).

## E. Protocols & firmware scope

32. Master-only vs master+slave for SPI (competition lists both)? If both: 2 cores? Firmware role switch?  
   → Decided: **master + slave** (firmware role switch, single core) (Sam, 24 Sep 2026).
33. I2C master-only (A) vs master+slave?  
   → Decided: **master + slave** (Sam, 24 Sep 2026).
34. Which target addresses/speeds must the demo cover (pick concrete test corners)?  
   → Decided: **defer** — pick concrete corners during M3 (Sam, 24 Sep 2026).
35. Baud range for UART (fixed 115200 (A) vs 4800–115200 selectable)?  
   → Decided: **9600–115200 selectable** (Sam, 24 Sep 2026).
36. Error reporting path for framing errors (e.g. RX parity/framing): status register in GPIO? `uo_out` flag?  
   → Decided: **yes, status flag** (Sam, 24 Sep 2026).
37. Which protocol goes into the public `docs/info.md` demo? (A: UART TX + RX as hero, SPI/I2C as additional.)  
   → Decided: **ALL** (UART/SPI/I2C + JTAG demo) (Sam, 24 Sep 2026).
38. Need a "watchdog / reset-on-hang" behavior? (TB kills overruns; core has none by default.)  
   → Decided: **not needed** (Sam, 24 Sep 2026).

## F. Verification (scored — be ambitious)

39. What is our "verification story" one-liner? (A: unit TB per block + bit-accurate protocol checkers + 1000-seed constrained-random + gate-level re-run.)  
   → Decided: **yes** (Sam, 24 Sep 2026).
40. Coverage: do we generate/track coverage (cocotb coverage / Verilog covergroups)?  
   → Decided: **Verilog covergroups** (Sam, 24 Sep 2026).
41. Golden model: build a full-cycle-accurate Python golden model of the core and trace-compare every test? (A: yes, high value.)  
   → Decided: **yes** (Sam, 24 Sep 2026).
42. Negative tests: inject 1-bit UART corruption, NACK, framing error — how many? (A: 3 canonical.)  
   → Decided: **3 canonical** (Sam, 24 Sep 2026).
43. Stress: 1000 seeds × mixed-protocol bistream? Runtime OK on iverilog? (A: yes, ~minutes each — verify early.)  
   → Decided: **yes** (Sam, 24 Sep 2026).
44. Waveform retention: keep `tb.fst` + `tb.gtkw` per protocol as submission evidence?  
   → Decided: **yes** (Sam, 24 Sep 2026).
45. FPGA pre-check on friend's board: which FPGA/board? Map which `uio` bits to which FPGA pins? Write a `docs/fpga.md` plan?  
   → Decided: **yes; board = AUP_ZU3** (Zynq UltraScale+ ZU3) (Sam, 24 Sep 2026).
46. Do we require a multi-day overnight regression run? (Nice-to-have.)  
   → Decided: **yes** (Sam, 24 Sep 2026).

## G. Area, flow & hardening

47. Usable area ceiling: 18K cells (A) leaving 6K for clock tree/routing vs 22K (tighter with more firmware)?   
   → Research: **18 K ceiling** (core ~8 K typical / ≤10 K worst → comfortable headroom).
48. SRAM: synthesizable FF-based (safe, ~large) vs Tiny Tapeout SRAM macro (smaller, if available)? Decide after M2.5 synthesis.  
   → Decided: **use the Tiny Tapeout SRAM macro** (TT prioritized; Sam, 24 Sep 2026). ⚠️ Disagrees with the research FF-based note — budget for the macro's **registered-read latency** (see `competitor-research.md` pitfall #2: the IEEE Simple CPU went multi-cycle for it).
49. Yosys + IHP 130nm Liberty available in our flow? Confirm PDK/Lib presence before M4  
   → Decided: **yes** (Sam, 24 Sep 2026).
50. OpenLane vs LibreLane for P&R (TT uses OpenLane + Sky130-derived? — confirm IHP 130 flow files exist).  
   → Decided: **OpenLane** (Sam, 24 Sep 2026).
51. Timing margin target: 0 ns slack (A) vs 5% slow corner?  
   → Decided: **5% slow corner** (Sam, 24 Sep 2026).
52. RTL freeze point: after M3.5 (all protocol tests green) + gate-sim = P&R input. Agree?  
   → Decided: **agree** (Sam, 24 Sep 2026).
53. Gate-level: full M3 suite on netlist (A) — which subset if runtime too long?  
   → Decided: **full suite; measure runtime near the end** (Sam, 24 Sep 2026).
54. SDF annotation for gate-level? (A: yes if flow supports.)  
   → Decided: **yes, if flow supports** (Sam, 24 Sep 2026).
## H. Team & process

55. **Division of labor**: who does RTL (M2), who firmware (M3), who flow (M4)?  
   → Decided: **work together, paired programming** (Sam + Muath, 24 Sep 2026).
56. Cadence: weekly demo of one gate passing? Weekly?  
   → Decided: **not needed** (Sam, 24 Sep 2026).
57. Branch model: `main` always green + feature branches? (A: yes.)  
   → Decided: **keep `main` green; feature branches** (Sam, 24 Sep 2026).
58. Private vs public line: AI planning docs, plans, prompts stay private (`docs/plan-guide` pattern). Agree the list?  
   → Decided: **private, stays in `docs/`** (Sam, 24 Sep 2026).
59. Milestones: M1 done 1 wk, M2 done 3 wks, M3 done 6 wks, M4 by 18 Jan. Realistic?  
   → Decided: **milestones look good; Gantt chart needed** (Sam, 24 Sep 2026) — see `PLAN.md` §Milestones.
60. Deadlock/tie-break for architecture: who has final say?  
   → Deferred (Sam, 24 Sep 2026).
61. What must be in the final repo for scoring: blog post? unit docs? run scripts? (Read competition rubric — add specific items here.)  
   → TBD (Sam, 24 Sep 2026).

## I. Competition / submission specifics

62. Confirm deadline 18 Jan 2027 = GDS-for-shuttle(2027 Mar CMOS5L)?  
   → Decided: **yes** (Sam, 24 Sep 2026).
63. Max collaborators / who is official author?  
   → Decided: **Sam and Muath** (24 Sep 2026).
64. Is a public blog post part of scoring? Deadline?  
   → Decided: **no** (Sam, 24 Sep 2026).
65. Prize/score rubric: which axes are weighted (verification? novelty? area?) — pin down our emphasis.  
   → TBD (Sam, 24 Sep 2026).
66. Any stipulation about "novel" vs "known" protocol? (All standard; novelty = emulator concept + custom ISA.)  
   → Decided: **emphasize novelty** (emulator concept + custom ISA) (Sam, 24 Sep 2026).

## J. Reprogrammability & boot path (from `docs/sam/architecture-research.md`, 24 Sep 2026)

> Research recommendation: **hybrid** = RISC brain + hardware shift datapath +
> event-driven stalls + open-drain I/O + ROM bootloader. ~8 K cells typical.

67. **[BLOCKING] Adopt the hybrid model (RISC + shift datapath + `WAIT_EDGE`/`WAIT_CMP`)?**
    A: yes. This is the "what we'd do differently from PIO/PRU" answer.  
    → **Closed by §A Q1/Q4** (hybrid, cycle-accurate) (Sam, 24 Sep 2026).
68. **[BLOCKING] Event-driven stalls: `WAIT_EDGE pin` + `WAIT_CMP imm`, with hardware
    timeout (else a hang = dead chip)?** A: yes, both, with a 16-bit timeout.  
    → **Closed by §B Q8**: `WAIT_CMP` is **register-based** (`WAIT_CMP rs`); `WAIT_EDGE #imm8` (pin = low 3 bits); 16-bit timeout.
69. **Hardware shift datapath: 16-bit serial in/out register (MSB-first) + `SHIFT_IN`/`SHIFT_OUT`?**
    A: yes. Makes UART/SPI/JTAG/PS2 one-shift-per-bit; frees ALU for CRC.  
    → **Confirmed** (Sam, 24 Sep 2026).
70. **IMEM size: 256 → 512 words (research says 256 too tight for bootloader + 3 protocols)?**
    A: 512.  
    → **Closed by §B Q7** (512 words, TT SRAM macro per §G Q48).
71. **ROM-resident UART-RX bootloader (~64–128 words) for post-fab firmware loading?**
    A: yes — this is the explicit "reprogrammable after fabrication" claim.  
    → **Confirmed** (Sam, 24 Sep 2026).
72. **Stretch blocks (bit-stuffing / CRC assist / micro-sequencer): compile-out by default, enable only if area allows?**
    A: yes, parameterized off.  
    → **Confirmed** (Sam, 24 Sep 2026).
73. **Drop 10 Mbit Ethernet from the stretch list (1.2 cycles/bit @12 MHz — infeasible)?**
    A: yes, document as out-of-scope. Keep USB 1.0 low (8 cycles/bit).  
    → **Confirmed** (Sam, 24 Sep 2026).
74. **Demonstration protocol: add JTAG or SWD as a *firmware-only* demo (no RTL change) to prove reprogrammability?**
    A: JTAG (4-wire, TAP state machine — impressive, fits 8 pins).  
    → **Confirmed: JTAG** (Sam, 24 Sep 2026).
75. **Open-drain I/O model: `OE_SET`/`OE_CLR` + pull-up in TB. Confirm as the I2C/USB/SWD convention?**
    A: yes. (Same decision as §C Q22.)  
    → **Closed by §C Q22** (Sam, 24 Sep 2026).
76. **Shift datapath width: 16-bit serial in/out register, MSB-first, direction configurable?**
    A: yes (16-bit matches the 16-bit data path; MSB-first = natural UART/SPI/JTAG order).  
    → **Confirmed** (Sam, 24 Sep 2026).
77. **Boot ROM size: ~64–128 words for the UART-RX bootloader? Enough to cover a full firmware image over 8 pins at 115200?**
    A: 128 words (headroom for framing + a simple checksum).  
    → **Confirmed: 128 words** (Sam, 24 Sep 2026). 
