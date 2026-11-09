#!/usr/bin/env sh
# Compiles the MCU test's v3 code on the PC with every warning on: v3_lines.c as
# it is (it has no TI headers), v3_test.c and main.c against stubs/, a stand-in
# for the generated header whose DriverLib names have the SDK's signatures. Catches
# what a compiler can; the CCS build on the MCU's own machine is still the build.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
src="$here/../protocol_tests"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
flags="-std=c99 -Wall -Wextra -Werror -Wconversion -Wno-sign-conversion -O2"
gcc $flags -c "$src/v3_lines.c" -o "$tmp/v3_lines.o"
gcc $flags -I"$here/stubs" -c "$src/v3_test.c" -o "$tmp/v3_test.o"
gcc $flags -I"$here/stubs" -c "$src/main.c" -o "$tmp/main.o"
echo "PASS: the MCU's v3 code compiles"
