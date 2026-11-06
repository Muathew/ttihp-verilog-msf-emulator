#ifndef SPI_TEST_H
#define SPI_TEST_H

#include "protocol_test.h"

void SPI_Test_init(void);
TestStatus SPI_Test_run(void);
void SPI_Test_report(TestStatus status);

#endif
