#include "ti_msp_dl_config.h"
#include "uart_test.h"
#include "v3_test.h"

#define UART_TEST_TIMEOUT  1000000U

#define UART_RX_ERRORS (DL_UART_MAIN_INTERRUPT_FRAMING_ERROR | \
                        DL_UART_MAIN_INTERRUPT_BREAK_ERROR |   \
                        DL_UART_MAIN_INTERRUPT_OVERRUN_ERROR)

static volatile uint8_t uartRxByte;
static volatile bool uartRxReady;
static volatile uint32_t uartRxCount;

static uint8_t uartTestByte;

/* What the last run saw, for UART_Test_report() */
static uint32_t uartSpins;
static uint32_t uartStray;
static uint32_t uartErrors;

void UART_Test_init(void)
{
    uartRxByte = 0;
    uartRxReady = false;
    uartRxCount = 0;
    uartTestByte = 0;

    NVIC_ClearPendingIRQ(UART_1_INST_INT_IRQN);
    NVIC_EnableIRQ(UART_1_INST_INT_IRQN);
}

TestStatus UART_Test_run(void)
{
    uint32_t timeout = UART_TEST_TIMEOUT;

    /*
     * Whatever came in since the last run is not the answer to this byte: RX
     * floats until the FPGA has loaded its firmware, and a late echo lands here.
     * The interrupt normally takes it, but read here too: on the board an MCU started
     * before the FPGA once timed out on every pass, every echo overrunning a byte
     * that no interrupt came for. The manual sets the interrupt when a byte fills the
     * empty register, so a byte left there can block the next, until it is read.
     */
    while (!DL_UART_Main_isRXFIFOEmpty(UART_1_INST)) {
        V3_Test_uartByte(DL_UART_Main_receiveData(UART_1_INST));
        uartRxCount++;
    }
    uartStray = uartRxCount;
    uartRxCount = 0;
    DL_UART_Main_clearInterruptStatus(UART_1_INST, UART_RX_ERRORS);

    uartRxReady = false;
    uartTestByte++;

    DL_UART_Main_transmitData(UART_1_INST, uartTestByte);

    while ((!uartRxReady) && (timeout > 0U)) {
        timeout--;
    }

    uartSpins = UART_TEST_TIMEOUT - timeout;
    uartErrors = DL_UART_Main_getRawInterruptStatus(UART_1_INST, UART_RX_ERRORS);
    uartRxCount = 0;

    if (timeout == 0U) {
        return TEST_TIMEOUT;
    }

    if (uartRxByte == uartTestByte) {
        return TEST_PASS;
    }

    return TEST_FAIL;
}

void UART_Test_report(TestStatus status)
{
    Log_str("MCU UART tx=");
    Log_hex8(uartTestByte);
    Log_str(" rx=");
    if (status == TEST_TIMEOUT) {
        Log_str("--");
    } else {
        Log_hex8(uartRxByte);
    }
    Log_str(" spins=");
    Log_dec(uartSpins);
    Log_str(" stray=");
    Log_dec(uartStray);
    if ((uartErrors & DL_UART_MAIN_INTERRUPT_FRAMING_ERROR) != 0U) {
        Log_str(" FRAMING-ERROR");
    }
    if ((uartErrors & DL_UART_MAIN_INTERRUPT_BREAK_ERROR) != 0U) {
        Log_str(" BREAK");
    }
    if ((uartErrors & DL_UART_MAIN_INTERRUPT_OVERRUN_ERROR) != 0U) {
        Log_str(" OVERRUN");
    }
    Log_str(" -> ");
    Log_status(status);
    Log_str("\r\n");
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
            uartRxCount++;
            V3_Test_uartByte(uartRxByte);   /* the v3 test reads every byte */
            break;

        default:
            break;
    }
}
