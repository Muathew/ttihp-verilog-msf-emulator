#include "ti_msp_dl_config.h"

#include "protocol_test.h"

#include "uart_test.h"
#include "spi_test.h"
#include "i2c_test.h"

TestResults protocolResults[PROTOCOL_COUNT];

static uint32_t passNumber;

void Log_str(const char *text)
{
    while (*text != '\0') {
        DL_UART_Main_transmitDataBlocking(UART_0_INST, (uint8_t) *text);
        text++;
    }
}

void Log_hex8(uint8_t value)
{
    static const char digits[] = "0123456789ABCDEF";

    DL_UART_Main_transmitDataBlocking(UART_0_INST, (uint8_t) digits[value >> 4]);
    DL_UART_Main_transmitDataBlocking(UART_0_INST, (uint8_t) digits[value & 0x0FU]);
}

void Log_hex32(uint32_t value)
{
    Log_hex8((uint8_t) (value >> 24));
    Log_hex8((uint8_t) (value >> 16));
    Log_hex8((uint8_t) (value >> 8));
    Log_hex8((uint8_t) value);
}

void Log_dec(uint32_t value)
{
    char text[11];
    uint32_t i = sizeof(text) - 1U;

    text[i] = '\0';
    do {
        i--;
        text[i] = (char) ('0' + (value % 10U));
        value /= 10U;
    } while (value != 0U);
    Log_str(&text[i]);
}

void Log_status(TestStatus status)
{
    switch (status) {
        case TEST_PASS:
            Log_str("PASS");
            break;
        case TEST_FAIL:
            Log_str("FAIL");
            break;
        case TEST_TIMEOUT:
            Log_str("TIMEOUT");
            break;
        default:
            Log_str("NOT READY");
            break;
    }
}

static void logTotals(const char *name, ProtocolType protocol)
{
    Log_str(name);
    Log_str(" pass=");
    Log_dec(protocolResults[protocol].passed);
    Log_str(" fail=");
    Log_dec(protocolResults[protocol].failed);
    Log_str(" timeout=");
    Log_dec(protocolResults[protocol].timeouts);
}

void ProtocolTest_init(void)
{
    uint32_t i;

    for (i = 0; i < PROTOCOL_COUNT; i++) {
        protocolResults[i].passed       = 0;
        protocolResults[i].failed       = 0;
        protocolResults[i].timeouts     = 0;
        protocolResults[i].notReady     = 0;
        protocolResults[i].transactions = 0;
    }
    passNumber = 0;

    /*
     * The rates are read back from the hardware: a wrong figure here means
     * SysConfig did not regenerate the project after p_test.syscfg changed.
     */
    Log_str("\r\n\r\nMCU ==== protocol test, built " __DATE__ " " __TIME__ " ====\r\n");
    Log_str("MCU SPI ");
    Log_dec((CPUCLK_FREQ / 2000U) /
            (DL_SPI_getBitRateSerialClockDivider(SPI_0_INST) + 1U));
    Log_str(" kHz (the FPGA target needs at most 375), I2C ");
    Log_dec((CPUCLK_FREQ / 10000U) /
            ((uint32_t) DL_I2C_getTimerPeriod(I2C_0_INST) + 1U));
    Log_str(" kHz, CPU ");
    Log_dec(CPUCLK_FREQ / 1000000U);
    Log_str(" MHz\r\n");
}

void ProtocolTest_record(ProtocolType protocol, TestStatus status)
{
    if (protocol >= PROTOCOL_COUNT) {
        return;
    }

    switch (status) {
        case TEST_PASS:
            protocolResults[protocol].passed++;
            protocolResults[protocol].transactions++;
            break;

        case TEST_FAIL:
            protocolResults[protocol].failed++;
            protocolResults[protocol].transactions++;
            break;

        case TEST_TIMEOUT:
            protocolResults[protocol].timeouts++;
            protocolResults[protocol].transactions++;
            break;

        case TEST_NOT_READY:
        default:
            protocolResults[protocol].notReady++;
            break;
    }
}

void ProtocolTest_runAll(void)
{
    TestStatus uart;
    TestStatus spi;
    TestStatus i2c;

    /*
     * All three first, then the log, so a pass on the wires is one short burst
     * that the FPGA's logic analyser can record whole.
     */
    uart = UART_Test_run();
    spi  = SPI_Test_run();
    i2c  = I2C_Test_run();

    ProtocolTest_record(PROTOCOL_UART, uart);
    ProtocolTest_record(PROTOCOL_SPI, spi);
    ProtocolTest_record(PROTOCOL_I2C, i2c);

    passNumber++;
    Log_str("MCU ---- pass ");
    Log_dec(passNumber);
    Log_str(" ----\r\n");
    UART_Test_report(uart);
    SPI_Test_report(spi);
    I2C_Test_report(i2c);
    Log_str("MCU totals:");
    logTotals(" UART", PROTOCOL_UART);
    logTotals(" | SPI", PROTOCOL_SPI);
    logTotals(" | I2C", PROTOCOL_I2C);
    Log_str("\r\n");
}
