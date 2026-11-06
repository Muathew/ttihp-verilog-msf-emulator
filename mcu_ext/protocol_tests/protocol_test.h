#ifndef PROTOCOL_TEST_H
#define PROTOCOL_TEST_H

#include <stdint.h>
#include <stdbool.h>

typedef enum {
    PROTOCOL_UART = 0,
    PROTOCOL_SPI,
    PROTOCOL_I2C,
    PROTOCOL_CAN,
    PROTOCOL_COUNT
} ProtocolType;

typedef enum {
    TEST_PASS = 0,
    TEST_FAIL,
    TEST_TIMEOUT,
    TEST_NOT_READY
} TestStatus;

typedef struct {
    uint32_t passed;
    uint32_t failed;
    uint32_t timeouts;
    uint32_t notReady;
    uint32_t transactions;
} TestResults;

extern TestResults protocolResults[PROTOCOL_COUNT];

void ProtocolTest_init(void);
void ProtocolTest_record(ProtocolType protocol, TestStatus status);
void ProtocolTest_runAll(void);

/*
 * Serial log on UART0 (PA10 TX, PA11 RX): the LaunchPad's back-channel UART,
 * the XDS110 "Application/User UART" COM port on the PC, 115200 8N1. The calls
 * block until the bytes are in the UART, so never call them during a test: at
 * 115200 a line takes milliseconds.
 */
void Log_str(const char *text);
void Log_hex8(uint8_t value);
void Log_hex32(uint32_t value);
void Log_dec(uint32_t value);
void Log_status(TestStatus status);

#endif
