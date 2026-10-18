#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM

python3 "$repo_root/fpga/ps/loader/generate_image.py" \
  "$repo_root/firmware/thread_demo.s" \
  --output "$tmp_dir/firmware_image.h"
gcc -std=c99 -Wall -Wextra -Werror -fsyntax-only \
  -I"$repo_root/fpga/tests/vitis_stubs" -I"$tmp_dir" \
  "$repo_root/fpga/ps/loader/src/main.c"
printf '%s\n' "PASS: PS loader C source syntax and generated image interface"
