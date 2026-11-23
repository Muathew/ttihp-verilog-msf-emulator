![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/formal/badge.svg) ![](../../workflows/fpga/badge.svg)

# msfcore: a protocol-emulator ASIC where an edge leaves on its cycle, or not at all

A 16-bit CPU for pins and time, for IHP SG13CMOS5L on Tiny Tapeout (8x4 tiles, 24 digital pins).
Protocols are firmware, loaded over UART or a parallel port after fabrication. Entry for the Jane
Street protocol emulator ASIC competition. **This branch is v4: signed off on 8x4 at 50 MHz (20 ns)
in every corner, its netlist run with the sign-off delays at that clock, formally checked to depth
16 in CI with the timed pins proved for all time, and run on an FPGA board at 12 MHz. Not yet: six
of the controller's assertions (F3's timeouts, F8) are proved to depth 16 only** ([what is shown
on which version](#evidence)).

One chip plays the device, drives the bus, watches it and breaks it on purpose: an SPI flash or I2C
EEPROM, a JTAG or SWD probe, a monitor that reports every timing fault with its length, a margin
finder, a pin finder for a board nobody documented. 79 firmware programs, from UART to CAN, USB
low speed and 10BASE-T ([the list](docs/repository.md#firmware-by-role-firmware)).

```
  ui[8]     uio[8]     uo[8]      pins: 2-flop synchronisers in, registered pads out
    |         |          ^
+------------------------------------------------------------------------------+
| 4 threads in a fixed rotation; every instruction's cost is fixed by the ISA  |
| per thread: a timed-pin slot (AT, TS): a write on its cycle, or refused      |
+--------------------------+--------------------------+------------------------+
| engine set 0             | engine set 1             | capture engine, glitch |
| TX | RX | CRC            | TX | RX | CRC            | filters, FIFO between  |
|                          | (an external clock too)  | the threads            |
+--------------------------+--------------------------+------------------------+
| 2,048 x 16 SRAM (four macros), 128-word boot ROM, UART/parallel loader       |
+------------------------------------------------------------------------------+
```

## What is different ([architecture](docs/architecture.md))

1. **An edge on its cycle, or not at all.** `AT rb` arms a thread's slot; its next pin write waits
   there and is applied when the cycle counter reaches the time, whatever the four threads are doing.
   A write that would be late is refused with a flag, never made late. Measured: a reply exactly
   delta cycles after an input edge, jitter 0, with 1 to 4 threads running (`test_timed_pins`).
2. **A program that compiles is on time.** Instruction costs are part of the ISA, so a static checker
   certifies every deadline before the program runs, and `msfc`, a small language with time in its
   syntax, refuses a program it cannot certify and names the source line ([msfc](docs/msfc.md)).
3. **Evidence we did not write alone.** An ISA model written from the specification is compared with
   the RTL on every issue of random programs; formal properties are checked against planted bugs
   that must fail them; six protocols were drawn by a public random round and
   judged by sigrok's decoders; [every bug we found on the way is listed](docs/v4-bugs.md).

Two engine sets (both directions of a link, or two protocols, at once), engines that follow the other
side's clock (a fast SPI target), HDLC/CAN stuffing, frame/stuff/underrun flags and 10BASE-T
receive with 802.3's full 13.5 ns of edge jitter (fine mode: the pin sampled on both clock edges)
are v4's other additions ([envelope](docs/envelope.md): how fast each class of protocol goes, from
tests).

## Evidence

| Claim | Evidence | Shown on |
|---|---|---|
| The RTL does what [`isa.md`](docs/isa.md) says | the model against the RTL on every issue of random programs, timed windows and both engine sets included | v4 RTL, simulation, 11 Nov |
| The protocols work | 330 cocotb tests in 79 modules, peers written from the standards: all pass (20 Nov, the signed-off RTL; three failed in the 11 Nov run and were fixed: [v4-bugs](docs/v4-bugs.md)) | v4 RTL, simulation |
| An edge leaves on its cycle | `test/test_timed_pins.py`: jitter 0 with 1 to 4 threads, one stalled, one loading; F17, the timed-pin rule, proved for all time for every program | v4 RTL, simulation; v4 RTL, formal (CI, every push) |
| Deadlines are met | `tools/msf_timing.py --all`; CI also compares it with the RTL | v4 firmware, the checker |
| Formal properties hold | the formal properties in CI ([verification](docs/verification.md)) | v4 (12 Nov, CI): bounded check to depth 16 passes on the controller, the capture engine and the top level, 18 of 18 planted bugs caught; F16's planted bug shown on v4 (13 Nov). For all time (controller): 68 of 86 assertions by PDR (18 Nov, CI); F17's timed pins by k-induction with helper invariants (23 Nov, CI, every push); F3's timeouts (5) and F8 (1) hold to depth 16 only |
| The layout meets timing | CI sign-off job | v2.2 on 6x4: 30 ns in every corner. v4 on 8x4 with fine mode (20 Nov, CI 44698976647): **20 ns (50 MHz) in every corner**, setup slack +2.27 ns at the slow corner (1.08 V, 125 °C), hold +0.29 ns; routing DRC, antenna and LVS clean; TT precheck, the gate-level tests (zero delay) and netlist = RTL pass; the gate-level tests also pass with that run's SDF at 20 ns, slow and fast corners, and with random SRAM contents at power-up (CI 44821883241, `gl_sdf.yaml`). First hardened on 15 Nov at 30 ns ([SRAM power fix](docs/sram-power-integration.md)) |
| It runs on real hardware | AUP-ZU3 FPGA: 67 of 67 C protocol tests; free-running against an LP-MSPM0G3519: UART, SPI, I2C, 148 of 148 passes at 12 MHz | v2.2 core. v4 (17 Nov, 12 MHz): 304 of 304 passes in the board test against the LP-MSPM0G3519; 60 of 67 C tests, the 7 failures a test-harness fault, fixed and waiting for a rebuild ([open problems](docs/v4-open-problems.md) B) |

## Limits

* v4 signs off at 20 ns (50 MHz), which covers the 48 MHz in `info.yaml` (USB full speed at 4
  cycles a bit). The gate-level runs with delays are simulation (Icarus runs no setup or hold
  checks; STA signs those off); no board has run above 12 MHz ([open problems](docs/v4-open-problems.md) B).
* Pad speed is unknown: there is no CMOS5L silicon. [`envelope.md`](docs/envelope.md) says which
  limits are logical (one bit a cycle) and which depend on the pads.
* The board's C tests step the core one clock at a time; only the MCU test runs it free, at 12 MHz.
* Firmware is assembly, or `msfc` (one thread, 16-bit values, no functions).
* Planned and not built: the fractional divider, the ROM's read-back and memory test
  ([plan v4](docs/plan-v4.md) has the status of every item). The demo-board driver (`host/`) is
  a first cut: it runs against the RTL in simulation, not yet on a board.
* Programs are at most 2,048 words, data included. Not a RISC-V core, by decision.

## Try it

```sh
python tools/msfc.py firmware/msfc/spi_mode0.msf --run 700   # compile, certify, run on the ISA model
python tools/msf_timing.py firmware/uart_tx.s                 # certify every deadline of a program
python tools/msf_timing.py firmware/uart_tx.s -D BIT=9        # too fast for its own loop: refused
cd test && python run_tests.py -m test_timed_pins             # the timed pins on the RTL (Icarus, cocotb)
```

The playground in `site/` runs the assembler, checker and model in a browser (needs the repository
public). More commands, the layout and every document: [what is where](docs/repository.md). How AI
was used, and where it was wrong: [`docs/ai.md`](docs/ai.md).

## What is Tiny Tapeout?

Tiny Tapeout is an educational project that makes it easier and cheaper than ever to get digital
designs manufactured on a real chip. See https://tinytapeout.com.
