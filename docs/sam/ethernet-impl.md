# 10Mbit Ethernet (10BASE-T) on msfcore — implementation note (private)

> Branch `ethernet`. Supersedes the "dropped — 1.2 cycles/bit, infeasible" line in
> `isa.md` §6 (Q73). This note records the feasibility math, the chosen strategy,
> the cycle/area budget, and the (honest) scope.

## TL;DR

10BASE-T is **feasible in firmware** if the core is overclocked from its 12 MHz
nominal to **20 MHz**. At 20 MHz one core cycle = 50 ns = one Manchester half-bit,
so firmware bit-bangs one `PIN_W` per half-bit (the PIO output stage latches each
level and holds it for a full cycle). **TX-only**: preamble (7×0xAA) + SFD (0xD5) +
a short frame. No RX, no CRC/FCS, no CSMA/CD.

## Feasibility math

- 10 Mbit/s → 1 bit = 100 ns; Manchester half-bit = 50 ns.
- @ 12 MHz (nominal): 1 cycle = 83.33 ns → 0.6 cycles/half-bit → **infeasible**
  (this is why Q73 dropped it).
- @ 20 MHz: 1 cycle = 50 ns → **exactly 1 cycle/half-bit** (integer, zero drift).
- @ 40 MHz: 1 cycle = 25 ns → 2 cycles/half-bit (safer, coarser cadence; insert
  one `NOP` after each `PIN_W`).

Chosen: **20 MHz, 1 cycle per half-bit.**

## Strategy comparison

| # | Strategy | Verdict |
|---|---|---|
| 1 | Overclock core (20 MHz) + firmware bit-bang | **Chosen.** No new RTL, no area, build stays green; 20 MHz is a 1.67× overclock of a ~4.3 K-cell core with short critical paths — plausible on IHP130. |
| 2 | Dedicated HW Manchester/shift datapath at a faster periph clock | Rejected. Tiny Tapeout has **one** clock; a faster peripheral clock needs a clock multiplier (area) + a new clock tree — not justified for a TX-only generator. The existing 16-bit OSR/ISR is clocked at the core rate and has no "load OSR from register" op, so it can't act as a fast level FIFO. |
| 3 | Dual-clock subdomain (12.5 MHz periph) | Rejected. Same clock-tree/area cost as (2) for no benefit at this scope. |

## Cycle / area budget

- Firmware: `OE_SET 1` + `LDR R1,#1` + 256 × `PIN_W` (256 half-bits) + hold loop
  ≈ **260 words** (fits the 384 usable SRAM words 128–511).
- Area: **unchanged** (~4.3 K cells, << 18 K gate). No RTL added.
- Timing: 20 MHz is a 1.67× overclock of the 12 MHz nominal (5% margin → 12.6 MHz).
  **Caveat:** the cocotb sim is timing-independent, so it validates the firmware's
  *cycle counting* at 20 MHz, not physical timing closure (deferred to M4.2 P&R).
  If 20 MHz is too aggressive, fall back to 40 MHz / 2 cycles per half-bit.

## What "10Mbit Ethernet" means here (scope)

- **In scope (TX):** Manchester-encoded preamble 7×0xAA (10101010, the IEEE 802.3
  10BASE-T preamble), SFD 0xD5, and a short 16-byte frame (DA 0x5555, SA 0x5555,
  LEN 0x0002, DATA 0xABCD) → 128 bits → 256 half-bits, emitted at 10 Mbit/s on
  `uio` pin 0.
- **Out of scope:** RX path, CRC/FCS, inter-frame gap, CSMA/CD, minimum 64-byte
  frame, full MAC state machine. A real 10 Mbit MAC is not achievable in this
  footprint; a TX bit-bang is the meaningful, achievable part.

## Manchester rule (per spec)

- '1' bit = low→high = `[0, 1]` half-bits.
- '0' bit = high→low = `[1, 0]` half-bits.
- Each bit = 2 half-bits; the **second** half-bit of the pair == the data bit
  (used by the test's decoder).

## Verification

- `test/test_extensions.py :: test_ethernet_10mbit_tx` (replaces the
  `test_ethernet_10mbit_analysis` stub).
- Run: `..\.venv\Scripts\python.exe run_tests.py -m test_extensions -t ethernet_10mbit_tx`
- Pass criteria: the 256-half-bit level sequence is recovered from `uio_out`
  bit 0; decoded bytes == the 16-byte frame; preamble == 7×0xAA; SFD == 0xD5;
  every half-bit pair is a valid transition.
