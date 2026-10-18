// SPDX-License-Identifier: Apache-2.0
// Board HAL: PS GPIO controller, EMIO bank 3 (EMIO 0-31 = GPIO 78-109).
#include "msf_emio.h"

#include <stddef.h>

#include "sleep.h"
#include "xgpiops.h"
#include "xparameters.h"
#include "xstatus.h"

#define MSF_EMIO_BANK 3U

static XGpioPs gpio;

int msf_emio_init(void)
{
    XGpioPs_Config *cfg;

#ifndef SDT
    cfg = XGpioPs_LookupConfig(XPAR_XGPIOPS_0_DEVICE_ID);
#else
    cfg = XGpioPs_LookupConfig(XPAR_XGPIOPS_0_BASEADDR);
#endif
    if (cfg == NULL ||
        XGpioPs_CfgInitialize(&gpio, cfg, cfg->BaseAddr) != XST_SUCCESS)
        return -1;

    /* Core in reset, PMOD connected: set the data before enabling outputs. */
    XGpioPs_Write(&gpio, MSF_EMIO_BANK, 0U);
    XGpioPs_SetDirection(&gpio, MSF_EMIO_BANK, MSF_O_MASK);
    XGpioPs_SetOutputEnable(&gpio, MSF_EMIO_BANK, MSF_O_MASK);
    return 0;
}

void msf_emio_write(uint32_t value)
{
    XGpioPs_Write(&gpio, MSF_EMIO_BANK, value & MSF_O_MASK);
}

uint32_t msf_emio_read(void)
{
    return XGpioPs_Read(&gpio, MSF_EMIO_BANK);
}

void msf_emio_delay_us(uint32_t us)
{
    usleep(us);
}
