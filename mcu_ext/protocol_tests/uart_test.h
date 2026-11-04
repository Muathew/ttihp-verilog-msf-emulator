#ifndef UART_TEST_H
#define UART_TEST_H

#include "protocol_test.h"

void UART_Test_init(void);
TestStatus UART_Test_run(void);
void UART_Test_report(TestStatus status);

#endif
