// SPDX-License-Identifier: Apache-2.0
// Host stand-in for the link under fpga/ps/mcu_test/src/main.c: the boot calls
// succeed, and each msf_sample() is the next hex byte on stdin as uo_out. The
// program ends with the input. See run_mcu_monitor_test.py.
#include <stdio.h>
#include <stdlib.h>

#include "msf_link.h"

int msf_link_init(void)
{
    return 0;
}

int msf_boot_enter(int step)
{
    (void)step;
    return MSF_OK;
}

int msf_boot_write(uint16_t addr, const uint16_t *words, uint16_t count)
{
    (void)addr;
    (void)words;
    (void)count;
    return MSF_OK;
}

int msf_boot_go(uint16_t addr)
{
    (void)addr;
    return MSF_OK;
}

void msf_release_pins(void)
{
}

msf_pins msf_sample(void)
{
    msf_pins pins = {0, 0, 0};
    unsigned value;

    if (scanf("%x", &value) != 1)
        exit(0);
    pins.uo = (uint8_t)value;
    return pins;
}
