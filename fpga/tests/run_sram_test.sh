#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM

iverilog -g2012 -s tb_sram -o "$tmp_dir/tb_sram" \
  "$repo_root/fpga/rtl/tt_um_msf_sram.v" \
  "$repo_root/fpga/tests/tb_sram.v"
vvp "$tmp_dir/tb_sram"
