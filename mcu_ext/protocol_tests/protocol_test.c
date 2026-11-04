#include "protocol_test.h"

#include "uart_test.h"
#include "spi_test.h"
#include "i2c_test.h"

TestResults protocolResults[PROTOCOL_COUNT];

void ProtocolTest_init(void)
{
    uint32_t i;

    for (i = 0; i < PROTOCOL_COUNT; i++) {
        protocolResults[i].passed       = 0;
        protocolResults[i].failed       = 0;
        protocolResults[i].timeouts     = 0;
        protocolResults[i].notReady      = 0;
        protocolResults[i].transactions = 0;
    }
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
    ProtocolTest_record(PROTOCOL_UART, UART_Test_run());
    ProtocolTest_record(PROTOCOL_SPI,  SPI_Test_run());
    ProtocolTest_record(PROTOCOL_I2C,  I2C_Test_run());
}