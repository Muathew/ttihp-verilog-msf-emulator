// SPDX-License-Identifier: Apache-2.0
// Compile-check stub for the Vitis standalone XGpioPs driver (the subset the
// PS code uses). LookupConfig takes a device ID in the classic flow and a
// base address in the system-device-tree (SDT) flow.
#ifndef MSF_TEST_XGPIOPS_H
#define MSF_TEST_XGPIOPS_H

#include <stdint.h>

typedef struct {
    uint16_t DeviceId;
    uintptr_t BaseAddr;
} XGpioPs_Config;

typedef struct {
    XGpioPs_Config GpioConfig;
    uint32_t IsReady;
} XGpioPs;

#ifndef SDT
XGpioPs_Config *XGpioPs_LookupConfig(uint16_t DeviceId);
#else
XGpioPs_Config *XGpioPs_LookupConfig(uintptr_t BaseAddress);
#endif
int32_t XGpioPs_CfgInitialize(XGpioPs *InstancePtr, const XGpioPs_Config *ConfigPtr,
                              uint32_t EffectiveAddr);
void XGpioPs_Write(const XGpioPs *InstancePtr, uint8_t Bank, uint32_t Data);
uint32_t XGpioPs_Read(const XGpioPs *InstancePtr, uint8_t Bank);
void XGpioPs_SetDirection(const XGpioPs *InstancePtr, uint8_t Bank, uint32_t Direction);
void XGpioPs_SetOutputEnable(const XGpioPs *InstancePtr, uint8_t Bank, uint32_t OpEnable);

#endif
