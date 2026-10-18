// SPDX-License-Identifier: Apache-2.0
// Console output: xil_printf on the board, printf on the host (MSF_HOST).
// Keep formats to %d %x %s %c (with widths) so both accept them.
#ifndef MSF_LOG_H
#define MSF_LOG_H

#ifdef MSF_HOST
#include <stdio.h>
#define msf_printf(...) ((void)printf(__VA_ARGS__), (void)fflush(stdout))
#else
#include "xil_printf.h"
#define msf_printf xil_printf
#endif

#endif
