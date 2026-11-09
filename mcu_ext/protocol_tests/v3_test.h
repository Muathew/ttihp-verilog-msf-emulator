#ifndef V3_TEST_H
#define V3_TEST_H

#include <stdbool.h>
#include <stdint.h>

/*
 * The v3 board test, against firmware/mcu_v3_peer.s (fpga/ps/mcu_v3 loads it). That
 * peer prompts on the UART ('S' every 0.2 s while it waits), so the MCU tells which
 * FPGA program it faces without being told: main.c runs the v2 protocol passes
 * until the first 'S', and v3 passes from then on. external-mcu_testing.md says
 * what a pass does and how to read its log.
 */
void V3_Test_init(void);          /* SysTick, the prompt queue; listens 0.6 s for 'S' */
bool V3_Test_peerIsV3(void);      /* an 'S' has come: the FPGA runs the v3 peer */
void V3_Test_runPass(void);       /* one pass, then its log */

/* Every byte UART1 receives (uart_test.c's interrupt and its flush call this). */
void V3_Test_uartByte(uint8_t byte);

#endif
