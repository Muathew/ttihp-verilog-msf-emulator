# Protocols drawn at random: what each agent was told

The rule, the pool and the round were committed before the round fell (`tools/protocol_draw.py`,
`pool.tsv`, commit a34b7eb at 19:35:40 UTC on 7 Nov 2026; the round fell at about 20:00:45). The
draw is in `draw.json`. `src/` last changed in d1179f3 and hashes to `b1958606...` (sha256 over the
tree, LF line endings): the chip does not change for any of this. This file was written after the
draw and before any agent started: the pin maps and messages below are the only thing the two
agents of a protocol share.

Each protocol gets two agents that cannot see each other's work. One writes the firmware, and one
writes a model of the other side and a checker, from the specification. Then three things judge the
firmware: `msf_timing.py`, sigrok's decoder, and the other agent's model. A failure counts as a result
and goes in the table in `docs/instruments.md`, as it stands.

## Both agents

* You work in `C:/Year3_projects/ttihp-verilog-msf-emulator`, in a tree other agents are using right
  now. Create only the files named for you below. Do not edit anything else, and do not read another
  agent's files (`firmware/draw_*`, `test/*draw_*` except your own). Do not run git commands that change
  anything; the person who started you commits.
* Run `date -u` when you start. Stop at 90 minutes, whatever state it is in, and report what stands.
* Find the protocol's specification yourself, on the web or from what you know, and name your sources
  in your report and in a comment at the top of your file. Do not read sigrok's decoder source, under
  `/home/sambo/.msf_sigrok/root/usr/share/libsigrokdecode/decoders/`: it is the judge. If you can find no
  description of a protocol at all, you may read that decoder as the only one left, and you must say so,
  because it weakens the result.
* Report honestly: what works, what does not, what you left out of the protocol, every assumption. A
  protocol that does not fit is a good result if you show why. A faked pass is worthless, so never bend
  the message, the timing or a check to get green. Your final message is the record, so make it complete.

## The firmware agent

You write `firmware/draw_<id>.s` and `test/test_draw_<id>.py`. The chip is finished and cannot change.

Read `docs/isa.md`, `firmware/msf.inc`, and examples you choose from `firmware/` (`uart_tx.s`,
`i2c_master.s`, `onewire.s`, `can.s`, `manchester*` and `ps2_device.s` are good starts). `test/fw_tb.py`
and `test/test_firmware.py` show how a firmware test runs: `fw_boot`, `Bench`, `Recorder`, `save_trace`.
You have 896 words of program and 128 of data; the core runs one instruction a cycle, and is signed off to
33.33 MHz. The clock is 12 MHz unless the contract says you may choose.

Your test boots the firmware, puts the smallest stand-in you need on the other side of the bus, and saves
the pins with `save_trace("draw_<id>", samples, decoders, annotations, expect, tooth, rate)`, where `expect`
holds what the decoder must print, and `tooth` is an edge that must change it. Judge it yourself as you go:

```sh
cd test && MSF_BUILD=draw_<id> ../.venv/Scripts/python.exe run_tests.py -m test_draw_<id>   # the RTL
python -I tools/msf_timing.py firmware/draw_<id>.s                                          # must certify
export MSYS_NO_PATHCONV=1 SIGROK_CLI="wsl -d Ubuntu -- /home/sambo/.msf_sigrok/sigrok-cli"
python -I tools/sigrok_check.py draw_<id> --show                                            # the decoder
wsl -d Ubuntu -- /home/sambo/.msf_sigrok/sigrok-cli -P <decoder> --show                     # its channels
```

Always use `MSF_BUILD=draw_<id>`. If the decoder disagrees with you and the specification says you are
right, do not bend the firmware to suit it: report that, with the waveform's evidence.

Report: minutes taken, words, threads, the clock, how many times you ran the RTL, what `msf_timing.py` said
(worst slack), what the decoder printed, what you left out, and the hardest part.

## The model agent

You write `test/draw_<id>_model.py` (the model, no cocotb, with a `self_check()` that drives it with
waveforms you synthesise from the specification, including wrong ones it must reject; run it with
`python -I test/draw_<id>_model.py`) and `test/test_draw_<id>_peer.py`, a cocotb test that boots
`draw_<id>.s` with `fw_boot`, puts your model on the bus through `Bench` (see `test/fw_tb.py`, and
`SpiDevice` in `test/test_interpose.py` for the shape of a model), and asserts everything the contract
says. Be strict: check timing against the specification's limits, not just the data.

The firmware does not exist yet, and you will not run this test: someone else runs it once both are done.
Check that it imports (`.venv/Scripts/python.exe -c "import ast,sys; ast.parse(open(sys.argv[1]).read())"`)
and use the helpers correctly by reading them. Write the contract's expected values into the test, and do not
guess at anything the firmware might choose. A clock or rate it may choose should be read from the waveform.

Report: minutes taken, your sources, what the model checks, and what it does not.

## Contracts

All pins are `uio[n]` unless stated. Open-drain means the firmware only ever drives low and otherwise
releases the pin; the bench's pull-up makes it high. Where a contract says *report*, the firmware sends those
bytes on `uo_out[0]`, 8N1 at 104 cycles a bit, in order, then sets `uo_out[1]` high. Where a peer is named, it
exists only in the test.

### `em4100`, the chip is the tag

Data `uio[0]`, push-pull. A standard 64-bit EM4100 frame (header, ten data nibbles with row parity, column
parity, stop bit) for version/customer `0x1A` and data `0x2B3C4D5E`, Manchester at RF/64 of 125 kHz, which is
6144 cycles a bit at 12 MHz. It repeats with no gap, as a tag does: at least 5 whole frames. No report.

### `spdif`, the chip is the transmitter

Data `uio[0]`, push-pull. IEC 60958 consumer format: biphase-mark, subframes A and B with B, M and W preambles,
192-frame blocks, V = 0, U = 0, even parity, and channel status that says consumer, PCM, and the true sample
rate. 16-bit samples, in the top of the 20-bit audio field, from this table repeated, left then right:
L = `0000 5A82 7FFF 5A82 0000 A57E 8001 A57E`, R = the same table starting at its third entry. At least 2 whole
blocks. Use the highest of 48, 44.1 or 32 kHz you can reach. You may choose any clock up to 33.33 MHz and must
state it; pick one that gives the rate exactly. If no standard rate can be reached, say so, and show the fastest
non-standard rate as a partial result, clearly marked as one. The clock goes in the test's `rate`. No report.

### `microwire`, the chip is the controller

CS `uio[0]`, SK `uio[1]`, SI `uio[2]` (chip to device), SO `uio[3]` (device to chip). The peer is a 93C46
EEPROM in 16-bit mode, 64 words, with word 6 preloaded with `0x1234`, and a write time of 2 ms at most. Clock
at 1 MHz or less. The sequence: EWEN; WRITE word 5 = `0xBEEF`; wait for ready with the status check, on SO;
READ word 5; READ word 6; EWDS. Report `BE EF 12 34`.

### `sda2506`, the chip is the controller

CLK `uio[0]`, DATA `uio[1]` (bidirectional), CE# `uio[2]`. The peer is a Siemens SDA 2506-5 serial EEPROM, 128
bytes, with address `0x12` preloaded with `0x5A` and all others `0xFF`. The sequence: read `0x12`; write `0xA7` to
`0x34`; wait out the write time; read `0x34`. Report `5A A7`. The rest, the clocking, the command format and
timing, comes from the part's datasheet.

### `cec`, the chip is logical address 4, playback

The bus is `uio[0]`, open-drain, one wire. Standard HDMI-CEC timing: start bit, data bits, EOM, ACK with the
follower pulling low, the inverted meaning for broadcast, signal-free time before each frame. In order:

1. Send `40 04` to the TV (Image View On). The peer, a TV at address 0, ACKs it.
2. Send `4F 82 10 00` broadcast (Active Source). Nobody objects.
3. Send `48 04` to device 8, which is absent. No ACK. Retransmit as the specification says, and give up.
4. Listen. The peer sends `04 83` to us (Give Physical Address). ACK both bytes as a follower.
5. Send `4F 84 10 00 04` broadcast (Report Physical Address).

Report: `A` for step 1, `A` for 2, `N` for 3, the two bytes received in 4, `A` for 5.

### `iec`, the chip is the controller, the computer

DATA `uio[0]`, CLK `uio[1]`, ATN `uio[2]`, all open-drain. The Commodore serial bus at its standard timing, with
no fast loaders. The peer is a drive at device 8. In order:

1. ATN asserted: LISTEN 8 (`0x28`), SECOND 15 (`0x6F`). Release ATN.
2. Send the bytes `I`, `0` (`49 30`), with EOI on the last.
3. ATN asserted: UNLISTEN (`0x3F`). Release ATN.
4. ATN asserted: TALK 8 (`0x48`), SECOND 15 (`0x6F`), then the bus turnaround.
5. Receive until EOI. The peer sends `00, OK,00,00` and a carriage return, 13 bytes.
6. ATN asserted: UNTALK (`0x5F`). Release ATN.

Report the 13 bytes received.
