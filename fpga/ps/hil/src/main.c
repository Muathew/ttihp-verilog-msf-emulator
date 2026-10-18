// SPDX-License-Identifier: Apache-2.0
// Vitis standalone app: run the msfcore protocol tests on the FPGA core
// (step mode over PS EMIO GPIO) and report on the PS UART.
#include "msf_log.h"
#include "msf_tests.h"

int main(void)
{
    int failed;

    msf_printf("\r\nmsfcore FPGA protocol tests\r\n");
    failed = msf_tests_run(0);
    if (failed == 0)
        msf_printf("ALL PASSED\r\n");
    else
        msf_printf("FAILED: %d\r\n", failed);
    return failed == 0 ? 0 : 1;
}
