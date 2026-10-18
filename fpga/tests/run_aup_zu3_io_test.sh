#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM

iverilog -g2012 -I"$repo_root/fpga/rtl" -s tb_aup_zu3_io \
  -o "$tmp_dir/tb_aup_zu3_io" -f "$repo_root/fpga/rtl/sources.f" \
  "$repo_root/fpga/tests/tb_aup_zu3_io.v"
vvp "$tmp_dir/tb_aup_zu3_io"
