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

## Update of 23 Oct 2026

About 20 repositories have appeared since 18 Oct. The old leaders (PRISM,
TeslaCoilerOW, TejasDasa, umerimran, ProtocolGremlin) had not pushed since.
The new strong entries are all on IHP SG13CMOS5L at 6x4 tiles, and they
share one theme we lacked: **timing you can prove**, not only test. As
before, the claims are as the entrants state them (read 23 Oct), not
checked by us.

| Entry | Approach | What stands out |
|---|---|---|
| [MarcosAsh/protocol-emulator](https://github.com/MarcosAsh/protocol-emulator) | Hardcaml; two cores, three SRAM macros; 48 MHz with +3.49 ns at the slow corner | the assembler's timing analyser certifies each program's deadlines, with the analyser's kernel proved sound against the RTL; a CI job proving the hardened netlist equals the RTL; formal "teeth" (weakened copies must fail); 47 firmware images, among them PS/2, JTAG, SWD, CAN, 10BASE-T, 1-Wire, DShot600, SENT and CEC; an ECP5 board demo |
| [parthDOOM/escapement](https://github.com/parthDOOM/escapement) | a four-context barrel processor whose hardware admits or refuses each timed event | an event it cannot meet is refused, never late, with an unbounded proof over every program; the gate-level netlist proved sequentially equivalent to the RTL; a capture "tape" in SRAM; power per workload (3.0 mW sending UART); an execution digest; device emulation (24C02 EEPROM, 25xx flash); JTAG, SWD, CAN, PS/2, 1-Wire, NEC IR |
| mrnathanhumphrey-droid | five lanes with earliest-deadline-first scheduling | worst-case response times from a real-time analysis, cross-checked with pyRTA; FPGA tests with real parts |
| ArkaKhorchidian (Polyglot) | three cores and a programmable line coder (any CRC, stuffing, NRZI, Manchester) | CAN TX, a pin crossbar, breakpoints, single step and fault injection |
| 2AMLogic | the whole repository built by an AI agent | `docs/submission/ai-use.md`, an account of how AI was used |
| mcranny | | unbounded proofs; a results page |
| Kanishk234 (TRIPWIRE) | | over 100 commits in five days, but the README is still the template |

What we have taken since, on branch `fixes` (`docs/plan.md` §3–§4):

* **A static deadline checker**, `tools/msf_timing.py`: every `WAIT_CMP` in
  our firmware is certified on every path, cross-checked against the ISA
  model, in CI (`verification.md` §6b). Ours is checked against the model,
  not proved sound against the RTL as MarcosAsh's is.
* **A missed deadline is never silent:** `WAIT_CMP` to a passed target now
  releases at once and sets a per-thread LATE flag (ISA v2.2), with formal
  properties F10/F11 and a mutant that must fail. Escapement goes further
  and refuses the event; we report it.
* **JTAG, SWD, PS/2, CAN and 1-Wire firmware**, each against an independent
  model of the other end (`instruments.md` §6–§7).
* **Mutation testing re-run** on the final RTL, with a baseline run so a
  failing test can no longer fake a kill (`verification.md` §8).
* **Netlist equals RTL**, unbounded proofs and the AI write-up are in
  progress (`plan.md` §3.3–§3.5).

Not taken: earliest-deadline-first or other dynamic scheduling (it breaks
the fixed timing that is the point of msfcore), 8x4 designs, latch
memories, a RISC-V host. Still open from this survey: 25xx SPI flash
emulation, power per workload, an execution digest, a results page.
10BASE-T receive is out of reach at the clocks we can sign off: the
Manchester receiver needs about 80 MHz.

## Update of 7 Nov 2026

GitHub search now finds about 150 entries: 70 are still the template or close
to it, 83 have at least 5 KB of RTL, 50 pushed in the last week, and about 15
show real depth. Private repositories can't be seen (at least one entry says
it stays private until submission), so this is a lower bound. Claims are as
the entrants state them (read 7 Nov). We checked the tile count in each
`info.yaml`, the CI status of the GDS runs that MarcosAsh and Escapement cite,
and the flow facts below; the rest is not checked by us.

| Entry | Tiles; clock at the slow corner | Firmware | Hardware run | What stands out |
|---|---|---|---|---|
| [MarcosAsh](https://github.com/MarcosAsh/protocol-emulator) | 8x4; 48 MHz (+4.03 ns) | 68 | ECP5 at 48 MHz: 21 demos, USB keyboard included | the chip walks each program against a certificate and refuses to start one it can't check; the checking kernel is proved against the RTL; a missed deadline takes the engine off its pins (proved); USB full-speed and 10BASE-T receive; protocols drawn by a public random beacon, and eight sealed by hash in advance; sigrok as an outside decoder; margin-sweep tooling for real parts on a bench; 127 of 128 mutants |
| [Escapement](https://github.com/parthDOOM/escapement) | 6x4; 50 MHz | 49 | none: bitstreams built, not run | "never late" proved for every program, for all time; "record it, speak it" learns a transmitter's timing from a recording and generates certified firmware; VGA Pong; a real Linux kernel enumerates the simulated chip over USB/IP; 653 of 710 mutants killed in the main campaign, none surviving |
| [Wirelingo](https://github.com/josusanmartin/wirelingo) | 6x4; 50 MHz (+0.01 ns) | 68 | KV260: USB low and full speed with Linux; ARP, ping and UDP echo with Linux's stack | 10BASE-T receive within 802.3's jitter at 50 MHz; 21 programs added with the chip frozen; a blind test; a verifier agent's reports kept verbatim (27 pass, 24 fail); a Hardcaml port proved equal to the RTL; Lean proofs |
| [openPIO](https://github.com/theotacconi/openPIO) | 8x4; 42 MHz | 24 protocols | Tang Nano bring-up next | from a recording it finds the line code, stuffing and CRC polynomial and writes a receiver; MIL-STD-1553B, USB Power Delivery, MFM |
| [Loom](https://github.com/thomasgilbert481/tt_um_loom) | 8x4; 43 MHz | 26 | none | thread isolation proved for each of four threads; 911 mutants, 99.9 % killed; RTL and model written from one contract by sessions that couldn't read each other's code; a public bug ledger |
| [mrnathanhumphrey](https://github.com/mrnathanhumphrey-droid/protocol-emulator-asic) | 6x4; 50 MHz (+2.68 ns) | UART, SPI, I2C, 1-Wire | Artix-7 with a real FRAM and DS18B20; CI re-checks the recorded witnesses | response-time analysis cross-checked with pyRTA and timed-automata tools; Lean proofs |
| [ProEm](https://github.com/Vertrex-star/ProEm-Open-Source-Protocol-Emulator) | 6x4; 62 MHz (+3.84 ns) | frame programs for 25+ standards | none stated | a frame engine rather than a CPU; 10BASE-T receive |
| [Tarang](https://github.com/prasadp4009/tt_um_pemu) | 6x4; 43.8 MHz | about 10 | Zybo: UART, SPI, I2C, 1-Wire, USB | an on-chip trace block the core has no path into |
| msfcore (ours) | 6x4; 33 MHz (+1.24 ns) | 22 | AUP-ZU3: 67 C tests stepped; UART, SPI and I2C running free at 12 MHz against a real MCU, 148 of 148 passes | one core makes the bit-rate decisions and runs the whole job; bus monitors that report timing faults with their lengths; exact timing-fault injection; debug view; ROM boot over UART with no host at run time |

PRISM and Abagel-coder have not pushed since mid-October.

What changed in the field:

* **8x4 may now be allowed.** Twelve entries set 8x4 in `info.yaml`. On 7 Nov
  the competition page still says 6x4, but Sarkar22's and openPIO's logs of
  6 Nov say Jane Street confirmed 8x4 for the March shuttle. We have not seen
  that message, so "those designs don't comply" (18 Oct above) may be out of
  date.
* **The shuttle has its own flow.** Tiny Tapeout's template `main` now uses
  `tt-gds-action@ttihp27a`: LibreLane 3.1.0.dev4, IHP-Open-PDK `89d42ee6`,
  `tt-support-tools` `main`. We harden on `@ihp-cmos5l`: LibreLane 3.1.0.dev3,
  PDK `2bbec75`. The competition page still links the template's `cmos5l`
  branch, which uses the older tag. LibreLane dev4 changes the default
  `SYNTH_STRATEGY` from `AREA 0` to `DELAY 4`, and our `config.json` doesn't
  pin it; MarcosAsh measured 19 % more cell area from that change and their
  6x4 stopped routing.
* **"New protocols after fabrication" is now shown, not asserted.** MarcosAsh,
  Wirelingo, Loom and openPIO add protocols with the hardware frozen, some
  drawn at random or sealed in advance. We have no such exercise.
* **Outside oracles are common:** sigrok's decoders (MarcosAsh, Escapement,
  Wirelingo, mrnathanhumphrey, ProEm) and Linux's USB and network stacks
  (Wirelingo, Escapement). Our peers are models we wrote, plus one real MCU.
* **Learning a protocol from a recording** has appeared twice (Escapement,
  openPIO).
* **10BASE-T receive at 40 to 50 MHz** is shown by Wirelingo and MarcosAsh,
  with dedicated receive hardware. Our 80 MHz figure is a limit of our
  receiver, not of the protocol.

Where we stand: strong, but not leading. MarcosAsh, Escapement and Wirelingo
are ahead on timing guarantees, firmware breadth, clock and the high-speed
protocols. We are ahead of Escapement and Loom on hardware, since they have run
nothing on a board; of Tarang on slow-corner sign-off, since we meet timing at
our rated clock in every corner. The instrument story, which is what the brief
says the chip is for, is our best claim to novelty, but it is not ours alone.
MarcosAsh has tooling that sweeps real parts (SK6812, EEPROM, DS18B20) past
their datasheet limits on a bench with a logic analyser, though we found no
published results from it, and firmware that raises an interrupt when an edge
leaves its expected rows. Escapement has capture, replay and `EXPECT` windows,
and Wirelingo an I2C sniffer. What we did not find in their documents is what
ours does: monitors for UART, I2C and SPI that run on the chip and report each
timing fault with its length. FIPE has the idea but no formal proofs or board
run, and has not pushed since 22 Oct.

## Update of 18 Nov 2026

Written for: the team, reading the repository. GitHub search for "protocol
emulator" and "jane street asic" finds about 70 repositories with real names;
about 50 were pushed after 7 Nov and about 10 today. The claims are as each
entrant states them in its README or `info.yaml` (read 18 Nov), not checked by
us. Only the tile count and clock are read from `info.yaml`.

| Entry | Tiles; clock in `info.yaml` | Pushed | What changed since 7 Nov, or what stands out |
|---|---|---|---|
| [MarcosAsh](https://github.com/MarcosAsh/protocol-emulator) | 8x4; 48 MHz | 18 Nov | Unchanged claims: a program that cannot be certified does not start; 127 of 128 mutants; ECP5 demos |
| [Escapement](https://github.com/parthDOOM/escapement) | 8x4 (was 6x4); 25 MHz (was 50) | 18 Nov | Retiled to 8x4; description now says 71 programs; "never late" proved for all programs |
| [Loom](https://github.com/thomasgilbert481/tt_um_loom) | 8x4; 50 MHz | 18 Nov | 26 programs, 13 of them written after the hardware was frozen for protocols it was not designed for (NEC IR, 1-Wire, LIN, HDLC, Wiegand, DMX512, DShot): the "new protocols after fabrication" point, shown |
| [openPIO](https://github.com/theotacconi/openPIO) | 8x4; 50 MHz (was 42) | 18 Nov | 24 protocols, each tested on its pins against a reference device written from the spec; a logic analyser; Tang Nano bring-up next |
| [Wirelingo](https://github.com/josusanmartin/wirelingo) | 8x4 (was 6x4); 48 MHz | 17 Nov | Checked on 16 Nov at STA, gate level, precheck and formal equivalence; "FPGA is not silicon" |
| [ProEm](https://github.com/Vertrex-star/ProEm-Open-Source-Protocol-Emulator) | 6x4; 50 MHz | 18 Nov | Frame engine for an RP2040 host, ISA v0.7.1, design reference PDF |
| [pivotvlsi](https://github.com/pivotvlsi/jane_street_asic_protocol_emulator) | 8x4; 50 MHz in `info.yaml`, 100 MHz in the README | 18 Nov | Four 8-bit accumulator cores; the README and `info.yaml` disagree on the clock |
| [RISC-M/COMMS-M](https://github.com/RISC-M/COMMS-M) | 8x4; 50 MHz | 18 Nov | New. Dual core, transport-triggered architecture |
| [MikeKovetsky/protoemu](https://github.com/MikeKovetsky/protoemu) | 8x4; 60 MHz | 17 Nov | New. Built by an AI agent in a scored loop |
| [Sarkar22/pe-asic](https://github.com/Sarkar22/pe-asic) | 8x4; 50 MHz | 17 Nov | New in this table; logs cited 6 Nov for the 8x4 confirmation |
| [Kanishk234 (TRIPWIRE)](https://github.com/Kanishk234/protocol-emulator-asic) | 8x4; 50 MHz | 18 Nov | Not re-read; was a placeholder on 7 Nov |
| msfcore (ours) | 8x4; 48 MHz target, signed off at 33 MHz (30 ns) | 17 Nov | See below |

Our `info.yaml` says 8x4 (`tiles`, line 11), with the comment "8x4 is the
shuttle's final size (Jane Street, 11 Nov 2026)". Its clock line (line 8) gives
48 MHz and calls it a target, not signed off.

What changed in the field:

* **8x4 is now the common size.** Eight of the strongest entries declare it.
  Area is not a judging criterion, so 8x4 no longer sets us apart.
* **Formal and proof claims have spread.** MarcosAsh (kernel proved against the
  RTL, netlist equal to RTL), Escapement (unbounded "never late" and gate-level
  equivalence), Loom (per-thread isolation proved, 911 mutants), Wirelingo (Lean
  proofs and a Hardcaml port proved equal). Ours are bounded (depth 16) on v2.
* **"New protocols after fabrication" is shown.** Loom and openPIO both show it;
  we have not.
* **Clocks are higher.** Rivals claim 42 to 60 MHz. We sign off at 33 MHz
  (30 ns). The 15 Nov layout has +8.90 ns of setup slack at the slow corner,
  so its worst path is about 21 ns (about 47 MHz), but no run has been
  constrained tighter than 30 ns. The "50 MHz sign-off" of 21 Oct (branch
  `rtl-changes`, never merged) was withdrawn on 23 Oct: its slow corner had
  been timed with the typical SRAM library (`ai.md`).
* **10BASE-T receive** is in MarcosAsh and Wirelingo, and in Escapement's
  description. We have transmit only.

Where we stand: still strong, not leading, and the gap has grown on proofs
and clock. The claim that is still ours is the instrument story:
on-chip monitors that report every timing fault with its length, and exact
timing-fault injection. Escapement's capture and `EXPECT` windows and Wirelingo's
I2C sniffer come close, and MarcosAsh's margin sweep is a bench tool we don't
have. Protocol breadth is level with the leaders (79 programs in the README
against 68 to 71). Two corrections to our own tables: the 7 Nov row for
msfcore's firmware says 22 programs, and the repository has 79; and the 18 Oct
text that says 8x4 "doesn't comply" is out of date.

Our CI, read on 18 Nov:

* **The GDS run of 15 Nov failed** at its Magic DRC step (416,536 errors, all
  inside SRAM footprints; `sram-power-integration.md` §7). It also hit the
  artifact quota. The shuttle's own KLayout deck passes with 0 items, so the
  sign-off is not in doubt, but CI is red, and the precheck, gate-level and
  viewer jobs did not run in CI.
* **The newest v4 push (`8ffa5a2`, 17 Nov 07:02) failed `formal`, `docs` and
  `test` in about 4 seconds with no steps run.** The job log is not available
  through the API, so the cause is not known. Check the workflow file and the
  account's Actions settings before anything else.
* **README and commit text overstate this.** The README's evidence row says the
  CI GDS run gives "same results" as the local hardening, and `405f022` says the
  CI GDS run "matches the local hardening". CI did not confirm either.
* **Formal on v4:** BMC depth 16 was green at `e92524b` (12 Nov). The `ni`
  target and the unbounded proof have not been run on v4.
* **Board:** v4 ran on the AUP-ZU3 on 17 Nov at 12 MHz (bitstream built
  03:13; the HIL digest `boot_parallel_go 6F06A9E1 over 5366 steps` is v4's).
  The v3 board test against the LP-MSPM0G3519 passed 304 of 304, guard checks
  included. The HIL suite passed 60 of 67; the 7 failures are the `cap_*`
  tests reading CNT while v4's two-flop reset release still holds the core,
  a harness fault fixed in `8ffa5a2` and waiting for a rebuild of the `hil`
  app. The README's "v4 is not built on the board" is out of date.

Recommended next, ranked by what a judge sees first:

1. Find out why the 17 Nov runs failed in 4 seconds, and make CI green. Turn off
   `RUN_MAGIC_DRC` in the GDS config (the shuttle's precheck already does) so the
   GDS job reaches the checks that matter.
2. Correct the README evidence row and the `405f022` claim until a CI GDS run is
   green.
3. Show new protocols after fabrication: write three programs for protocols not
   on our list (NEC IR, DShot, Wiegand or DMX512) with no RTL change, and record
   them in `instruments.md` the way Loom does.
4. Run the unbounded `ctrl` proof and the `ni` target on v4, so the proof claim
   is not behind the others.
5. Choose the clock. The 15 Nov layout's slack points at about 47 MHz, so a
   run at 21 ns (or the plan's 18 ns place-and-route, 20 ns sign-off) is the
   one that settles 48 or 50 MHz. Until one passes, `info.yaml` should give
   the signed-off rate. The judges read `info.yaml`.
