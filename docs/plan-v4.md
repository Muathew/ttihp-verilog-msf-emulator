# Plan v4: win on 8x4

Written 11 Nov 2026, from the `plan7` checkout, after Jane Street's email of the same
week: 8x4 tiles are confirmed and final for the March 2027 CMOS5L shuttle, the template's
`main` now targets CMOS5L, and the competition page has an FAQ. Sam: "make an ambitious
long and detailed plan to get us to win ... do not change anything ... if you are
keeping a similar architecture then review for bugs."

This file is a plan only. Nothing in `src/`, `test/`, `firmware/`, `tools/` or the other
documents was changed to write it. The deadline is Monday 18 January 2027: 68 days.

It replaces nothing: `plan.md` (v2.2, `main` and `plan7`) and `plan-v3.md` (branch `v3`)
stay as the record of what was built. Their open items are folded in here (§2.1).

## Status

Kept current as items land, like the other plans' tables.

| # | Item | Section | Status |
|---|---|---|---|
| 0 | Decisions by Sam and Muath | §16 | taken as recommended on Sam's order of 11 Nov ("follow this plan and implement it fully"); 7, 8, 9, 10 are Muath's or Sam's to act on |
| 1 | Baseline: v3 unchanged, hardened on 8x4 to the end; four-macro floorplan trial | §7.1 | **four-macro trial done** (15 Nov): v4 with four macros on the core's bottom edge hardens on 8x4, locally and on CI (run 43740378700, the same counts): routing DRC 0, antenna and LVS clean, the TT precheck passes locally ([sram-power-integration.md](sram-power-integration.md) §8). The first attempt (13-14 Nov) shorted VPWR to VGND (same document, §2). The die is 1724.16 x 710.64 um. The v3 baseline suite passes (286 of 286, 11 Nov). Not run: v3 unchanged on 8x4 |
| 2 | Step 1: fixes from the review (B1-B11), reset synchroniser, registered pads | §6.2 | **built and tested** (11 Nov, commit 0f11429): B1-B5, B7, B9-B11; 9 new tests (`test_v4_fixes`); B6 is step 5, B8 step 4. Not run: formal F18, hardening (B7, B9 unconfirmed) |
| 3 | Step 2: 2048 words (four macros), 11-bit PC | §6.3 | **built and tested** (11 Nov): RTL, model, assembler, checker, FIFO/capture/debug view, `fpga/rtl`; the capture default stays 512/256 so v3 programs run unchanged (the plan said 1024). The full suite found one program that relied on the old 10-bit wrap (`spi_flash.s`, bug F1 in `v4-bugs.md`); fixed. Floorplan trial not run |
| 4 | Step 3: timed pins (`AT`, `TS`) | §6.4 | **built and tested** (11 Nov): `ESEL`, ports on page 1, refusal, checker, model; 9 tests (a reply with jitter 0 with 1 to 4 threads among them), random timed windows, RTL == model. F17 written, not run. **12 Nov:** F17 passes CI's BMC to depth 16 and catches its 3 mutants; the checker models the one-event slot (`v4-bugs.md` T5); the first shipped program with timed writes, `ow_presence.s` (1-Wire presence responder), has its timed writes cross-checked on the RTL (4 of 4) |
| 5 | Step 4: two engine sets, external clock, general stuffing, half-cycle sampling | §6.5 | **a, b, c (RX and TX), d, e built and tested** (11 Nov): second TX/RX/CRC set (4 tests), engines on `ui`/`uo`, external clock (RX 2 cycles a bit, TX 6; 7 option tests), HDLC/CAN stuffing, frame/stuff/underrun errors, STAT bit 15. **f (half-cycle sampling) built and tested** (19 Nov, branch `halfcycle`): `RX_OPT` bit 6, fine mode. The model study (`test/mode3_model.py`) found half-cycle sampling alone not enough (1 % of frames wrong at 13.5 ns spread at random, 28 % at the limit), so fine mode also locks on 31 centres with falling gains and tracks at 1/32: 0 of 4,000 wrong at 13.5 ns spread at random, 3 of 4,000 at the limit; `test_eth10_rx` at 13.5 ns, and the RTL's phase equal to the model's at every cycle. 3 flops a set for the falling-edge sample, 2 more for the lock count. **Hardened** (20 Nov, CI 44698976647 at 970d4b8): signs off at 20 ns in every corner (item 7), then merged into `v4` and `main`; formal takes the falling-edge sample as a free input. **g (fractional divider) not built** |
| 6 | Step 5: boot ROM: autobaud, read-back, memory test, chip ID | §6.6 | **autobaud and chip ID built and tested** (11 Nov): the ROM times a 0xFF byte (`ui_in[3]` strap), the loader is 115 words (`test_autobaud_loads_at_any_clock`); page 3 reads 0x4D53 ('MS'), 0x0400 (version 4.0), a capability word 0x0BBF (its ROM-memory-test bit is clear) and `CNT_H`. **ROM read-back and the memory test not built** |
| 7 | Sign-off on 8x4 at 20 ns, hold margin, SDF gate-level runs | §7 | **signed off at 20 ns (50 MHz) in every corner, with fine mode** (20 Nov, CI 44698976647 at 970d4b8): setup slack slow +2.27, typical +3.89, fast +4.58 ns (at the typical and fast corners the worst path is a pad into fine mode's falling-edge flop, which has half a cycle); **hold +0.29 ns** at the fast corner; one max-slew warning, an SRAM address pin 0.7 ps over at the slow corner; routing DRC, antenna and LVS clean; precheck, `gl_test` and netlist = RTL pass. Before fine mode: **signed off at 20 ns (50 MHz) in every corner** (19 Nov, CI 44499734618 at dfa4c86, merged into `v4` and `main`): setup slack slow +3.36, typical +9.69, fast +12.84 ns; **hold +0.26 ns** at the fast corner, so B9's 0.2 ns holds; routing DRC, antenna and LVS clean; precheck, `gl_test` and netlist = RTL pass. The run with the calibration alone (CI 44499740319) also passed, slow +1.45 ns. How it got there: 15 Nov: hardened with place and route at 20 ns and sign-off at 30 ns, every corner clean (slow +8.90 ns); the 5 gate-level tests pass on that netlist, without delays (run locally). 18 Nov, 50 MHz (sign-off 20 ns, place and route 18 ns): CI 44351742340 missed at the slow corner by 1.40 ns on 16 paths through the boot ROM's lookup, so the ROM now registers its address (784a270); its run, CI 44379389831, missed by 0.02 ns on one path (`u_ctrl.cur` to serdes 1's receive register; typical +7.35, fast +11.74). **Hold: +0.31 ns at the fast corner**, so B9's 0.2 ns holds. The near-miss came from the wire model: every estimate before extraction used half the extracted capacitance (`src/config.json`, `LAYERS_RC`); calibrated in dfa4c86, with timing-driven placement and repair after global routing. **SDF runs done** (19 Nov, locally, `test/run_gl_sdf.py` on CI 44499734618's netlist and SDF, at 20 ns; 20 Nov, on the fine-mode layout's, in CI: `gl_sdf.yaml`, run 44821883241, the same three pass): the 5 gate-level tests pass at the slow and fast corners and at the slow corner with random SRAM contents |
| 8 | Verification of v4: model, formal, mutation, cost proof | §8 | **partly done** (11 Nov): the model is compared with the RTL on every issue of random programs with timed windows and both engine sets; the final-tree run is 319 cocotb tests in 77 modules, of which 316 passed; the other 3 (two flash-target tests, one engine-sets test) failed in it, were fixed (`v4-bugs.md` F1, I4) and pass in reruns of their modules. **12 Nov:** CI's first formal run failed on F6 (a wrong property, `v4-bugs.md` V1; fixed); new properties F19 (engine binding), F20 (`TS`), F21 (the cost table), each with mutants caught by its own property; the `ni` miter wired for v4's state (V3); formal now runs on this PC (native OSS CAD Suite). **CI formal green at e92524b** (run 43275921661): BMC depth 16 PASS on `ctrl`, `cap` and the top level, 18 of 18 covers reached, 18 of 18 mutants caught (16 by their own property; the F12 and F13 mutants by F1). **18 Nov:** the unbounded proof of the controller (CI 44363125574, an hour): 68 of 86 assertions proved for all time; F17's timed pins (12), F3's timeouts (5) and F8 (1) stay bounded at depth 16, with no counterexample (the job is marked failed when the hour runs out). **Not done:** the unmutated `ni` target (F16) to depth 16, the 18 open assertions, the firmware mutation score, the cost proof |
| 9 | FPGA build and board run of v4 (Muath) | §11 | **run on the board** (17 Nov): v4 on the AUP-ZU3 at 12 MHz (the HIL digest `boot_parallel_go 6F06A9E1 over 5366 steps` is v4's). Board test against the LP-MSPM0G3519: 304 of 304 passes. HIL: 60 of 67; the 7 `cap_*` failures are a harness fault (it read `CNT` while v4's two-flop reset release still held the core), fixed in 8ffa5a2 and waiting for a rebuild of the `hil` app. Open: the MCU build's stream check is wider than the repository's (`v4-open-problems.md` B). Not done: a board run above 12 MHz |
| 10 | Tag the netlist; flexibility campaign after the tag | §9 | not started (needs a hardened netlist) |
| 11 | `msfc`, the demo-board driver, the bring-up kit | §10 | **`msfc` built** (11 Nov): `tools/msfc.py`, `docs/msfc.md`, 3 programs, 7 tests (120 random programs against its own interpreter; edges at the pins; refusals with source lines); one thread, 16-bit values. **19 Nov: the demo-board driver, first cut** (`host/msf_host.py`, `host/tt_board.py`): reset with the boot-mode pins, both loaders, single steps, run to a PC, registers through the debug view; the same code runs against the RTL in `test_host_driver` (5 of 5). Not run on a board or under MicroPython. **Not built:** the driver's fault cases and faster loading on the board, the bring-up kit, inlined functions and threads in `msfc`, ten programs rewritten (three are) |
| 12 | Bench session, photos, video | §11 | not started (needs people, a board and the MCU) |
| 13 | Write-up | §12 | **drafted** (11 Nov): one-screen `README.md`, `docs/architecture.md` (for someone who knows PIO and PRU), `docs/envelope.md` (what each class of protocol can do, from tests), `docs/msfc.md`, `docs/ai.md` updated, `docs/v4-bugs.md` and `docs/v4-open-problems.md` for review. **18 Nov:** the README states v4's evidence, version by version, before the repository went public. **Not done:** the cold read, the playground's v4 examples (and the playground is not yet published), the `instruments.md` one-page summary, the second draw and the newcomers |
| 14 | Final GDS on the final commit, repository public, submission form | §13 | **repository public** (18 Nov), and CI runs again. Not started: the final GDS, the submission form |

**Progress note, 11 Nov (end of the first working session on this plan).** Items 2-6, the
model and tests of item 8, `msfc` and the write-up's documents are built in one working tree
and committed on branch `v4` (code and tests in one commit, documents in another: the steps
were developed together and share files, so they are not separable). Everything that needs a
solver, a flow, a board or a person is listed in [`v4-open-problems.md`](v4-open-problems.md);
every bug found on the way, fixed or not, with file references, is in
[`v4-bugs.md`](v4-bugs.md), written for a larger model to review.

**Progress note, 19 Nov.** Since the 12 Nov table: v4 hardens on 8x4 (15 Nov, 30 ns), runs on
the board at 12 MHz (17 Nov), and has 68 of its controller's 86 assertions proved for all time
(18 Nov). The repository went public on 18 Nov, which got CI running again. What stands between
us and the tag on 20 Dec, in order: ~~a 50 MHz sign-off~~ (item 7: signed off later on 19 Nov,
CI 44499734618), ~~then the SDF and random-SRAM gate-level runs on that netlist~~ (passed the same evening); the `hil` rebuild for 67 of 67 and a board run
above 12 MHz (Muath); the demo-board driver on a real board (item 11; a first cut runs against the RTL). The README's
gds badge reads `main`, whose last GDS run (3 Nov) failed; 50 MHz is merged into `main` and a
GDS run on it was started the same day (passed, 20 Nov: CI 44603982945).

**Progress note, 20 Nov.** Fine mode (item 5f, 10BASE-T receive at 802.3's 13.5 ns of edge
jitter) signs off at 20 ns in every corner (CI 44698976647) and is merged into `v4` and `main`.

## 0. The plan on one page

**The sentence we want a judge to repeat:** msfcore is the brief's chip, a tiny CPU for
pins and time, and it is also the instrument you would test a protocol with: it plays the
device, drives the bus, watches it and breaks it, and an edge leaves the chip on its
cycle or not at all.

**Five moves, in this order:**

1. **Fix what the review found, move v3 to 8x4, close 50 MHz.** The review (§5) found one
   real mismatch between v3 and its own specification (confirmed in simulation) and five
   places where the layout could behave differently from the simulation. None needs a
   new architecture.
2. **Spend the new tiles on pins and time, not on a second CPU.** Three additions, each
   chosen because it opens a *class* of protocols or closes a claim we had to weaken:
   timed pins (an edge on its cycle whatever the threads are doing), two full engine
   sets with an external-clock mode (fast synchronous slaves, both directions of a link
   at once, no shared engine between threads), and 2,048 words of memory (a whole
   Ethernet frame, deep captures).
3. **Show flexibility with evidence we did not make ourselves.** Tag the netlist, then:
   a second public draw, protocols written by people who have never seen the chip, and
   a table of what each class of protocol can and cannot do, measured by a sweep.
4. **Answer the C toolchain.** A small timing-first language (`msfc`) in which a program
   that compiles is on time, and a driver for the demo board's RP2350 so the judges'
   chip boots with one command.
5. **Evidence on real hardware, and a write-up that can be read in ten minutes.** The FAQ
   says the write-up is weighed heavily; ours is honest but long.

**If it goes wrong:** every step is shippable on its own, and there is a ladder (§14):
v4 complete, v4 without the last steps, v3 on 8x4 with the fixes, and at the bottom
v2.2 as it is on `plan7` today, which already passes the shuttle's flow.

**What I am not recommending:** a RISC-V core on the chip (§4), a second pipeline, an
on-chip check of each program's timing certificate, a host port in hardware, a PLL (§6.7).

## 1. What changed this week

From the email:

* 8x4 tiles are confirmed for the March 2027 CMOS5L shuttle, and Tiny Tapeout says the
  tile sizes are final. `info.yaml` takes `tiles: "8x4"`.
* The template's `main` branch targets CMOS5L; the `cmos5l` branch is no longer needed.
* The deadline is unchanged: 18 January 2027.
* We received it, so one of us is on the sign-up list. That closes the "sign-up not
  checked" item of `plan.md` §7.0 (an inference from the email's arrival, not a check of
  the form).

From the FAQ (read 11 Nov through a page summariser, so re-read the page itself before
quoting any of this in the write-up):

* **Judging.** "There's no fixed weighting." "Flexibility is the main thing": whether
  the chip can be reprogrammed for protocols it was not designed for. Then the protocols
  demonstrated, unique capabilities the architecture enables, and the design and
  verification approach. "We'll also weigh your write-up heavily." "A design with fewer
  protocols but a strong architecture and verification approach can definitely be
  competitive."
* **Not criteria:** clock frequency ("there's no target") and area (8x4 is a maximum).
* **It need not be a CPU.** Entrants are pointed at the RP2040's PIO and TI's PRU and
  asked what they would do differently. The more protocol work on the chip (line coding,
  CRC, clock recovery), the better.
* **Clock and pins.** The demo board's RP2350 makes the clock; 60 MHz is confirmed
  acceptable. I/O is estimated at about 10 ns round trip through pads and mux and about
  66 MHz at an output, not measured: there is no CMOS5L silicon yet. 24 digital pins, no
  analog pads; 3.3 V on the board; external parts go on a Pmod.
* **Memory.** More than one IHP SRAM macro is allowed if it fits and passes precheck.
* **Reset and loading.** Flops do not start at 0, so use `rst_n`. A design loses its
  state when deselected, so programs are reloaded. Loading is part of the design; the
  demo board's microcontroller is "the natural host".
* **Sign-off.** A clean Tiny Tapeout flow and precheck. "We strongly recommend
  gate-level simulation to catch design (and occasionally tool) errors." Custom flows
  are discouraged; a local run is allowed only past GitHub's six-hour limit.
* **Submission.** A link to a public repository with RTL, tests and a passing
  GDS/precheck, the architecture and instruction-set documents, a toolchain with
  examples, a verification write-up. The repository may stay private until submission.
  A video is optional. AI use must be described.
* **Prize.** About four designs are taped out; small non-functional fixes such as
  re-hardening are allowed after the deadline.

## 2. Where we stand on 11 Nov

### 2.1 Our three versions

| | `main` (v2.2) | `plan7` | `v3` (worktree `msf-v3`) |
|---|---|---|---|
| Last commit | 8011d2c, 6 Nov | 06da447, 8 Nov | 6113268, 9 Nov |
| Netlist | signed off | the same netlist as `main` (only `ifdef FORMAL` text added) | new: decode stage, late guard, RX mode 3 |
| Tiles | 6x4 | 6x4 | 6x4 |
| Clock, slow corner | 33 MHz | 33 MHz: CI 42393871451 on the shuttle's flow, +3.62 ns at 30 ns, hold +0.08 ns, 17,245 cells, 46.5 % | not known: the 18 ns run was stopped in detailed routing; +5.56 ns at the typical corner before routing |
| Programs | 22 | 39 `.s` files in `firmware/` | 79 `.s` files (78 programs by `plan-v3.md`'s count) |
| Tests | 237 | 237 and the tool tests | 257 of 284 run, none failed; `test_mcu_v3_peer` never run to the end |
| Board | 67 C tests stepped; UART, SPI, I2C free-running at 12 MHz against the MSPM0, 148 of 148 | the same evidence applies | written, not run: needs Muath's Vivado build |
| Formal | F1-F12 | and F13 | F1-F15; F16 (two copies) not proved and its planted bug not yet shown |
| Open | bench session (7.11), repository private | not merged into `main` | harden (item 2), sign-off (7), board (8) |

Folded into this plan: `plan.md` 7.11 (bench) and 7.0 (public repository); `plan-v3.md`
items 2, 7 and 8, which become §7 and §11 here, on 8x4 and on the v4 netlist.

### 2.2 Position, plainly

About four designs are taped out. What can win is being clearly the best at something
the brief asks for, with no gap anywhere that lets a judge put us aside. That is what
§0's five moves are for:

* gaps that get an entry put aside: v3 not signed off; nothing above 12 MHz on a board;
  no demo-board driver; assembly only; a write-up nobody can read in ten minutes;
* where we can be best: the chip as an instrument, on real hardware, with timing the
  hardware itself keeps, explained honestly.

## 3. What the judges reward, what we show, what is missing

| Criterion (the FAQ's words) | What we have | What is missing | Here |
|---|---|---|---|
| Flexibility: "reprogrammed for protocols it wasn't designed for" (the main thing) | 79 programs on v3; six drawn at random on an unchanged chip; a learner | nobody outside the project has written a protocol; assembly only; a synchronous slave tops out at clk/16; one engine pair for four threads; a 1.3 ms time horizon at 50 MHz | §6.4, §6.5, §9, §10 |
| Protocols demonstrated | USB low speed, 10BASE-T transmit, CAN, JTAG, SWD and the rest in simulation with outside decoders; USB full speed and 10BASE-T receive on v3 | on a board only UART, SPI, I2C; 10BASE-T receive holds 10 ns of jitter, 802.3 asks 13.5 | §6.5, §11 |
| Unique capabilities the architecture enables | the instrument firmware | no hardware feature of our own yet | §6.4, §6.5 |
| Design and verification approach | independent model, random differential tests, formal with planted bugs, mutation, netlist = RTL, deadline checker, claims attacked | F16 unproved; four properties bounded; 40 mutants; the checker's cost table is tested, not proved; no gate-level run with delays | §8 |
| Write-up (weighed heavily; readability, new techniques explained) | honest and complete | long; no short path; never read cold by an outsider | §12 |
| Sign-off | `plan7` passes the shuttle's flow | v3 and v4 do not exist as layouts; hold +0.08 ns | §7 |

## 4. The extra area and a RISC-V core

**What 8x4 changes.** At 6x4, a 32-bit core with its memory and a timing engine barely
fitted, so a small custom core was the natural design and ours used half its tiles.
At 8x4 (about 1.225 mm² against 0.916 mm², a third more) area stops being a reason: a
RISC-V core with engines and memory fits. Such a core brings C, a known instruction
set and ready-made verification of the core. Area is not a judging criterion, so a
design that leaves a third of the die empty earns nothing for it.

**What a RISC-V core does not get from its ISA.** A standard core's timing is a
property of its pipeline and its memory, not of the instruction set, so exact pin
timing has to come from engines beside it. That is two machines to
document, verify and program, and the brief's "tiny CPU with an instruction set
designed for reading pins, writing pins, counting cycles, and hitting timing precisely"
describes ours, not that split.

**Decision proposed: no RISC-V core on the chip.** Reasons:

1. It would be a second instruction set to verify and document in 68 days, in a design
   whose claim is that one core does the whole job.
2. On-chip memory cannot hold useful C: 2,048 words of 16 bits is about 1,000 RV32
   instructions, less than one USB stack.
3. The host already exists. The FAQ calls the demo board's RP2350 "the natural host".
   Upper layers belong there, in MicroPython or C, with our chip keeping the timing.
4. It adds nothing a judge would call novel.

**What we do instead, so the extra area and the C argument both have an answer:**

* spend the tiles on pin timing hardware and memory (§6), where a general-purpose core
  cannot follow;
* `msfc` (§10.1): a small language whose compiler places every deadline and refuses a
  program it cannot certify. C on a RISC-V cannot promise that;
* a host library for the RP2350 (§10.2), so "write the upper layer in Python on the
  host" is a shipped path, not a remark.

## 5. Bug review of the RTL

**Scope.** We keep the architecture, so I read every line of `src/` on branch `v3`
(about 2,300 lines: `ctrl`, `serdes`, `sram`, `cap`, `filt`, `crc`, `pio`, `alu`,
`regfile`, `bootrom`, `project`), compared it with `plan7`'s, and read the boot loader,
`config.json` and the SDC files. One finding was simulated; the others are from reading
and are marked so. Not reviewed: the firmware, the tools (assembler, checker, learner),
the test benches, the formal scripts, the FPGA wrapper and the PDN plug-in.

### 5.1 Findings

| # | Where | What | How found | Applies to | Severity |
|---|---|---|---|---|---|
| B1 | `ctrl`, `assign imem_raddr` (line 524 on `v3`; [line 471 here](../src/tt_um_msf_ctrl.v#L471) is v2.2's) | v3 fetches a cycle earlier (with `pcs_n`). A store to the instruction two ahead now reads the memory on the same edge it is written: the **old** word runs. `isa.md` v3 still says a write "≥ 2 instructions ahead" is allowed and that v2.2 programs run unchanged. By the same mechanism, with two threads running, a thread can no longer change its own next instruction (not simulated). On silicon it is a read and a write of one address on the two ports in one cycle, which our macro model does not define. | **simulated**: `ST` to W+2 on both trees. `plan7` runs the new word, `v3` the old. W+3 is right on both. Scratch test, not in the repository | v3 | real, small: no shipped program is known to do it (to confirm with a tool), but the specification is wrong |
| B2 | every `negedge rst_n` (e.g. [project.v:78](../src/project.v#L78)) | `rst_n` goes from the pad to every flop's asynchronous reset with no synchroniser. Released near a clock edge, some flops leave reset a cycle before others. Traced by hand on v3: if `bub` leaves first, word 0 of the boot ROM (`IN_RD R1`) is skipped, `ui_in[0]` reads as 0 and the chip enters the loader instead of direct boot; if the PC leaves last, word 1 runs twice. The test of "a reset released on a clock edge" covers the logical case, not this one. | reading | all | real on silicon, rare (the window is a fraction of a nanosecond in each period); two flops fix it |
| B3 | [project.v:44](../src/project.v#L44) | `dbg_mode` has no reset and is loaded only by a clock edge while `rst_n` is low, from `ui_sync[2]`, which itself needs two edges. A reset pulse with fewer than three clock edges leaves `dbg_mode` as it was; after power-up that is random, and if it is 1 `uo_out` shows the debug view instead of `UO`: the boot status and every firmware output on `uo` are wrong. | reading | all | real if the host resets without clocks; fixed with B2, and by stating "hold reset for 3 clocks" |
| B4 | [pio.v:44-45](../src/tt_um_msf_pio.v#L44) | The pads are driven through logic, not straight from flops: `uio_out` is a multiplexer between `OUT` and the TX engine, and on v3 `uio_oe` is `OE` gated by the late guard. When the engine takes or releases a pin (release mode, `TX_CFG`) and both sides are 1, the multiplexer can glitch low for a moment. | reading | all | low, but it is the kind of thing an ASIC reviewer looks for; one register stage, no change of latency if the next values are used |
| B5 | [sram.v:43](../src/tt_um_msf_sram.v#L43) | Port A of bank 0 reads on every cycle whose fetch address is 0-511, including 0-127, where the boot ROM supplies the word. While the loader runs (PC in 0-127) and writes data RAM at 0-127 through port B, the two ports meet on one address whenever the write address equals the fetch address. The read data is unused; whether the write is safe is not something our model of the macro says. It also burns power for nothing. | reading | all | unknown, probably harmless; removing the question costs one AND gate |
| B6 | [bootloader.s:43](../test/bootloader.s#L43) | The loader's UART divisor is the constant 104, right for 12 MHz. At v3's clocks the loader runs at clk/104: 461,538 baud at 48 MHz, 480,769 at 50. `info.yaml` still says 12 MHz and 6x4. And every time limit shrinks with the clock: at 50 MHz `CNT` and `TMO` reach 1.31 ms (5.4 ms at 12 MHz), and the slowest engine bit is 763 baud (183 at 12 MHz). "v2.2 programs run unchanged" is true in cycles, not in seconds. | reading | v3 at speed | real for bring-up: the host tool and the documents must follow, or the ROM measures the rate itself (§6.6) |
| B7 | [project.v:111](../src/project.v#L111), `dbg_sel` | The debug select goes from the raw `ui_in` pads through a 64-to-1 multiplexer over four register files to `uo_out`, and is timed as an ordinary pad-to-pad path: 60 % of the period after the I/O delays, 12 ns at 20 ns. It may be among v3's worst paths for no functional reason (the select is static between clock steps by its own specification). | reading; not yet looked up in a timing report | all, matters at 20 ns | a closure lever: check the report, then declare the path in the SDC with the reason |
| B8 | `serdes`: [line 231](../src/tt_um_msf_serdes.v#L231), [line 114](../src/tt_um_msf_serdes.v#L114) | The receiver's character mode stops at the last data bit and never looks at the stop bit: no framing error, no break. De-stuffing drops the bit after six ones without checking it is a 0. A transmitter that runs out of words holds the line silently (known, `plan.md` 7.13). For a chip whose claim is watching a bus, the engines themselves report no errors. | reading | all | design gap, not a bug; `STAT` bit 15 is free |
| B9 | [config.json:47](../src/config.json#L47) | Hold slack at the fast corner is +0.08 ns with margins of 0.1 and 0.05 ns. Setup can be bought back with a slower clock on the bench; a hold failure cannot, and there is no CMOS5L silicon to say how the models compare. | reading the recorded metrics | all | sign-off risk; raise the margins |
| B10 | `serdes` mode 3 (v3) | The phase arithmetic assumes the phase cannot pass a bit boundary in the cycle a centre is taken: true only for `RX_DIV` up to about 0.26 of a bit a cycle, that is four cycles a bit or more. Faster, a boundary is missed. | reading, by the arithmetic | v3 | a limit to write into `isa.md` §6.2 |
| B11 | text | Stale: `project.v`'s header says ISA v2.0 and two threads; `regfile.v`'s says T1 only; `isa.md`'s reset list says "both threads"; `info.yaml` says 6x4 and 12 MHz. `TSTART`/`TSTOP` use two bits of a three-bit field, so `#4`-`#7` alias `#0`-`#3` in the RTL: check the assembler refuses them. | reading | all | cosmetic, but judges read headers |

### 5.2 Read and found sound

Each of these I traced through the code looking for a fault and found none:

* the thread rotation when the running set changes: every case of two, three and four
  threads I could construct (a thread stopping itself, stopping the next owner,
  restarting itself or another, the last thread stopping with T0 stalled or not), in
  v3's form with the fetch a cycle earlier;
* v3's forwarding: the write of the instruction before, the write-back of a released
  blocking op, a restart that clears a register file, the change between single and
  multi-thread mode;
* `LD` and `FIFO_GET` data across other threads' slots, with two banks;
* the flag priorities, the late `WAIT_CMP`, the timeout count (exactly `TMO`+1);
* the FIFO's pointers, full and empty; the capture engine's queue and its use of port B;
* the CRC unit; the TX engine's two buffers (a push can never meet the engine emptying
  the buffer in the same cycle, because a push needs the buffer empty); the RX engine's
  pop and arrival in the same cycle.

This agrees with the formal properties and the random differential tests, which cover
most of it more thoroughly than a reading can.

### 5.3 What this means

No finding calls for a different architecture. B1 is a specification error that v3
introduced and nothing caught, because the formal properties leave memory free and the
random programs do not store into code nearby. B2-B5 and B9 are all of one kind: places
where silicon can differ from the simulation. They matter more than their size suggests,
because the prize is a fabricated chip and the FAQ recommends gate-level simulation "to
catch design (and occasionally tool) errors". Fixing them and saying so is evidence of
the kind of care the judges ask for.

## 6. v4 hardware

### 6.0 Rules

* **Specification first**, as always here: `isa.md`, then the ISA model from the
  specification, then the RTL, the assembler, the random generator, formal, directed
  tests, `fpga/rtl`, the checker.
* **Each step is shippable.** After every step the suite, formal and a quick synthesis
  pass, and the step is pushed. If time runs out, what is in is in (§14).
* **Old programs keep working.** Every v3 program assembles and passes on v4 unchanged,
  except where a configuration word is repacked for 11-bit addresses (§6.3), which
  `msf.inc` hides.
* **Every addition names the class of protocols it opens**, not one protocol.

### 6.1 Encoding

The instruction set is full: 32 of 32 opcodes, 32 of 32 EXT sub-ops, 8 of 8 ports.
Free: `SETM` and `CLRM` selectors 6 and 7, which take an 8-bit immediate and no
register.

* `SETM` selector 6 becomes **`ESEL #imm8`**, a selector byte per thread:
  bit 0 chooses the engine set (0 or 1) that this thread's `TX_*`, `RX_*`, `CRC_*` and
  `STAT` bits 0-4 address; bits 3:1 choose the port page for `PW`/`PR`, giving 64
  ports. Reset and `TSTART`: 0, so every existing program sees page 0 and set 0.
* `SETM` 7 and `CLRM` 6, 7 stay free.
* The selector is per thread, so one thread's choice cannot change what another's
  instructions mean, and the checker follows it as it follows any constant.

Page 0 is today's ports. Page 1: timed pins. Page 2: engine options. Page 3: time and
identity.

### 6.2 Step 1: the fixes

| Fix | Change | Check |
|---|---|---|
| B2, B3 | a two-flop reset synchroniser (asserted at once, released on a clock edge); every flop uses it; `dbg_mode` is loaded under it. `info.md`: hold reset for at least three clocks | the suite with the release at random phases; a gate-level run with delays that releases reset between edges. Cycle numbers counted from reset move by two: the tests that count them are updated once |
| B4 | the pads come straight from flops: `pio` registers the final `uio_out` and `uio_oe` from the next values of `OUT`, `OE`, the engines and the guard, so the pad timing in `isa.md` §1 is unchanged | the differential tests already compare the pads cycle by cycle |
| B5 | port A of bank 0 is enabled only for addresses 128 and up | none needed in simulation; the netlist equivalence run |
| B1 | `isa.md` §1: a store must be at least three instructions ahead in single-thread mode; in multi-thread mode, a word must not be written in the two cycles before it is fetched. The ISA model raises an error on a violation, so the random tests see it; the assembler's listing and the checker warn. No bypass in hardware: it would sit on the fetch address, the latest signal in the core | the scratch test becomes a regression test; a scan of all firmware for stores into code |
| B7 | read v3's timing report; if the debug path is in the worst ten, declare it in both SDC files with the reason | the report |
| B9 | hold margins raised (§7.3) | sign-off |
| B10, B11 | text | review |

Three working days.

### 6.3 Step 2: 2,048 words

Four of the macro we already use (512x16, two ports), bank = address bits 10:9.

* **Opens:** frames longer than the memory used to be (a 1,518-byte Ethernet frame is
  759 words; today code and data share 1,024), USB device firmware with its
  descriptors, captures of 1,024 words that no longer sit in the code space, replay
  tables, bigger learned programs.
* **ISA:** the PC and every address become 11 bits. `JMP` and `CALL` already carry 11.
  `LD`, `ST`, `MEM_A`, `JR`, `RET`, `TSTART` take bit 10 of their register.
  `FIFO_CFG` and `CAP_BUF` are repacked (base 11 bits); `msf.inc` supplies the macros.
  Map: 0-127 data RAM, 128-1023 code, 1024-2047 code or data; capture defaults to
  1024-2047.
* **Why this macro again:** its integration is known to work here: the PDN plug-in,
  the liberty files per corner, the precheck pin fix, LVS. Before choosing, list the
  macros in the pinned PDK commit with their sizes; a single larger macro for the upper
  half would be smaller, but only if it has two ports or the upper half is data only.
* **Risk:** the floorplan (§7.1). Four macros side by side are 1,610 µm of a die about
  1,724 µm wide. If the trial fails: three macros (1,536 words), or two and this step
  is dropped.

Three working days of RTL, model and tests; the floorplan trial runs beside it.

### 6.4 Step 3: timed pins

The architectural addition, and the one I would keep if everything else were cut.

**The problem it removes.** Today an edge is exact only if the instruction that makes
it runs on the right cycle. With *n* threads a thread owns every *n*-th cycle, so its
edges land on a grid of *n* cycles, and a reaction to an input is late by up to *n*-1
cycles and by however long the code between the wait and the write takes. The TX engine
is exact but only for a stream of bits.

**The addition (port page 1):**

* `TS ra`: the value of `CNT` on the cycle the event that released this thread's last
  `WAIT_EDGE` or `WAIT_CMP` happened, not the cycle the thread was released.
* `AT rb`: arms this thread's one event slot with the time `rb`. The thread's next pin
  write (`PIN_W`, any `SETM`/`CLRM` pin operation, `OUT_W`) is not applied in its own
  cycle; it is held and applied when `CNT` equals `rb`, so the pad changes on the cycle
  after, as for a write executed then. The thread does not wait.
* **Refusal.** If the time has already passed when the write arrives (the same test as
  a late `WAIT_CMP`), or the slot is still occupied, the write is not made at all, `Z`
  is 1, the thread's LATE flag is set and the late guard acts. An edge is on its cycle
  or it does not happen.
* Events of several threads on one cycle are all applied; on the same bit the lower
  thread wins, and `isa.md` says so.

So `WAIT_EDGE; TS R1; ADD R1,R1,R2; AT R1; PIN_TGL #m` answers an edge exactly `R2`
cycles after it, whatever the thread count and whatever else the thread computes
meanwhile.

* **Opens:** every reactive protocol where the response time is the specification: a
  1-Wire slave's slots, an I2C target's ACK and clock stretching, SPI target data, IR
  and SENT replies, USB turn-around, and exact fault injection (a glitch *k* cycles
  after the start bit) from any thread.
* **Prior art, to cite:** XMOS's timed ports (output at a port time, timestamp an
  input) and Escapement's admit-or-refuse queue. What is ours: it is a property of the
  ISA of a four-thread core with a checker and a late guard, and the write-up says
  exactly that.
* **Formal:** F17, for every program: an armed write is applied on the cycle `CNT`
  equals its time and on no other, or refused when armed; a refusal sets LATE. The
  state is small (four slots), so an unbounded proof is the aim.
* **Checker:** an `AT` deadline needs only "armed before its time", which is easier to
  certify than an exact path length.
* **To settle in `isa.md` before any RTL:** whether `SHIFT_OUT`/`XFER`'s output bit
  counts as a pin write (proposed: yes); whether `AT` can also hold the start of a
  `TX_PUSH` (proposed: not in v4); what `TSTOP` does to a pending event (proposed:
  cancels it).
* **Cost:** about 190 flops and four comparators.

Five working days with its proof and tests.

### 6.5 Step 4: two engine sets

Today four threads share one TX engine, one RX engine and one CRC unit, both engines
work only on `uio`, and `plan.md` 7.13 had to reword "a thread's timing never depends
on other threads" because of it.

**a. A second TX, RX and CRC**, chosen per thread by `ESEL` bit 0.

* **Opens:** both directions of a link in hardware at once (a monitor on TX and RX of
  a UART, a USB sniffer, an interposer that receives and re-sends each way with engine
  timing), two protocols at once (USB to UART, SPI to I2C), and threads that share no
  engine, which makes the isolation claim true without the caveat.
* If both sets drive one pin, set 1 wins; `isa.md` says so.

**b. Engines on all 24 pins.** `RX_CFG` bit 3 (free) selects `ui` instead of `uio`, as
`WAIT_EDGE` already can; `TX_CFG` bit 15 (free) selects `uo`. A monitor then needs no
bidirectional pin.

**c. External clock (page 2).** An engine shifts on an edge of a chosen input pin
instead of on its divider, optionally only while a chosen select pin is active.

* **Opens the class we are weakest in:** synchronous protocols where the other side has
  the clock. Today's SPI target is firmware, 16 cycles a bit: 750 kHz at 12 MHz, 3 MHz
  at 50. With the engine shifting on SCK the CPU works per word, not per bit; the aim
  is 4 cycles a bit to receive and 8 to answer (12 and 6 MHz at 50 MHz), to be measured.
  It also gives I2S and TDM as a slave, a JTAG TAP, an SWD target, a 25xx flash at a
  useful speed.

**d. General stuffing (page 2).** Count and kind: after *n* ones insert a 0 (USB with 6,
HDLC with 5) or after *n* equal bits insert the opposite (CAN with 5), for transmit and
receive, with a stuff-error flag. CAN and HDLC then run on the engines.

**e. Error flags (B8).** `STAT` bit 15: the selected set has an error; page 2 says which:
framing (stop bit), stuff error, transmit underrun while streaming.

**f. Half-cycle sampling for RX mode 3.** A second synchroniser on the falling clock
edge gives the phase one more bit: 10 ns at 50 MHz instead of 20. v3 holds 10 ns of
jitter where 802.3 asks 13.5 and names sampling on both edges as the way to more. The
Python model of mode 3 is run with it before any RTL; if the model does not hold
13.5 ns with margin, this is not built.

**g. Optional, first to be cut:** a fractional divider (eight fractional bits) so one
clock makes awkward rates (S/PDIF's 48 kHz needed a 30.72 MHz clock in the draw), and
an engine prescaler of 16 for rates below 763 baud at 50 MHz.

Cut order inside this step, if time runs short: g, f, d, c's transmit side, then a.
Eight working days.

### 6.6 Step 5: the boot ROM

Firmware only, in the 25 free words of the ROM (words 103-127 are NOPs).

* **Autobaud (B6):** the host sends 0xFF first; its start bit is one bit long, and the
  ROM measures it with `CNT` and sets both dividers. The loader then works at any
  clock, and a host that does not send it keeps the old rate. The frame format does
  not change.
* **Read-back:** a command that returns memory words, so a load can be verified and a
  capture fetched without loading a reader.
* **Memory test:** a command that writes and checks every word of the SRAM and reports
  the first bad address on `uo_out`. On a first chip this separates "the SRAM is dead"
  from "my program is wrong".
* **Chip ID and version** on port page 3, so the host tools can tell v2.2, v3 and v4
  apart.
* **32-bit time:** the high half of a 32-bit cycle count readable on page 3, for long
  timeouts and timestamps in firmware. `WAIT_CMP` stays on the low half.

If it does not fit in 128 words, read-back goes first. Two working days.

### 6.7 Considered and not taken

| Idea | Why not |
|---|---|
| A RISC-V host core | §4 |
| A second pipeline or core | throughput is not what limits any protocol we have measured; engines and timed pins are |
| A host port in hardware (SPI slave into the SRAM) | the UART loader moves 2,048 words in about a tenth of a second at 50 MHz; the rest of its value (live memory access) is not worth a new block on the pins this late |
| A PLL or any analog block | allowed, but unverifiable for us and the RP2350 already makes the clock |
| 60 MHz as the target | clock is not a criterion; 48 and 50 MHz are what USB and Ethernet need |
| Register files in latches | saves area we no longer need, costs timing analysis we would have to learn |
| Dynamic scheduling | breaks the fixed rotation that the proofs rest on |

### 6.8 Area

Estimates from counting registers, to be replaced by `tools/synth_check.py` at each
step.

| | Flops added | Note |
|---|---|---|
| v2.2 as laid out | 1,604 flops, 11,534 cells from synthesis, 17,245 after layout | measured |
| v3 | about 100 | not measured after layout |
| Step 1 | about 30 | reset, pads |
| Step 2 | about 20 | wider PCs and addresses |
| Step 3 | about 190 | four event slots and timestamps |
| Step 4 | about 300 for the second set, 60 for options, 32 for half-cycle sampling | |
| v4 total | about 2,350 flops; perhaps 16,000-17,000 cells from synthesis | estimate |

The die (derived from the 6x4 die's 1289.28 x 710.64 µm and the tile pitch, to be read
from the template's DEF): about 1724 x 711 µm, 1.225 mm². Four macros are 0.354 mm²,
29 % of it. Standard cells would fill about 38 % of what is left, against about 34 %
today; with three macros about 34 %. That is close to today's density, where Metal3,
the only horizontal signal layer, was already the constraint, so the floorplan trial
(§7.1) decides, not this arithmetic.

## 7. Physical design on 8x4

### 7.1 This week: two trials, before any v4 RTL

1. **v3's RTL unchanged, on 8x4, with its two macros, hardened to the end** on the other
   machine, placed and routed at 18 ns, signed off at 20 ns. This gives the number v3
   never got, the slow corner after routing, and tells whether the wider die relieves
   the congestion that stopped the 6x4 run. Result:
   * meets 20 ns: v4 goes on at 20 ns;
   * misses by less than 2 ns: v4 goes on; the worst paths get a cut in step 1 (B7 first);
   * misses by more, or does not route: the clock target becomes 25 ns (40 MHz, where
     10BASE-T still works) and USB full speed at 48 MHz becomes a typical-corner claim.
     Sam decides.
2. **Four macros side by side on 8x4**: floorplan, power grid with the stripe plug-in,
   global routing and the precheck's pin check only. It need not meet timing. It tells
   whether step 2 is four macros, three or none.

Also this week: sync the repository with the template's `main` (workflows, support
tools), set `tiles: "8x4"` on the v4 branch, and read the die size from the DEF.

### 7.2 Floorplan

Macros along the bottom with their pins facing up, as now. The two that hold code next
to the core's fetch logic in the middle; the two for data and capture outside them: a
load has a whole cycle, a fetch does not. The 43 tile pins are at the top; the pad
registers (B4) and the synchronisers go next to them.

### 7.3 Timing and sign-off

* Place and route at 18 ns, sign off at 20 ns in every corner, as v3 planned.
* **Hold:** margins raised so the fast corner's hold slack is at least 0.2 ns after
  routing (today +0.08 ns). Setup slack on the bench is recoverable; hold is not.
* **Gate-level simulation with delays** (SDF) at the slow and fast corners for the boot,
  one UART program and one thread program. We have gate-level tests without delays;
  the FAQ recommends gate-level simulation.
* **Unknown memory at power-up:** one gate-level run with the SRAM model filled with
  random words instead of zeros, to show nothing depends on a clean memory.
* Then the existing chain: `tools/check_signoff.py` on the metrics, the precheck, the
  gate-level tests, `formal/netlist_equiv.py`.
* One CI GDS run on the shuttle's action for the tagged commit, and one for the final
  commit. An 8x4 run will take longer than 6x4's 2.5 hours; the limit is 6. Each is
  asked for first, as now.

## 8. Verification of v4

What exists is kept and re-run. What is added, and the number to aim for:

| Item | Aim |
|---|---|
| ISA model | every v4 feature, written from `isa.md` before the RTL; the store-ahead rule as an error |
| Random differential tests | generator covers `ESEL`, `AT`, both engine sets' configuration, 11-bit addresses |
| Directed tests | each feature; each protocol class's sweep (§9.4) |
| Formal: timed pins | F17 unbounded |
| Formal: engine binding | a thread on set 0 cannot change set 1's state or pins, and the reverse (single copy; it is a decode property) |
| Formal: the cost table | **new claim:** for each instruction class, the number of cycles to the thread's next issue is what `isa.md` §5 says. The deadline checker's table is then proved against the RTL, not only tested. This is the cheap half of what MarcosAsh's kernel proof gives |
| Formal: F16, two copies | one more attempt with the datapath narrowed for the proof (the property is about control); a time box of two days. If undecided, it stays "not proved" in the write-up, as now |
| Formal: the four bounded properties | unbounded where the cost proof makes them easy; otherwise stated as bounded |
| Planted bugs | every new property has one that must fail |
| Mutation | 300 or more mutants over the v4 RTL (40 today), on a quiet machine |
| Deadline checker | follows `ESEL`; certifies `AT`; the planted-timing-bug campaign re-run to the end (it was stopped on 9 Nov) |
| Gate level | §7.3 |
| FPGA | `fpga/rtl` in step; the 67 C tests; the MCU test at 48 MHz (§11) |

Everything in this table has a row in `verification.md`'s claims-to-evidence table, and
a claim without its evidence by 10 January is removed from the write-up, not softened.

## 9. Flexibility: evidence we did not make ourselves

The main criterion, and the one where a list of 79 names proves least.

### 9.1 The tag

When v4 is signed off and has run on the board (§13, 20 December), tag the commit and
record the netlist's hash in `verification.md`. Everything in §9 happens after the tag
and touches no RTL. This is not a freeze of `main` (Sam's decision of 28 October
stands): it is a dated point that makes "written for an unchanged chip" checkable.

### 9.2 A second draw

Twelve protocols drawn from the pool by a public random value published after the tag,
the pool and the rule committed before it, as in `docs/draw/`. Each judged by an outside
decoder or a device model written from its standard. Failures are published as they
are: the first draw's disagreement with a data sheet is the most convincing part of it.

### 9.3 People who have never seen the chip

Two or three people outside the project (course mates, a lab demonstrator) each write
one protocol from the quick-start and `isa.md` alone, timed, with the playground. We
publish the time taken, what they got wrong, and what we changed in the documents
because of it. An afternoon each.

### 9.4 The envelope

A table by *class* of protocol, generated by a sweep script in CI, that says how fast
each goes and what sets the limit:

| Class | Example | Limit today | What sets it | After v4 |
|---|---|---|---|---|
| Asynchronous serial, engine | UART | to measure | synchroniser, 16-bit divider | and a prescaler |
| Synchronous, we are the clock | SPI controller | to measure | instructions per bit | the same |
| Synchronous, they are the clock | SPI target | 16 cycles a bit (measured) | firmware per bit | 4 to receive, 8 to answer (aim) |
| Self-clocked | 10BASE-T, Manchester | 5 cycles a bit at 10 ns jitter (measured on v3) | 20 ns sampling | 13.5 ns (aim) |
| NRZI with stuffing | USB | 4 cycles a bit (v3) | engine | and CAN, HDLC on the engine |
| Pulse width and position | WS2812, SENT, IR, 1-Wire | to measure | thread grid | 1 cycle with timed pins |
| Reactive device | I2C EEPROM, 1-Wire slave | to measure | reaction path | exact with `TS`/`AT` |
| Parallel | HD44780, 8080 | to measure | pin count | the same |
| Two at once | bridge, interposer | one engine pair | sharing | two sets |
| Long gaps | DCF77, servo | 1.31 ms a wait at 50 MHz | 16-bit `CNT` | loops, 32-bit read |

And a list of what it cannot do, with the reason: anything over about 12 Mbit/s,
anything needing analog levels, more than eight bidirectional pins.

### 9.5 Breadth

100 programs or more, but counted by class in the write-up, not as a total. New ones
are chosen to fill the envelope's rows, and to use what v4 adds: a 25xx flash at
several megahertz, an I2S slave, a JTAG TAP, CAN and HDLC on the engines, a USB to UART
bridge, a full-duplex UART monitor, an interposer with engine timing both ways, a
1-Wire slave on timed pins, a maximum-length Ethernet frame held in memory.

## 10. Toolchain

### 10.1 `msfc`: a program that compiles is on time

A small structured language that compiles to msfcore assembly: 16-bit variables,
`if`/`while`/`for`, pins, waits and engines as built-ins, and time as syntax: `at t+104
{ ... }`, `every 104 { ... }`. The compiler places the `WAIT_CMP`s and `AT`s, runs the
deadline checker on its own output, and refuses the program if a deadline cannot be
certified, with the path that misses.

* Checked like everything else: a reference interpreter of the language in Python,
  written from its specification, compared with the compiled code on the ISA model over
  random programs.
* Ten existing programs rewritten in it, with a table of size and timing against the
  hand-written assembly, whichever way it comes out.
* Scope kept small on purpose: no functions beyond inlining, seven live variables and
  spills to data RAM, no types but 16-bit. Seven working days. If it slips past
  3 January it is dropped and the assembler's macros are documented instead.

### 10.2 The demo-board driver and bring-up kit

We have a PC loader (`tools/msf_boot.py`) and nothing for the board the winners get.

* A MicroPython module for the Tiny Tapeout demo board: set the clock, reset with the
  right straps, load over UART with autobaud or over the parallel port, read the debug
  view, single-step, run the self-test.
* The same code runs on a PC against the RTL, with the board's pins as a cocotb model,
  so the bring-up is rehearsed before any chip exists, with faults planted (a stuck
  pin, a wrong rate, a bad SRAM word; the idea is Escapement's).
* A bring-up document in stages: clock and reset; the debug view answers; the ROM
  answers; the memory test; the self-test firmware; loopbacks on Pmod jumpers; then the
  clock raised step by step to find the limit.
* A detail to settle on the board's firmware: USB full speed needs exactly 48 MHz, so
  the RP2350's own clock must be a multiple of it.

Four working days.

### 10.3 The playground

It exists. Add `msfc`, the envelope table and v4's model. It needs the repository
public (§16).

## 11. Hardware evidence

**FPGA, by Muath.** One Vivado build of v4 at 48 MHz; the 67 C tests; the MCU test,
extended from v3's (`fpga/ps/mcu_v3`, `mcu_ext/protocol_tests`), running free. v3's
board test was written and never run. Running it this week, if the build is cheap,
would tell us whether the decode stage works at speed before v4 is built on it; if
Muath's time is short, skip it and run v4's.

**One bench session** (`plan.md` 7.11, extended), in order of evidence per hour, with
what Sam and Muath already have:

| Demo | Parts | Why |
|---|---|---|
| msfcore as an SWD probe reads the MSPM0's ID and memory | none | a real ARM debug port; written (`swd_mcu`), never run |
| the margin finder pushes the MCU's UART, SPI and I2C until they fail | none | the instrument claim on a real device, with numbers |
| a full-duplex monitor and an interposer between the MCU and the PC | none | what the second engine set is for |
| the pin finder finds the LaunchPad's SWD pins | none | |
| the learner records a made-up protocol from the MCU and speaks it | none | |
| a timed-pin reply measured on a scope or the capture engine: jitter of 0 cycles with four threads running | none | the v4 headline, on hardware |
| USB full speed to a PC as a keyboard | a cut USB cable and one resistor | the strongest single photo; only if the parts are to hand |
| 10BASE-T | a jack with magnetics | only if the lab has one |

Nothing here is bought (Sam's decision). Each demo is recorded with its command, its
output and a photo; the video is three minutes of this session.

## 12. The write-up

The FAQ: the write-up is weighed heavily, for readability and for how clearly new
techniques are explained.

* **A ten-minute path.** The README becomes one screen: what it is, a diagram, the
  three things that set it apart, a table of evidence with links, the limits, and
  "try it" (the playground, one command for a demo). Everything else is behind links.
* **One architecture document** with one diagram, written for someone who knows PIO and
  PRU: instruction costs as part of the ISA, the thread rotation, timed pins, engines,
  what we would do differently from PIO, PRU and XMOS's timed ports, and what we took
  from each.
* **The techniques, each explained once and shortly:** the model written from the
  specification and compared on every issue; planted bugs for every proof and for the
  checker; the cost table proved; the public draw; the newcomers; claims attacked; how
  AI was used and where it was wrong (`ai.md`, updated for v4, including this plan).
* **Limits, first-class.** The section stays, and stays honest. It is the part of our
  write-up that reads least like everyone else's.
* **A cold read** by someone outside the project, timed at ten minutes, before the
  last week; what they could not find is moved up.
* `instruments.md` is 641 lines and stays as the reference; it gets a one-page summary.

## 13. Schedule

Work is done in one conversation at a time (no sub-agents), so the items below are
sequential; long simulations and hardening run in the background or on the other
machine. "Days" are working sessions.

| Week | Dates | Work | Gate at the end |
|---|---|---|---|
| 0 | 11-15 Nov | decisions (§16); the two trials (§7.1); template sync; step 1 begun | **G0:** v3 on 8x4 has a slow-corner number; the four-macro floorplan has an answer |
| 1 | 16-22 Nov | step 1 (fixes), step 2 (memory) | **G1:** suite, formal and quick synthesis pass with both; pushed |
| 2 | 23-29 Nov | step 3 (timed pins) with F17 | pushed when proved |
| 3 | 30 Nov-6 Dec | step 4 (engine sets), in its cut order; step 5 (ROM) | **G2:** whatever of step 4 is not passing on 6 Dec is cut |
| 4 | 7-13 Dec | hardening on 8x4 to sign-off; SDF runs; mutation; `fpga/rtl`; Muath's build starts | a signed-off layout locally |
| 5 | 14-20 Dec | board run; fixes from it; the CI GDS run; the cost proof | **G3: the tag.** v4 signed off, netlist = RTL, run on the board |
| 6 | 21-27 Dec | demo-board driver and bring-up kit; envelope sweep; the draw | |
| 7 | 28 Dec-3 Jan | `msfc`; breadth by class; the newcomers | `msfc` in or dropped on 3 Jan |
| 8 | 4-10 Jan | bench session and video; write-up; cold read; repository public | **G4:** a complete draft; every claim has its evidence or is removed |
| 9 | 11-17 Jan | final GDS on the final commit; fresh-clone check of CI and Pages; submission form | **G5:** submitted by 16 Jan, two days spare |

After G3 an RTL change can no longer get a board run and a full round of evidence
before the deadline. That is a cost to weigh at the time, not a rule.

Firmware and tools that need no RTL (the envelope script, the driver, the write-up's
skeleton) can be pulled forward whenever hardening is running.

## 14. The ladder down

Each rung is a complete entry. We step down only at a gate, and say so in the write-up.

| Rung | What is submitted | When we step to it |
|---|---|---|
| 1 | v4 complete | |
| 2 | v4 with step 4 cut down or out (timed pins, memory and the fixes stay) | G2 |
| 3 | v4 = v3 with the fixes and timed pins, two macros | the four-macro trial fails, or G2 with step 3 only |
| 4 | v3 with step 1's fixes, on 8x4 | timed pins not proved by 6 Dec |
| 5 | v2.2 as on `plan7`, 33 MHz | v3 does not close timing at any clock we would state; it passes the shuttle's flow today at 6x4. Ask the organisers by email now whether a 6x4 entry is still accepted, since the answer costs one line |

On every rung §9 to §12 still happen: they do not depend on the RTL.

## 15. Risks

| Risk | Sign | Answer |
|---|---|---|
| Too much RTL too late | a step not passing at its gate | the cut orders in §6.5 and §14; the tag date does not move |
| 20 ns does not close on 8x4 | G0's number | B7's path, then the fallbacks of `plan-v3.md` §2, then 25 ns |
| Four macros do not fit or do not pass precheck | the trial | three, or two |
| Muath's time for builds and the board | no build by 13 Dec | one build only, of the tagged RTL; the C tests in simulation stand in, and the write-up says which evidence is from which version |
| The other machine or Docker fails again mid-run | as on 9 Nov | checkpoints; CI as the fallback for one run, asked first |
| Usage limits cut a session | as on 7 Nov | the status table and the memory notes are kept current so the next session starts from them |
| The write-up grows instead of shrinking | the README is more than a screen | the cold read decides |
| We lose anyway | | every rung leaves a chip design and a method worth having; nothing here is done only for the judges |

## 16. Decisions needed

For Sam, and Muath where named. My recommendation is first in each.

1. **Scope.** v4 as in §6, with the ladder, or v3 on 8x4 with the fixes only (rung 4)
   and all the time on §9-§12. I recommend v4: rung 4 alone adds nothing new in the
   hardware.
2. **Base.** `v3`, on a new branch `v4`. `plan7` merges into `main` whenever Sam likes;
   it does not affect this.
3. **8x4.** Yes: it is the shuttle's size, it costs nothing, and it eases routing.
4. **Memory.** Four macros if the trial passes.
5. **RISC-V.** No (§4).
6. **The tag date,** 20 December, and that after it RTL changes are weighed against the
   evidence they cost.
7. **The v3 board run this week** (Muath): worth doing if the build is cheap, because a
   failure would change what we build on; otherwise skipped.
8. **When the repository goes public** (Muath). The FAQ allows it to stay private until
   submission. I recommend 4 January: the playground and Pages need it, and a fresh
   clone must be seen to pass CI before the deadline, not on it.
9. **Parts for the USB demo:** a cut cable and a resistor, if they are to hand; nothing
   bought.
10. **An email to the organisers** with one question: is a 6x4 entry still accepted?
    It matters only for rung 5.
