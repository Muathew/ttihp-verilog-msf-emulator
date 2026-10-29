#!/usr/bin/env sh
# Compile-checks the PS applications (loader and protocol tests) against the
# Vitis driver stubs, in the classic and the SDT driver flow.
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM

ps="$repo_root/fpga/ps"
python3 "$ps/loader/generate_image.py" \
  "$repo_root/firmware/thread_demo.s" \
  --output "$tmp_dir/firmware_image.h"
python3 "$ps/hil/gen_images.py" --output "$tmp_dir/msf_images.h"

for flow in "" "-DSDT"; do
  gcc -std=c99 -Wall -Wextra -Werror -fsyntax-only $flow \
    -I"$repo_root/fpga/tests/vitis_stubs" -I"$tmp_dir" -I"$ps/common" \
    "$ps/loader/src/main.c" "$ps/common/msf_link.c" "$ps/common/msf_emio_xgpiops.c"
  gcc -std=c99 -Wall -Wextra -Werror -fsyntax-only $flow \
    -I"$repo_root/fpga/tests/vitis_stubs" -I"$tmp_dir" -I"$ps/common" -I"$ps/hil" \
    "$ps/hil/src/main.c" "$ps/hil/msf_tests.c" "$ps/hil/msf_proto.c" \
    "$ps/common/msf_link.c" "$ps/common/msf_emio_xgpiops.c"
  # the swd_mcu-only build (a microcontroller on PMOD JA)
  gcc -std=c99 -Wall -Wextra -Werror -fsyntax-only $flow -DMSF_HIL_MCU \
    -I"$repo_root/fpga/tests/vitis_stubs" -I"$tmp_dir" -I"$ps/common" -I"$ps/hil" \
    "$ps/hil/src/main.c"
done
printf '%s\n' "PASS: PS loader and protocol-test sources compile against the Vitis stubs"
