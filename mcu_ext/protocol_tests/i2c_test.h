#ifndef I2C_TEST_H
#define I2C_TEST_H

#include "protocol_test.h"

void I2C_Test_init(void);
TestStatus I2C_Test_run(void);
void I2C_Test_report(TestStatus status);

#endif
