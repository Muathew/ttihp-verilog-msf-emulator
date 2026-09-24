# Design Questions — Protocol Emulator ASIC (private)

> For discussion with collaborator. `[BLOCKING]` = answer before M2 RTL starts.
> Bracket the agreed answer, e.g. `→ Decided: 8 regs (Sam, 23 Sep 2026)`.
>
> Legend: **S** = Sam, **F** = Friend, **A** = AI suggestion (adopt/reject).

## A. Architecture model

1. **[BLOCKING] Core model: PRU-style RISC (A: yes), PIO-style state machines, or hybrid RISC+shift engines?**
2. If PIO/hybrid: how many state machines / shift engines (4? 8?) and how much control logic?
3. Which reference should we study first: RP2040 PIO datasheet sections or TI PRU (C6615/AM6P) spec? Who reads which?
4. Is "programmable processor with cycle counters" (RISC) the right read of the competition brief, or do we chase pure cycle-accuracy (PIO)?

## B. Core & ISA

5. **[BLOCKING] Register count: 8 (A) or 16?**
6. **[BLOCKING] Instruction width: 16-bit (A) or 32-bit?**
7. Instruction memory: 256 words (A) or 512? (256 enforces tight firmware; 512 is safer for 3 protocols.)
8. Immediate width: `LDR` imm6 (A, loops for long delays) vs imm9/imm12?
9. Add `CALL`/`RET` now (1-level, fixed reg R7) — cheap, recommended. Adopt?
10. Flags: Z + N only (A), or add V (carry/overflow)? V costs 1 ALU bit + flag logic.
11. All single-cycle execute (A)? Or allow a 2-cycle op (e.g. 16-bit multiply) if area allows?
12. Invalid/reserved opcodes = NOP (A). Agree?
13. PC width tied to SRAM size (8-bit @ 256 words). Words per program vs multiple programs? One big blob (A)?
14. How does the testbench load instruction SRAM (JB/JL style before run, or a frozen `rst_n` write phase)? A: load during reset, then deassert.
15. Is there a halt state / idle mode to lower power, or run-forever? (P&R power reports: do we care?)
16. Counter width: 16-bit free-running cycle counter? (max ~0.18 s @ 12 MHz)

## C. Pin I/O & GPIO map

17. Whole-word 8-bit GPIO + OE mask (A draft in `isa.md` §4) vs per-pin enableFSR? (A: whole-word + mask is simpler.)
18. 1-cycle output latency (pad register) accepted as the timing contract?
19. Confirm the draft bit map: bit0=TX, bit1=RX, 2=SCK, 3=MOSI, 4=MISO, 5=SDA, 6=SCL, 7=stretch. Conflict with your plans?
20. Do `ui_in` (dedicated inputs) get used for anything? (A: leave unused, tie into `IN_RD` only.)
21. Do we use `uo_out` at all (e.g. LED status / done flag)? Competition needs `uio` GPIOs, `uo` is free.
22. Open-drain (I2C) release convention: `OE_CLR` + pull-up model in TB. Agree?
23. Pin reuse across protocols (UART/SPI/I2C share 8 pins, firmware picks mode) vs fixed-per-protocol? (A: shared, firmware-driven.)

## D. Clock & timing

24. 12 MHz target confirmed (A, chosen by Sam)? Friend concurs?
25. Fallback 8 MHz acceptable if 12 fails timing (drops USB stretch)?
26. UART 115200 @ 12 MHz = 104 cycles/bit (0.17% drift). Acceptable? Or pick a baud like 9600 (1250 cycles/bit, cleaner)?
27. UART: 8N1 only (A)? Any parity/stop-bit support needed?
28. I2C: 100 kHz standard only (A), or 400 kHz too?
29. SPI: target 1 Mbit/s max? (12 cycles/bit). Which CPOL/CPHA modes must we support — all 4 or just 0,0?
30. USB 1.0 low-speed (1.5 Mbit/s = 8 cycles/bit @ 12 MHz): commit to stretch, or defer?
31. 10 Mbit Ethernet @ 12 MHz is not feasible (1.2 cycles/bit). Drop the stretch, or dual-clock 12.5 MHz subdomain (area cost)?

## E. Protocols & firmware scope

32. Master-only vs master+slave for SPI (competition lists both)? If both: 2 cores? Firmware role switch?
33. I2C master-only (A) vs master+slave?
34. Which target addresses/speeds must the demo cover (pick concrete test corners)?
35. Baud range for UART (fixed 115200 (A) vs 4800–115200 selectable)?
36. Error reporting path for framing errors (e.g. RX parity/framing): status register in GPIO? `uo_out` flag?
37. Which protocol goes into the public `docs/info.md` demo? (A: UART TX + RX as hero, SPI/I2C as additional.)
38. Need a "watchdog / reset-on-hang" behavior? (TB kills overruns; core has none by default.)

## F. Verification (scored — be ambitious)

39. What is our "verification story" one-liner? (A: unit TB per block + bit-accurate protocol checkers + 1000-seed constrained-random + gate-level re-run.)
40. Coverage: do we generate/track coverage (cocotb coverage / Verilog covergroups)?
41. Golden model: build a full-cycle-accurate Python golden model of the core and trace-compare every test? (A: yes, high value.)
42. Negative tests: inject 1-bit UART corruption, NACK, framing error — how many? (A: 3 canonical.)
43. Stress: 1000 seeds × mixed-protocol bistream? Runtime OK on iverilog? (A: yes, ~minutes each — verify early.)
44. Waveform retention: keep `tb.fst` + `tb.gtkw` per protocol as submission evidence?
45. FPGA pre-check on friend's board: which FPGA/board? Map which `uio` bits to which FPGA pins? Write a `docs/fpga.md` plan?
46. Do we require a multi-day overnight regression run? (Nice-to-have.)

## G. Area, flow & hardening

47. Usable area ceiling: 18K cells (A) leaving 6K for clock tree/routing vs 22K (tighter with more firmware)?
48. SRAM: synthesizable FF-based (safe, ~large) vs Tiny Tapeout SRAM macro (smaller, if available)? Decide after M2.5 synthesis.
49. Yosys + IHP 130nm Liberty available in our flow? Confirm PDK/Lib presence before M4.
50. OpenLane vs LibreLane for P&R (TT uses OpenLane + Sky130-derived? — confirm IHP 130 flow files exist).
51. Timing margin target: 0 ns slack (A) vs 5% slow corner?
52. RTL freeze point: after M3.5 (all protocol tests green) + gate-sim = P&R input. Agree?
53. Gate-level: full M3 suite on netlist (A) — which subset if runtime too long?
54. SDF annotation for gate-level? (A: yes if flow supports.)

## H. Team & process

55. **Division of labor**: who does RTL (M2), who firmware (M3), who flow (M4)? (A: Sam RTL+docs, Friend firmware+verification, AI scaffolding.)
56. Cadence: weekly demo of one gate passing? Weekly?
57. Branch model: `main` always green + feature branches? (A: yes.)
58. Private vs public line: AI planning docs, plans, prompts stay private (`docs/plan-guide` pattern). Agree the list?
59. Milestones: M1 done 1 wk, M2 done 3 wks, M3 done 6 wks, M4 by 18 Jan. Realistic?
60. Deadlock/tie-break for architecture: who has final say? (A: Sam, backup by area estimate.)
61. What must be in the final repo for scoring: blog post? unit docs? run scripts? (Read competition rubric — add specific items here.)

## I. Competition / submission specifics

62. Confirm deadline 18 Jan 2027 = GDS-for-shuttle(2027 Mar CMOS5L)?
63. Max collaborators / who is official author?
64. Is a public blog post part of scoring? Deadline?
65. Prize/score rubric: which axes are weighted (verification? novelty? area?) — pin down our emphasis.
66. Any stipulation about "novel" vs "known" protocol? (All standard; novelty = emulator concept + custom ISA.)

## J. Reprogrammability & boot path (from `docs/sam/architecture-research.md`, 24 Sep 2026)

> Research recommendation: **hybrid** = RISC brain + hardware shift datapath +
> event-driven stalls + open-drain I/O + ROM bootloader. ~8 K cells typical.

67. **[BLOCKING] Adopt the hybrid model (RISC + shift datapath + `WAIT_EDGE`/`WAIT_CMP`)?**
    A: yes. This is the "what we'd do differently from PIO/PRU" answer.
68. **[BLOCKING] Event-driven stalls: `WAIT_EDGE pin` + `WAIT_CMP imm`, with hardware
    timeout (else a hang = dead chip)?** A: yes, both, with a 16-bit timeout.
69. **Hardware shift datapath: 16-bit serial in/out register (MSB-first) + `SHIFT_IN`/`SHIFT_OUT`?**
    A: yes. Makes UART/SPI/JTAG/PS2 one-shift-per-bit; frees ALU for CRC.
70. **IMEM size: 256 → 512 words (research says 256 too tight for bootloader + 3 protocols)?**
    A: 512. FF-based SRAM.
71. **ROM-resident UART-RX bootloader (~64–128 words) for post-fab firmware loading?**
    A: yes — this is the explicit "reprogrammable after fabrication" claim.
72. **Stretch blocks (bit-stuffing / CRC assist / micro-sequencer): compile-out by default, enable only if area allows?**
    A: yes, parameterized off.
73. **Drop 10 Mbit Ethernet from the stretch list (1.2 cycles/bit @12 MHz — infeasible)?**
    A: yes, document as out-of-scope. Keep USB 1.0 low (8 cycles/bit).
74. **Demonstration protocol: add JTAG or SWD as a *firmware-only* demo (no RTL change) to prove reprogrammability?**
    A: JTAG (4-wire, TAP state machine — impressive, fits 8 pins).
75. **Open-drain I/O model: `OE_SET`/`OE_CLR` + pull-up in TB. Confirm as the I2C/USB/SWD convention?**
    A: yes.
