// SPDX-License-Identifier: Apache-2.0
#ifndef MSF_TEST_XGPIO_H
#define MSF_TEST_XGPIO_H

#include <stdint.h>

typedef struct {
    uint32_t placeholder;
} XGpio;

int XGpio_Initialize(XGpio *instance, uint16_t device_id);
void XGpio_SetDataDirection(XGpio *instance, unsigned channel, uint32_t mask);
void XGpio_DiscreteWrite(XGpio *instance, unsigned channel, uint32_t value);
uint32_t XGpio_DiscreteRead(XGpio *instance, unsigned channel);

#endif
