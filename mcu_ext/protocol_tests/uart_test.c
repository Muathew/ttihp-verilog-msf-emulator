#include "ti_msp_dl_config.h"
#include "uart_test.h"

#define UART_TEST_TIMEOUT  1000000U

static volatile uint8_t uartRxByte;
static volatile bool uartRxReady;

static uint8_t uartTestByte;

void UART_Test_init(void)
{
    uartRxByte = 0;
    uartRxReady = false;
    uartTestByte = 0;

    NVIC_ClearPendingIRQ(UART_1_INST_INT_IRQN);
    NVIC_EnableIRQ(UART_1_INST_INT_IRQN);
}

TestStatus UART_Test_run(void)
{
    uint32_t timeout = UART_TEST_TIMEOUT;

    uartRxReady = false;
    uartTestByte++;

    DL_UART_Main_transmitData(UART_1_INST, uartTestByte);

    while ((!uartRxReady) && (timeout > 0U)) {
        timeout--;
    }

    if (timeout == 0U) {
        return TEST_TIMEOUT;
    }

    if (uartRxByte == uartTestByte) {
        return TEST_PASS;
    }

    return TEST_FAIL;
}

/*
 * The function name expands to UART1_IRQHandler through
 * UART_1_INST_IRQHandler in the generated configuration header.
 */
void UART_1_INST_IRQHandler(void)
{
    switch (DL_UART_Main_getPendingInterrupt(UART_1_INST)) {
        case DL_UART_MAIN_IIDX_RX:
            uartRxByte = DL_UART_Main_receiveDataBlocking(UART_1_INST);
            uartRxReady = true;
            break;

        default:
            break;
    }
}