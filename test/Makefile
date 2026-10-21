# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info
# msfcore cocotb suite. Linux/macOS: `cd test && make` (RTL) or
# `make GATES=yes` (gate level, after the GDS action). Windows: use
# `python run_tests.py --all` (same modules, no make/sh needed).
# Keep PROJECT_SOURCES in sync with info.yaml (run_tests.py reads info.yaml).
# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = project.v tt_um_msf_ctrl.v tt_um_msf_regfile.v tt_um_msf_alu.v tt_um_msf_sram.v tt_um_msf_bootrom.v tt_um_msf_pio.v tt_um_msf_serdes.v tt_um_msf_crc.v tt_um_msf_cap.v tt_um_msf_filt.v

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
# Behavioral model of the SRAM macro (not in PDK stdcell lib; needed for GL sim)
VERILOG_SOURCES += $(SRC_DIR)/RM_IHPSG13_2P_512x16_c2_bm_bist_sim.v

# this gets copied in by the GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)
COMPILE_ARGS 		+= -g2012

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# List test modules to run, separated by commas and without the .py suffix:
# GL mode uses only top-level-port tests (no internal hierarchy in netlist).
ifneq ($(GATES),yes)
COCOTB_TEST_MODULES = test_core,test_regressions,test_isa_v2,test_peripherals,test_threads,test_boot,test_random_diff,test_protocols,test_extensions,test_firmware,test_reprogrammable,test_crv_invariants,test_golden_model,test_targets,test_monitors,test_bridge,test_faults,test_debug,test_capture
else
COCOTB_TEST_MODULES = test_gl_smoke
endif

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim
