# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info
# STATUS: full cocotb setup -- 7 msfcore sources + test_core (9 tests, green).
# INTEGRATION (Linux/macOS): `. .\setup-env.ps1` then `cd test; make -B` (venv first).
# INTEGRATION (Windows): Git-for-Windows `sh` fork limits break `make`; use
# `python run_tests.py` (no make/sh; cocotb_tools runner, iverilog).
# GATES/SIM/VVP sections unchanged (IHP PDK, glue-level sim support).
# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = tt_um_msf.v tt_um_msf_ctrl.v tt_um_msf_regfile.v tt_um_msf_alu.v tt_um_msf_sram.v tt_um_msf_bootrom.v tt_um_msf_pio.v

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_io/verilog/sg13g2_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v

# this gets copied in by the GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# List test modules to run, separated by commas and without the .py suffix:
COCOTB_TEST_MODULES = test_core

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim
