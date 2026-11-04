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

#endif