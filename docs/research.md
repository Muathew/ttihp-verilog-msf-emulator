# The brief and prior art

What the competition asks for, the published designs msfcore builds on, and why it
is a custom core rather than RISC-V. Our own design decisions are in
[history.md](history.md).

## The brief

From [the announcement](https://blog.janestreet.com/protocol-emulator-asic-competition/)
(10 Sep 2026), the lines that decide what scores:

* What they will fabricate: "the most novel designs". They are
  "particularly interested in projects with unique functionality, as well
  as those that demonstrate novel approaches to design and verification
  methodologies". There is no other rubric.
* What the chip is: "a tiny CPU with an instruction set designed for
  reading pins, writing pins, counting cycles, and hitting timing precisely
  enough that you can implement a real protocol in firmware rather than in
  fixed logic".
* What it is for: "a useful tool for hardware debugging and reverse
  engineering, which is a good part of what we do".
* What it is not: "The goal isn't to put a UART block, an SPI block, and an
  I2C block on one die and call it done"; it must support "new protocols
  after fabrication".
* Prior art to beat: RP2040 PIO and TI Sitara PRU, "and consider what you'd
  do differently".
* Protocols: UART, SPI, I2C; stretch low-speed USB and 10 Mbit Ethernet;
  also JTAG, SWD, PS/2, CAN. "Show us anything else your architecture
  makes possible that we haven't thought of."
* Verification: "formal methods, random constrained tests, AI-assisted
  verification, and more ... verification will be an extremely important
  aspect of the ASIC design flow going forwards". Hardcaml is what they use,
  not a requirement.
* FPGA: "If you have access to an FPGA, consider using it to test your RTL
  before the ASIC flow."
* Area: "The current maximum area is 6x4 tiles"; 8x4 is only a possibility
  they will announce if it happens. Deadline 18 Jan 2027, open source,
  teams recommended.

What this means for us: the judges are building a debugging and
reverse-engineering tool, and they weight verification method as heavily
as function. Features that make the chip an instrument (emulate a device,
watch a bus, stress a device's timing) and a well-documented verification
flow (formal, random, FPGA, AI-assisted) count for more than raw protocol
count.

## Prior art

Closest shipped prior art on IHP (surveyed 24 Sep 2026): nanoPIO (latch-based 16-word store, per
instruction delay field, fits one tile), Kraken IO (runs real `pioasm`),
AstraPIO, a UART-SPI-I2C bridge, a CRC-protected UART loader, TinyQV
(RV32EC), a USB CDC device, and an IEEE workshop CPU that went multi-cycle to
cope with the SRAM macro's registered read.

What we took from it: a CRC-checked bootloader that refuses bad loads (done
in v2), an independent model for differential testing (done in v2),
double-flop every input (done in v2), run full P&R early and keep a known
green GDS. Ideas not taken (yet): a per-instruction delay field, latch-based
memory, a second parallel lane, a ring-oscillator timing canary, a
zero-cost formal trace port.

Where msfcore started from: a self-contained chip (ROM boot), a real RISC core
rather than a state machine, and a hardware shift path.

## Our approach compared with a RISC-V approach (18 Oct 2026)

msfcore is a custom 16-bit CPU whose instruction set is built for pins and
cycle-exact timing. The common alternative is
RISC-V, in one of three forms:

* **RISC-V alone**, bit-banging pins from C (e.g. TinyQV, an RV32EC SoC
  that shipped on IHP at 64 MHz in 3x2 tiles, running from external QSPI
  flash or PSRAM);
* **RISC-V as host beside a timing engine** (a small RISC-V core plus a
  programmable state machine that does the bit timing);
* **RISC-V with custom instructions** for pins and timing, using the
  `custom-0`/`custom-1` opcode space.

| Question | msfcore (custom ISA) | RISC-V |
|---|---|---|
| Exact timing | every instruction has a documented cycle cost; `WAIT_CMP` waits for an absolute cycle count, so bit schedules don't drift; blocking waits with timeouts | a pipelined core fetching from external flash or PSRAM has variable latency, so exact bit timing needs a separate engine or custom instructions plus a guaranteed-deterministic pipeline |
| Fit to the brief | the brief describes "a tiny CPU with an instruction set designed for reading pins, writing pins, counting cycles, and hitting timing precisely": this is that | a standard ISA is the opposite of novel unless the novelty is elsewhere (in a timing engine beside it) |
| Area | 6,893 cells for the whole chip, two threads, SERDES and CRC included: 24 % of 6x4 | a 32-bit core with 16 x 32-bit registers is larger per core before any timing hardware |
| Program memory | 384 words of firmware plus 128 of data RAM on chip; a ROM boot loader; nothing external | RV32 code is larger; TinyQV runs from external memory, so the chip needs a flash or PSRAM PMOD |
| Toolchain | our own assembler and model; no C compiler | GCC/LLVM, C, debuggers, simulators, libraries: the biggest RISC-V advantage |
| Verification | our ISA model, random differential testing and formal properties, all written by us | riscv-formal, compliance suites and a mature ecosystem for the core; the timing engine still needs its own verification |
| Concurrency | two deterministic hardware threads | one hart, unless the design adds engines |
| Cost to switch now | none | rewrite the core, firmware, boot path and verification three months before the 18 Jan deadline |

**Conclusion:** keep the custom core. It is the brief's design almost word
for word, it is small, it runs from on-chip memory, and the timing is a
property of the ISA, not of extra hardware. The full brief strengthens this:
a standard ISA does nothing for "novel" or for "what you'd do differently"
from PIO and PRU. One point goes the other way: for a debugging instrument,
a host CPU that runs upper protocol layers and talks to the user is useful,
which is a fair argument for that split; on the board the Zynq PS
plays that role for us. The real cost is the toolchain: firmware is assembly
only. Ways to narrow that gap without switching:

* assembler macros and a small firmware library for common patterns
  (bit schedules, CRC setup, open-drain bytes);
* a C-like front end for the subset protocols need (loops, bit tests, pin
  and wait intrinsics) that emits msfcore assembly;
* keep the PS-side C (fpga/ps) as the host language for driving and
  checking the chip, which is where C helps most today.

If a future version needs a general host processor, a RISC-V host beside a
deterministic timing engine is the model to follow,
with msfcore as the timing engine.
