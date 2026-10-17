# 10BASE-T on msfcore — implementation note

> v2 (2026-10-17). Supersedes the v1 note, whose demo unrolled every half-bit
> into its own `PIN_W` (waveform playback, 16-byte frame, no FCS, no link
> pulses). The v2 transmitter is real firmware: `firmware/eth_10baset_tx.s`,
> checked by `test/test_firmware.py::test_fw_10baset_frame_fcs_tpidl_nlp`.

## Feasibility

* 10 Mbit/s Manchester = 20 M half-bits/s. At a **20 MHz** core clock one
  cycle is one half-bit. A CPU can't produce a level every cycle *and* fetch
  data, so the bits come from the **SERDES TX engine** in Manchester mode
  (`TX_DIV` = 1), double-buffered: one 16-bit word shifts out (32 cycles)
  while the firmware loads the next one from data RAM.
* 12 MHz (the USB-friendly default) gives 0.6 cycles per half-bit: not
  possible. The demo board can supply 20 MHz (e.g. RP2040 120 MHz / 6); the
  design is constrained at 40 ns (25 MHz) in `src/config.json`, 25 % above
  the 20 MHz this needs.

## What the firmware sends

1. 7 x `0x55` preamble + `0xD5` SFD (pushed as LE words `5555 5555 5555 D555`).
2. The frame from data RAM (DA, SA, EtherType, payload; >= 60 bytes) as LE
   16-bit words, LSB-first on the wire.
3. **FCS**: the CRC unit runs CRC-32 (reflected `0xEDB88320`, init
   `0xFFFFFFFF`, 16 bits per `CRC_W`) on each word as it is pushed — 16 cycles
   per word, well inside the 32-cycle word time — and the firmware sends
   `~CRC` as two more words. Checked against `zlib.crc32`.
4. **TP_IDL**: TX runs in *release mode*, so its pins show the OUT register
   whenever no symbol is on the line. OUT is set to TD+ high while the frame
   is sent; it appears on the exact half-bit boundary after the last FCS bit
   and the firmware returns the line to idle 6 cycles (300 ns) later.
5. **Normal link pulses**: a 100 ns (2-cycle) positive pulse every 16 ms
   (5 x 3.2 ms absolute `WAIT_CMP` steps, so no drift), which keeps a link
   partner's link-integrity test happy.

The pair is driven differentially (`TX_DIFF`: TD- = complement of TD+) and
needs an external resistor network + magnetics.

## Cycle budget (per 16-bit word = 32 cycles)

`LD` (2) + `TX_PUSH` (1, or stalls until the buffer frees) + `CRC_W` (1) +
`INCR`/`DECR` (2) + `JNZ` (2) = 8 cycles of work per 32: the SERDES never
underruns. After the last data word, the CRC finishes, the FCS words are
computed and pushed while the previous word is still on the line.

## Out of scope

* **Receive**: recovering a 10 MHz Manchester stream needs >= 4x
  oversampling (80 MHz). The RX engine handles NRZ/NRZI (UART, USB low speed)
  but not 10BASE-T.
* MAC-level behaviour (CSMA/CD, collision detect, auto-negotiation FLPs).
