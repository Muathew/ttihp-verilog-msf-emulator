# msfc: a program that compiles is on time

`tools/msfc.py` compiles a small structured language to msfcore assembly. The language has
time in its syntax, and the compiler runs the deadline checker (`tools/msf_timing.py`, see
[`verification.md`](verification.md) §6b) on the code it generated. If it cannot certify every
deadline it refuses the program and names the source line. That is the promise a C compiler on
a RISC-V core cannot make: msfcore's instruction costs are fixed by the ISA, so how long a path
takes is a property of the program, not of the machine it runs on.

```
const HALF = 16;
var tx = 0xA5;
time t = now() + 40;
oe_set(0x2C);                       // SCK, MOSI and CS are outputs
set(0x20);                          // CS high
at t clear(0x20);                   // CS low
repeat (8) {
    t += HALF;
    if (tx & 0x80) { at t set(0x08); } else { at t clear(0x08); }   // MOSI changes ...
    tx <<= 1;
    t += HALF;
    at t set(0x04);                 // ... half a period before SCK rises
    t += HALF;
    at t clear(0x04);               // SCK falls
}
t += HALF;
at t set(0x20);                     // CS high
halt;
```

(`firmware/msfc/spi_mode0.msf`.) Compiled, run on the ISA model, every edge is on a 16-cycle
grid. With `HALF = 4` the compiler says why not:

```
refused: a deadline cannot be certified
  spi4.msf:13: this timed pin write may miss its time (d at issue -129..-7)
  ...
```

## What the three time forms mean

| Form | Compiles to | Guarantee |
|---|---|---|
| `time t = now() + N;` `t += N;` | `CNT_R`, `ADDS` | `t` is a cycle count; it advances by exact amounts, so a schedule has no drift |
| `at t { ... }` | `WAIT_CMP t`, then the block | the block starts on the cycle after `t`, or the program is refused |
| `at t set(m);` (also `clear`, `toggle`, `write`, `uo`, `oe_set`, `oe_clear`) | `AT t`, the pin write, `WAIT_CMP t` | the pin changes in cycle `t + 1` **by the hardware** (timed pins, [`isa.md`](isa.md) §6.8): exact whatever the code around it does, and the write is refused by the hardware, never made late |
| `every N { ... }` | a loop on a hidden time variable | the block runs with period exactly N, if its body fits |

The checker's verdict for a timed write is its slack: the cycles to spare when the write issues.
A write that might arrive after its time is refused at compile time (the hardware would refuse it
at run time: Z = 1, the thread's LATE flag, the late guard).

## Checked like everything else

* **A reference interpreter** (`msfc.interpret`) written from the language's semantics, not from
  the code generator. `test/test_msfc.py` compiles 120 random programs (arithmetic, compares,
  `if`, `repeat`, `while`, pin writes), runs them on the ISA model and compares the final
  variables and every change of `OUT`, in order, with the interpreter's.
* **Edges at the pins:** `blink.msf` toggles with a period of exactly 50 cycles, `uart_tx.msf`
  sends five different bytes with every edge a multiple of 104 cycles from the start bit and the
  bytes decoded back, `spi_mode0.msf` clocks 0xA5 out on a 16-cycle grid.
* **Refusal:** a loop whose body costs more than its period, a timed write too soon after a long
  computation, and the SPI program with half a period of 4 are all refused with their lines; the
  same programs with room are certified.

## Against hand-written assembly

| Program | msfc | Hand-written | Note |
|---|---|---|---|
| UART transmitter, 8N1, 104 cycles a bit | 38 words, certified, every edge hardware-timed | `firmware/uart_tx.s`: 29 words, certified | the compiler is 31 % larger here, and needs no cycle counting |
| `blink.msf`, period 50 | 10 words | (none) | |
| `spi_mode0.msf`, one byte, mode 0 | 48 words | `firmware/spi_master.s` is a full master with modes 0-3 and is not comparable | |

(Sizes are from the compiled output; `python tools/msfc.py FILE -o OUT.s` writes the assembly.)

## Limits

* One thread, no functions, no multiplication or division, 16-bit values only, at most six
  variables, time variables and loop counters (R0-R5; R6 and R7 are the compiler's temporaries).
  Expressions are variables, constants and one nested level on the left; a right-hand operation
  has to be a variable first, and the compiler says so.
* Shift counts and pin masks are constants. `<` and `>` compare as signed 16-bit.
* `every` and `at` waits are limited to 65,280 cycles ahead (the hardware's `WAIT_CMP` rule).
* The timed form needs the thread on port page 1 for the whole program (`ESEL #2`, which the
  compiler emits), so a program that also needs the FIFO or the capture engine has to be written
  in assembly.
* The deadline checker analyses the generated code with its usual limits (counted loops unroll,
  other joins widen): a program it cannot follow is refused, not accepted on trust.
* Not done from the plan: ten existing programs rewritten (three are here), threads, inlined
  functions.
