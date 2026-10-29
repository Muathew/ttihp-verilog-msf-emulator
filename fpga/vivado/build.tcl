# SPDX-License-Identifier: Apache-2.0
# Build the AUP-ZU3 bitstream and XSA from the repository, in batch mode:
#
#   vivado -mode batch -source fpga/vivado/build.tcl [-tclargs <build dir>]
#
# The build dir (default fpga/build/vivado, git-ignored) is deleted first.
# Keep its path short and ASCII-only (19 Oct: Vivado crashed in a project
# under a long path). Vivado 2024.1, as msf_bd.tcl requires.
#
# The RTL comes from fpga/rtl/sources.f and nowhere else. src/ is the ASIC:
# its tt_um_msf_sram.v instantiates the IHP SRAM macro whenever SYNTHESIS is
# defined, and Vivado always defines it. The macro is a hard block for the
# chip (its supply pins are wired by the ASIC flow, src/config.json), with no
# logic Vivado can build. fpga/rtl/tt_um_msf_sram.v is the board's version:
# one block RAM. After synthesis this script stops if an IHP cell or a black
# box is left, or if u_sram did not become block RAM.
#
# Outputs in the build dir: msf_aup_zu3.xsa (bitstream included),
# utilization.rpt, timing.rpt and build_info.txt (commit, WNS, cell counts).

set here [file dirname [file normalize [info script]]]
set repo [file normalize [file join $here .. ..]]
if {$argc > 0} {
  set out [file normalize [lindex $argv 0]]
} else {
  set out [file join $repo fpga build vivado]
}
set part  xczu3eg-sfvc784-2-e
set board realdigital.org:aup-zu3-8gb:part0:1.0

proc fail {msg} {
  puts "build.tcl: ERROR: $msg"
  error $msg
}

file delete -force $out
create_project msf $out -part $part
if {[llength [get_board_parts -quiet $board]]} {
  set_property board_part $board [current_project]
} else {
  puts "build.tcl: board files for $board not installed; building for the bare part"
}

# --- sources: fpga/rtl/sources.f (paths relative to the repository root)
set srcs {}
set fh [open [file join $repo fpga rtl sources.f]]
foreach line [split [read $fh] "\n"] {
  set line [string trim $line]
  if {$line eq "" || [string match "#*" $line] || [string match "//*" $line]} continue
  if {![string match "fpga/rtl/*" $line]} {
    fail "sources.f lists $line: the board build takes RTL from fpga/rtl only"
  }
  lappend srcs [file join $repo $line]
}
close $fh
add_files -norecurse $srcs
add_files -fileset constrs_1 -norecurse [file join $repo fpga board constraints msf_aup_zu3.xdc]
update_compile_order -fileset sources_1

# --- block design (msf_bd.tcl uses the project open above)
source [file join $here msf_bd.tcl]
set bd [get_files -quiet msf_zynq_system.bd]
if {$bd eq ""} {
  fail "msf_bd.tcl did not create msf_zynq_system (see its messages above)"
}
# One global synthesis run, no out-of-context IP runs (the 19 Oct crash was
# in the out-of-context run of proc_sys_reset_0).
set_property synth_checkpoint_mode None $bd
generate_target all $bd
add_files -norecurse [make_wrapper -files $bd -top]
set_property top msf_zynq_system_wrapper [current_fileset]
update_compile_order -fileset sources_1

# --- synthesis, then check the SRAM
launch_runs synth_1 -jobs 1
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
  fail "synthesis failed: [get_property DIRECTORY [get_runs synth_1]]/runme.log"
}
open_run synth_1 -name synth_1
set ihp  [get_cells -quiet -hierarchical -filter {REF_NAME =~ RM_IHPSG13*}]
set bbox [get_cells -quiet -hierarchical -filter {IS_BLACKBOX == 1}]
set bram [get_cells -quiet -hierarchical -filter {REF_NAME =~ RAMB* && NAME =~ *u_sram*}]
if {[llength $ihp]} {
  fail "IHP SRAM macro in the netlist ([lindex $ihp 0]): src/tt_um_msf_sram.v was used instead of fpga/rtl/tt_um_msf_sram.v"
}
if {[llength $bbox]} {
  fail "black box in the netlist: [lindex $bbox 0] ([get_property REF_NAME [lindex $bbox 0]])"
}
if {![llength $bram]} {
  fail "u_sram is not in block RAM; check the synthesis log for the RAM inference messages"
}
puts "build.tcl: SRAM in block RAM: $bram"
close_design

# --- implementation and bitstream
launch_runs impl_1 -to_step write_bitstream -jobs 1
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
  fail "implementation failed: [get_property DIRECTORY [get_runs impl_1]]/runme.log"
}
open_run impl_1
report_utilization    -file [file join $out utilization.rpt]
report_timing_summary -file [file join $out timing.rpt]
write_hw_platform -fixed -include_bit -force -file [file join $out msf_aup_zu3.xsa]

set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
set commit unknown
catch {set commit [exec git -C $repo rev-parse --short HEAD]}
set info [join [list \
  "commit   $commit" \
  "vivado   [version -short]" \
  "part     $part" \
  "WNS      $wns ns" \
  "WHS      $whs ns" \
  "LUT      [llength [get_cells -quiet -hierarchical -filter {REF_NAME =~ LUT*}]]" \
  "FF       [llength [get_cells -quiet -hierarchical -filter {REF_NAME =~ FD*}]]" \
  "BRAM     [llength [get_cells -quiet -hierarchical -filter {REF_NAME =~ RAMB*}]]"] "\n"]
set fh [open [file join $out build_info.txt] w]
puts $fh $info
close $fh
puts "build.tcl: done, $out/msf_aup_zu3.xsa\n$info"
if {$wns eq "" || $wns < 0 || $whs eq "" || $whs < 0} {
  fail "timing not met (WNS $wns, WHS $whs): see timing.rpt"
}
