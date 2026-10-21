# Competitor research

Surveys of the other entries in the Jane Street protocol-emulator ASIC
competition and of related Tiny Tapeout prior art. Each survey is kept as a
dated snapshot; add new ones as a new dated section at the end. Our own
design decisions are in [history.md](history.md).

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

## Survey of 24 Sep 2026


Direct entries found on 24 Sep (all URL-checked then):

| Entry | Approach | State on 24 Sep |
|---|---|---|
| [ProtocolGremlin](https://github.com/Vedant817/ProtocolGremlin) | 24-opcode ISA, 4 registers, `WAITEDGE`, shift in/out, open drain, 256-byte program RAM, CRC-8 bootloader | 19,291 cells, 92 % of it flip-flop program RAM; 509 tests, Z3 BMC, mutation testing |
| [GP_PAE](https://github.com/sjrai007/gp_pae) | N small sequencer "lanes" sharing a pin crossbar; Hardcaml | ~12.4K cells; RTL checked against an OCaml interpreter; UART TX only |
| [protocol-emulator](https://github.com/Abagel-coder/protocol-emulator) | time-triggered CPU with line-coding pin engines | UART TX warm-up; LibreLane stalled locally |
| [janestreet.asic.protocol-emulator](https://github.com/mtanneer/janestreet.asic.protocol-emulator) | scaffold with strong CI | no CPU yet |
| protocol-emulator-asic (Kleven2k) | USB-LS and 10BASE-T claimed | repository not public |

Closest shipped prior art on IHP: nanoPIO (latch-based 16-word store, per
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

Our position then: a self-contained chip (ROM boot), a real RISC brain
rather than a state machine, and a hardware shift path; weaker on
parallelism and clock speed than some entries.

## Update of 18 Oct 2026

The competition page is unchanged: deadline 18 Jan 2027, 6x4 tiles with
8x4 "being worked on", no published rubric beyond "unique functionality"
and "novel approaches to design and verification". The field has grown from
5 entries to about 55 public repositories. Most are the empty template or a
first UART; about 15 show real work (judged by commits, size and docs).
The search was by repository name and description, so entries without
either can be missed (PRISM was found only by name, after a tip).
Claims below are as the entrants state them in their READMEs and datasheets
(read 18 Oct), not checked by us.

| Entry | Approach | State on 18 Oct | Where we're ahead | What to take from them |
|---|---|---|---|---|
| [kdp1965/ihp-um-janestreet-prism](https://github.com/kdp1965/ihp-um-janestreet-prism) (PRISM) | the strongest entry found. A programmable state machine (32 states, 128-bit state words, LUT-based branch decisions, splittable into two 16-state shards) loaded as a bitstream compiled from an ordinary Verilog state machine, beside a TinyQV RISC-V host with DMA to PSRAM. Per-shard datapath: counters, shifters, 2 KB SRAM FIFO, CRC 8/16/32, bit-stuff unit, Manchester recovery. Execution tracer into SRAM, halt / single-step / breakpoint debugger. State table in latch-array macros (DFFRAM flow); custom LibreLane step for macro power | ported from an earlier Tiny Tapeout shuttle. 8x4 tiles, 64 MHz, GDS green in CI on 15 Oct. In simulation: UART, SPI master (quad) and slave, I2C master and slave, 1-Wire, WS2812, USB LS device, 10BASE-T TX and RX together, CAN 2.0A, HDLC, JTAG, SWD, SpaceWire, PS/2, VGA | formal proofs and mutation checks on our core (PRISM's formal files cover only the TinyQV ALU and QSPI it inherits); one CPU does both the timing and the logic, so there is one thing to program instead of a state machine plus a RISC-V; fits the stated 6x4 tiles with 24 % of the area used; independent ISA model with random differential testing; FPGA tests that step the real core | an execution tracer and halt / single-step / breakpoint debugger; Manchester bit recovery so 10BASE-T can be received; SPI and I2C slave firmware; FIFOs with DMA so the CPU isn't in every byte; compiling protocols from a higher-level description |
| [Abagel-coder/protocol-emulator](https://github.com/Abagel-coder/protocol-emulator) | deterministic time-triggered CPU; ISA in one YAML file that generates the assembler, simulator and decode; host SPI port | closest to us. v1.0 with a 512 x 16 IHP SRAM instruction memory: 6,797 cells + macro, GDS green in CI on 17 Oct; UART RX and SPI firmware checked against independent reference models | two hardware threads; SERDES and CRC unit in hardware; USB LS and 10BASE-T firmware; formal proofs; FPGA port with C tests | one ISA file that generates the assembler, simulator and decode (our isa.md, assembler and model are kept in step by hand); their documented route to a green GDS with the SRAM macro in the fetch loop; a results table per hardening run |
| [TeslaCoilerOW/ttihp-protocol-emulator](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator) | Hardcaml; four engines running at once, each with a private 64-instruction memory (eight SRAM macros), TX/RX queues, mailboxes and an autonomous mover that forwards data between engines (e.g. UART in, SPI out) | designed at 8x4 tiles; GDS run in CI | fits 6x4 tiles; one general CPU with a firmware library that includes USB and 10BASE-T; deeper verification (independent model, random diff, formal) | an autonomous mover that forwards data between engines without the host (e.g. UART in, SPI out): our two threads could get mailboxes or a shared FIFO; the published GDS viewer |
| [TejasDasa/protocol-emulator-asic](https://github.com/TejasDasa/protocol-emulator-asic) (STT) | five state machines executing one 32-bit row per cycle, at most 32 rows per program, no pipeline or stalls | UART TX/RX, SPI mode 0, I2C master and target, USB LS token TX, JTAG, CAN; formal properties each with a deliberate break; area study; FPGA bring-up | a general CPU (their programs are at most 32 rows); USB TX and RX, 10BASE-T; full register sets per thread; random differential testing against an independent model | I2C target firmware and its response-latency analysis; a validation table mapping every claim to the method and artifact that proves it, and what is not validated; bench wiring for FPGA bring-up |
| [jlsviper/fipe](https://github.com/jlsviper/fipe) | pin events computed ahead and committed to a queue with exact due times on a 24-bit timebase; skew registers inject timing faults on purpose; four timing monitors flag a device breaking the protocol | examples for I2C (controller and target), SPI, full-duplex UART, USB LS, CAN, WS2812, 1-Wire, JTAG, SWD | a CPU with general control flow; SERDES framing for USB and Ethernet; formal proofs (none seen in FIPE's summary); FPGA port | deliberate timing-fault injection (our absolute `WAIT_CMP` schedules can shift edges by exact amounts); protocol monitors that flag a device breaking the rules (our C models could run as monitors); trap on an illegal instruction; a conflict flag when two threads drive one pin |
| [umerimran-10xe/protocol-emulator-asic](https://github.com/umerimran-10xe/protocol-emulator-asic) | four state machines sharing a 64 x 16 store and one pin block; `SYS BARRIER` to sync them | loaded over an SPI config port | 384 words of firmware against a 64-word shared store; SERDES and CRC hardware; ROM boot loader | a barrier instruction so threads meet on the same cycle; per-pin ownership by thread to prevent conflicts |
| [smoothjazzchazz/THICotPCotDUIF](https://github.com/smoothjazzchazz/THICotPCotDUIF) | "waveform translator": the core never touches pins; input arrives as symbol events and output leaves through a transmit table; programmable LFSR for CRCs | specification stage; top module is still the template | working, tested RTL against a specification | a programmable glitch filter on inputs; a PRBS mode in the CRC unit |
| [pivotvlsi](https://github.com/pivotvlsi/jane_street_asic_protocol_emulator) | four 4-bit accumulator CPUs in parallel, able to snoop the same wire | firmware and testbenches | 16-bit datapath, hardware serial assists, deeper verification | two cores reading the same wire at once (e.g. one receives UART while the other checks parity): a good two-thread demo for us |
| [Kanishk234 (TRIPWIRE)](https://github.com/Kanishk234/protocol-emulator-asic) | planned lanes of triggered "reflex" instructions | 855 commits but still a placeholder counter | a working design against a placeholder | the idea of hardware "reflex" responses (e.g. an I2C ACK without waiting on the CPU) |
| [yibeizzZZ/FoRG](https://github.com/yibeizzZZ/FoRG-protocol-emulator), [raphaelroshan](https://github.com/raphaelroshan/jane-street-asic-lab), [pveligeti2](https://github.com/pveligeti2/jane-street-asic) | small engines (32 or 16 instructions) or a fixed UART baseline; pveligeti2 plans RP2040 PIO binary compatibility | early | scope and verification depth | pveligeti2's plan to run unmodified RP2040 PIO programs: an assembler front end that translates `pioasm` would be a strong demo |
| ProtocolGremlin, GP_PAE, mtanneer | as on 24 Sep | no pushes since 24 Sep (ProtocolGremlin) or 15 Sep (the other two) | IHP SRAM macro instead of flip-flop memory (ProtocolGremlin's is 92 % of its area); broader firmware | ProtocolGremlin's zero-area trace port for silicon debug; GP_PAE's parallel lanes; mtanneer's CI checks (lint, secret scanning, protected `main`) |

Ideas worth taking, ranked by value for effort (revised against the full
brief: it is a debugging and reverse-engineering tool):

1. **SPI and I2C target firmware** (PRISM, TejasDasa, FIPE): firmware
   only. Emulating a device, not just driving one, is the core of a
   reverse-engineering tool and closes our gap in the required protocols.
   *Taken (21 Oct):* `spi_target.s`, `i2c_target.s` ([instruments.md](instruments.md)).
2. **Bus monitor, timing-fault injection and protocol checks** (FIPE):
   firmware plus the existing C models. A passive sniffer on the RX engine,
   edges shifted on purpose with `WAIT_CMP`, and flags when a device breaks
   the protocol make the chip an instrument, which is what the brief says
   it is for. *Taken (21 Oct):* UART, I2C and SPI monitors as firmware on
   the chip itself (not the C models), and timing knobs on the controllers.
3. **A claims-to-evidence validation table** (TejasDasa): documentation
   only; verification method is half of what the judges reward.
   *Taken:* [verification.md](verification.md) §9, with every CI hardening
   run in §7 and the mutation score in §8.
4. **Thread mailbox or barrier** (TeslaCoilerOW, umerimran): small RTL
   change; lets the two threads hand data over, e.g. UART in, SPI out.
   *Taken without the RTL change:* a lock-free queue in data RAM
   (`ring.inc`) carried the monitors' events and the UART-to-SPI bridge
   (`firmware/bridge.s`). *Then with it (branch `rtl-changes`):* a hardware
   FIFO between the threads, one instruction to push or pop (`isa.md` §6.5).
5. **Trace and debug** (PRISM, ProtocolGremlin): our FPGA step mode already
   does this off-chip; an on-chip PC/pin trace would help silicon bring-up.
   *Taken (branch `rtl-changes`):* the debug view (`isa.md` §8) shows the
   PC, instruction and every register on `uo_out`, so the demo board's
   RP2040 can halt and single-step the chip.
6. **Manchester receive for 10BASE-T** (PRISM): RTL change in the RX
   engine; the stretch goal we only half meet.

The rest of the table, as of 21 Oct:

* *Also taken:* two readers on one wire (pivotvlsi): `mon_uart.s` decodes
  each frame with the RX engine while thread 0 times the same frame; a
  results table per hardening run (Abagel-coder): `verification.md` §7;
  WS2812 (PRISM, FIPE): `ws2812.s`; what is not validated (TejasDasa):
  `verification.md` §9. A published GDS viewer (TeslaCoilerOW) was already
  there (linked from the README).
* *Not taken: RTL changes,* held back so the green GDS stands: the tracer
  and debugger (5), Manchester receive (6), FIFOs with DMA, a trap on an
  illegal instruction, a flag when two threads drive one pin, a barrier,
  per-pin ownership by thread, an input glitch filter, hardware reflex
  responses, more engines.
* *Not taken: tooling:* one ISA file that generates the assembler, model
  and decode (Abagel-coder); a `pioasm` translator (pveligeti2); compiling
  protocols from a higher-level description (PRISM); a PRBS mode, which
  the CRC unit could provide from firmware by shifting in zeros; lint in
  CI and a protected `main` (mtanneer).
* *Still missing against the brief:* firmware for JTAG, SWD, PS/2 and CAN,
  which the brief names and PRISM and FIPE show. Ours are only the v1
  waveform-playback demos in `test_extensions.py`, generated in Python
  and checked loosely. Firmware only; JTAG and SWD are the debug ports a
  reverse-engineering tool most needs.

What changed in the field:

* **Parallelism is the common bet.** Most serious entries run 4–5 engines
  or lanes at once; we have two threads.
* **Hardcaml is spreading** (TeslaCoilerOW, FIPE, GP_PAE, LeEmperor),
  which is Jane Street's own tool.
* **Green GDS with SRAM macros** is now shown by at least three entries
  (PRISM, Abagel-coder, TeslaCoilerOW). So is ours: v2 has hardened in
  every GDS run since 17 Oct ([verification.md](verification.md) §7).
* **8x4 tiles are being used** (PRISM, TeslaCoilerOW), but the rules say
  "the current maximum area is 6x4 tiles". Unless 8x4 is approved, those
  designs don't comply; PRISM's README calls 8x4 "the size allowed by the
  Jane Street competition", which the page does not say. We fit 6x4.
* **The bar for protocol coverage is PRISM's**: it receives 10BASE-T as well
  as transmitting it, and adds CAN, HDLC, SpaceWire and VGA, with a host CPU
  for the upper layers and a debugger for the state machine.
* **Acting as the target, not only the controller**: TejasDasa and FIPE
  answer as an I2C target. We planned SPI and I2C slaves (questions 32–33)
  but only built masters (now both: 21 Oct).
* **New ideas**: FIPE turns the emulator into a test instrument (timing
  fault injection and protocol monitors); TeslaCoilerOW moves data between
  engines without the host.

Where we still stand out: a CPU whose own instructions hit exact cycle
timing (PRISM splits the work between a state-machine engine and a RISC-V);
broad firmware coverage, including USB low-speed TX and RX with CRC16 and
10BASE-T transmit with FCS; an independent ISA model with random
differential testing, formal proofs and mutation checks (PRISM's only
formal files are the ALU and QSPI-controller proofs it inherits from TinyQV;
PRISM itself is tested in simulation); two independent boot paths; and an
FPGA port whose C tests step the real core cycle by cycle, which the brief
asks for by name. Against PRISM we are behind on protocol breadth, Ethernet
receive and debug features, but within the current 6x4 limit.

Updated recommendations:

1. Push v2 through the GDS action and get it green: the strongest rivals
   already have that. *Done 17 Oct.*
2. Add SPI and I2C target firmware (the slave models in `test_firmware.py`
   show what the master side expects). *Done 21 Oct.*
3. Lead the write-up with the two things the brief rewards: the chip as a
   debugging instrument (device emulation, bus monitoring, timing-fault
   injection) and the verification method (independent model, random
   differential testing, formal with mutation checks, cycle-stepped FPGA
   tests in C, and the AI-assisted workflow used to build them, described
   honestly). Answer "what you'd do differently" from PIO and PRU directly.
4. Stay at 6x4 unless 8x4 is announced; the area headroom (24 % used)
   already allows more threads or engines either way.

## Our approach compared with a RISC-V approach (18 Oct 2026)

msfcore is a custom 16-bit CPU whose instruction set is built for pins and
cycle-exact timing. The alternative most often seen in the field is
RISC-V, in one of three forms:

* **RISC-V alone**, bit-banging pins from C (e.g. TinyQV, an RV32EC SoC
  that shipped on IHP at 64 MHz in 3x2 tiles, running from external QSPI
  flash or PSRAM);
* **RISC-V as host beside a timing engine** (PRISM: TinyQV plus a
  programmable state machine that does the bit timing);
* **RISC-V with custom instructions** for pins and timing, using the
  `custom-0`/`custom-1` opcode space (not seen in the field yet).

| Question | msfcore (custom ISA) | RISC-V |
|---|---|---|
| Exact timing | every instruction has a documented cycle cost; `WAIT_CMP` waits for an absolute cycle count, so bit schedules don't drift; blocking waits with timeouts | a pipelined core fetching from external flash or PSRAM has variable latency, so exact bit timing needs a separate engine (PRISM) or custom instructions plus a guaranteed-deterministic pipeline |
| Fit to the brief | the brief describes "a tiny CPU with an instruction set designed for reading pins, writing pins, counting cycles, and hitting timing precisely": this is that | a standard ISA is the opposite of novel unless the novelty is elsewhere (PRISM puts it in the state machine) |
| Area | 6,893 cells for the whole chip, two threads, SERDES and CRC included: 24 % of 6x4 | a 32-bit core with 16 x 32-bit registers is larger per core before any timing hardware; PRISM needs 8x4 tiles |
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
which is a fair argument for PRISM's split; on the board the Zynq PS
plays that role for us. The real cost is the toolchain: firmware is assembly
only. Ways to narrow that gap without switching:

* assembler macros and a small firmware library for common patterns
  (bit schedules, CRC setup, open-drain bytes);
* a C-like front end for the subset protocols need (loops, bit tests, pin
  and wait intrinsics) that emits msfcore assembly;
* keep the PS-side C (fpga/ps) as the host language for driving and
  checking the chip, which is where C helps most today.

If a future version needs a general host processor, PRISM's split (a
RISC-V host plus a deterministic timing engine) is the model to follow,
with msfcore as the timing engine.
