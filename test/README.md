# msfcore test suite

cocotb + Icarus Verilog. `tb.v` instantiates `tt_um_msf`; tests drive the pins
and (in RTL simulation) read internal state through `dut.user_project.*`.

## Running

```sh
make                      # Linux/macOS: all RTL modules (see COCOTB_TEST_MODULES)
make GATES=yes            # gate level, with gate_level_netlist.v from the GDS action
python run_tests.py --all # Windows / no make: same modules, sources from ../info.yaml
python run_tests.py -m test_firmware -t fw_usb_ls_tx_packet   # one test
MSF_SEEDS=200 python run_tests.py -m test_random_diff         # more random programs
```

## Files

| File | Role |
|---|---|
| `assembler.py` | assembler/disassembler (ISA v2), `python assembler.py` regenerates the boot ROM |
| `bootloader.s` | boot ROM source (framed UART / parallel loader) |
| `isa_model.py` | independent ISA-level reference model |
| `diffcheck.py`, `rand_prog.py` | RTL-vs-model comparison, constrained-random program generator |
| `golden_model.py` | protocol and CRC golden references |
| `msf_tb.py`, `tools_path.py` | shared testbench helpers, host boot-frame builder import |
| `fw_tb.py` | firmware-level helpers: boot a `firmware/*.s` file, step the chip one clock at a time against Python device models, decode the monitors' reports |
| `test_*.py` | test modules — see `docs/verification.md` for what each covers |
