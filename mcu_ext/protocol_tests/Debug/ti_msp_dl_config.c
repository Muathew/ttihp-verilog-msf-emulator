/*
 * Copyright (c) 2023, Texas Instruments Incorporated
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *
 * *  Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 *
 * *  Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * *  Neither the name of Texas Instruments Incorporated nor the names of
 *    its contributors may be used to endorse or promote products derived
 *    from this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
 * THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
 * PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR
 * CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
 * EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
 * PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
 * OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
 * WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
 * OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE,
 * EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

/*
 *  ============ ti_msp_dl_config.c =============
 *  Configured MSPM0 DriverLib module definitions
 *
 *  DO NOT EDIT - This file is generated for the MSPM0G351X
 *  by the SysConfig tool.
 */

#include "ti_msp_dl_config.h"

DL_SPI_backupConfig gSPI_0Backup;
DL_MCAN_backupConfig gMCAN0Backup;

/*
 *  ======== SYSCFG_DL_init ========
 *  Perform any initialization needed before using any board APIs
 */
SYSCONFIG_WEAK void SYSCFG_DL_init(void)
{
    SYSCFG_DL_initPower();
    SYSCFG_DL_GPIO_init();
    /* Module-Specific Initializations*/
    SYSCFG_DL_SYSCTL_init();
    SYSCFG_DL_I2C_0_init();
    SYSCFG_DL_UART_0_init();
    SYSCFG_DL_UART_1_init();
    SYSCFG_DL_SPI_0_init();
    SYSCFG_DL_MCAN0_init();
    /* Ensure backup structures have no valid state */

	gSPI_0Backup.backupRdy 	= false;
	gMCAN0Backup.backupRdy 	= false;

}
/*
 * User should take care to save and restore register configuration in application.
 * See Retention Configuration section for more details.
 */
SYSCONFIG_WEAK bool SYSCFG_DL_saveConfiguration(void)
{
    bool retStatus = true;

	retStatus &= DL_SPI_saveConfiguration(SPI_0_INST, &gSPI_0Backup);
	retStatus &= DL_MCAN_saveConfiguration(MCAN0_INST, &gMCAN0Backup);

    return retStatus;
}


SYSCONFIG_WEAK bool SYSCFG_DL_restoreConfiguration(void)
{
    bool retStatus = true;

	retStatus &= DL_SPI_restoreConfiguration(SPI_0_INST, &gSPI_0Backup);
	retStatus &= DL_MCAN_restoreConfiguration(MCAN0_INST, &gMCAN0Backup);

    return retStatus;
}

SYSCONFIG_WEAK void SYSCFG_DL_initPower(void)
{
    DL_GPIO_reset(GPIOA);
    DL_GPIO_reset(GPIOB);
    DL_GPIO_reset(GPIOC);
    DL_I2C_reset(I2C_0_INST);
    DL_UART_Main_reset(UART_0_INST);
    DL_UART_Main_reset(UART_1_INST);
    DL_SPI_reset(SPI_0_INST);
    DL_MCAN_reset(MCAN0_INST);

    DL_GPIO_enablePower(GPIOA);
    DL_GPIO_enablePower(GPIOB);
    DL_GPIO_enablePower(GPIOC);
    DL_I2C_enablePower(I2C_0_INST);
    DL_UART_Main_enablePower(UART_0_INST);
    DL_UART_Main_enablePower(UART_1_INST);
    DL_SPI_enablePower(SPI_0_INST);
    DL_MCAN_enablePower(MCAN0_INST);
    delay_cycles(POWER_STARTUP_DELAY);
}

SYSCONFIG_WEAK void SYSCFG_DL_GPIO_init(void)
{

    DL_GPIO_initPeripheralInputFunction(GPIO_HFCLKIN_IOMUX, GPIO_HFCLKIN_IOMUX_FUNC);

    DL_GPIO_initPeripheralInputFunctionFeatures(GPIO_I2C_0_IOMUX_SDA,
        GPIO_I2C_0_IOMUX_SDA_FUNC, DL_GPIO_INVERSION_DISABLE,
        DL_GPIO_RESISTOR_NONE, DL_GPIO_HYSTERESIS_DISABLE,
        DL_GPIO_WAKEUP_DISABLE);
    DL_GPIO_initPeripheralInputFunctionFeatures(GPIO_I2C_0_IOMUX_SCL,
        GPIO_I2C_0_IOMUX_SCL_FUNC, DL_GPIO_INVERSION_DISABLE,
        DL_GPIO_RESISTOR_NONE, DL_GPIO_HYSTERESIS_DISABLE,
        DL_GPIO_WAKEUP_DISABLE);
    DL_GPIO_enableHiZ(GPIO_I2C_0_IOMUX_SDA);
    DL_GPIO_enableHiZ(GPIO_I2C_0_IOMUX_SCL);

    DL_GPIO_initPeripheralOutputFunction(
        GPIO_UART_0_IOMUX_TX, GPIO_UART_0_IOMUX_TX_FUNC);
    DL_GPIO_initPeripheralInputFunction(
        GPIO_UART_0_IOMUX_RX, GPIO_UART_0_IOMUX_RX_FUNC);
    DL_GPIO_initPeripheralOutputFunction(
        GPIO_UART_1_IOMUX_TX, GPIO_UART_1_IOMUX_TX_FUNC);
    DL_GPIO_initPeripheralInputFunction(
        GPIO_UART_1_IOMUX_RX, GPIO_UART_1_IOMUX_RX_FUNC);

    DL_GPIO_initPeripheralOutputFunction(
        GPIO_SPI_0_IOMUX_SCLK, GPIO_SPI_0_IOMUX_SCLK_FUNC);
    DL_GPIO_initPeripheralOutputFunction(
        GPIO_SPI_0_IOMUX_PICO, GPIO_SPI_0_IOMUX_PICO_FUNC);
    DL_GPIO_initPeripheralInputFunction(
        GPIO_SPI_0_IOMUX_POCI, GPIO_SPI_0_IOMUX_POCI_FUNC);
    DL_GPIO_initPeripheralOutputFunction(
        GPIO_SPI_0_IOMUX_CS0, GPIO_SPI_0_IOMUX_CS0_FUNC);

    DL_GPIO_initPeripheralOutputFunction(
        GPIO_MCAN0_IOMUX_CAN_TX, GPIO_MCAN0_IOMUX_CAN_TX_FUNC);
    DL_GPIO_initPeripheralInputFunction(
        GPIO_MCAN0_IOMUX_CAN_RX, GPIO_MCAN0_IOMUX_CAN_RX_FUNC);

}


SYSCONFIG_WEAK void SYSCFG_DL_SYSCTL_init(void)
{

	//Low Power Mode is configured to be SLEEP0
    DL_SYSCTL_setBORThreshold(DL_SYSCTL_BOR_THRESHOLD_LEVEL_0);

    DL_SYSCTL_setSYSOSCFreq(DL_SYSCTL_SYSOSC_FREQ_BASE);
    /* Set default configuration */
    DL_SYSCTL_disableHFXT();
    DL_SYSCTL_disableSYSPLL();
    DL_SYSCTL_setHFCLKSourceHFCLKIN();
    DL_SYSCTL_setULPCLKDivider(DL_SYSCTL_ULPCLK_DIV_1);
    DL_SYSCTL_setMCLKDivider(DL_SYSCTL_MCLK_DIVIDER_DISABLE);

}


static const DL_I2C_ClockConfig gI2C_0ClockConfig = {
    .clockSel = DL_I2C_CLOCK_BUSCLK,
    .divideRatio = DL_I2C_CLOCK_DIVIDE_1,
};

SYSCONFIG_WEAK void SYSCFG_DL_I2C_0_init(void) {

    DL_I2C_setClockConfig(I2C_0_INST,
        (DL_I2C_ClockConfig *) &gI2C_0ClockConfig);
    DL_I2C_setAnalogGlitchFilterPulseWidth(I2C_0_INST,
        DL_I2C_ANALOG_GLITCH_FILTER_WIDTH_50NS);
    DL_I2C_enableAnalogGlitchFilter(I2C_0_INST);

    /* Configure Controller Mode */
    DL_I2C_resetControllerTransfer(I2C_0_INST);
    /* Set frequency to 100000 Hz*/
    DL_I2C_setTimerPeriod(I2C_0_INST, 31);
    DL_I2C_setControllerTXFIFOThreshold(I2C_0_INST, DL_I2C_TX_FIFO_LEVEL_EMPTY);
    DL_I2C_setControllerRXFIFOThreshold(I2C_0_INST, DL_I2C_RX_FIFO_LEVEL_BYTES_1);
    DL_I2C_enableControllerClockStretching(I2C_0_INST);


    /* Enable module */
    DL_I2C_enableController(I2C_0_INST);


}

static const DL_UART_Main_ClockConfig gUART_0ClockConfig = {
    .clockSel    = DL_UART_MAIN_CLOCK_BUSCLK,
    .divideRatio = DL_UART_MAIN_CLOCK_DIVIDE_RATIO_1
};

static const DL_UART_Main_Config gUART_0Config = {
    .mode        = DL_UART_MAIN_MODE_NORMAL,
    .direction   = DL_UART_MAIN_DIRECTION_TX_RX,
    .flowControl = DL_UART_MAIN_FLOW_CONTROL_NONE,
    .parity      = DL_UART_MAIN_PARITY_NONE,
    .wordLength  = DL_UART_MAIN_WORD_LENGTH_8_BITS,
    .stopBits    = DL_UART_MAIN_STOP_BITS_ONE
};

SYSCONFIG_WEAK void SYSCFG_DL_UART_0_init(void)
{
    DL_UART_Main_setClockConfig(UART_0_INST, (DL_UART_Main_ClockConfig *) &gUART_0ClockConfig);

    DL_UART_Main_init(UART_0_INST, (DL_UART_Main_Config *) &gUART_0Config);
    /*
     * Configure baud rate by setting oversampling and baud rate divisors.
     *  Target baud rate: 9600
     *  Actual baud rate: 9600.24
     */
    DL_UART_Main_setOversampling(UART_0_INST, DL_UART_OVERSAMPLING_RATE_16X);
    DL_UART_Main_setBaudRateDivisor(UART_0_INST, UART_0_IBRD_32_MHZ_9600_BAUD, UART_0_FBRD_32_MHZ_9600_BAUD);


    /* Configure Interrupts */
    DL_UART_Main_enableInterrupt(UART_0_INST,
                                 DL_UART_MAIN_INTERRUPT_RX);


    DL_UART_Main_enable(UART_0_INST);
}
static const DL_UART_Main_ClockConfig gUART_1ClockConfig = {
    .clockSel    = DL_UART_MAIN_CLOCK_BUSCLK,
    .divideRatio = DL_UART_MAIN_CLOCK_DIVIDE_RATIO_1
};

static const DL_UART_Main_Config gUART_1Config = {
    .mode        = DL_UART_MAIN_MODE_NORMAL,
    .direction   = DL_UART_MAIN_DIRECTION_TX_RX,
    .flowControl = DL_UART_MAIN_FLOW_CONTROL_NONE,
    .parity      = DL_UART_MAIN_PARITY_NONE,
    .wordLength  = DL_UART_MAIN_WORD_LENGTH_8_BITS,
    .stopBits    = DL_UART_MAIN_STOP_BITS_ONE
};

SYSCONFIG_WEAK void SYSCFG_DL_UART_1_init(void)
{
    DL_UART_Main_setClockConfig(UART_1_INST, (DL_UART_Main_ClockConfig *) &gUART_1ClockConfig);

    DL_UART_Main_init(UART_1_INST, (DL_UART_Main_Config *) &gUART_1Config);
    /*
     * Configure baud rate by setting oversampling and baud rate divisors.
     *  Target baud rate: 115200
     *  Actual baud rate: 115211.52
     */
    DL_UART_Main_setOversampling(UART_1_INST, DL_UART_OVERSAMPLING_RATE_16X);
    DL_UART_Main_setBaudRateDivisor(UART_1_INST, UART_1_IBRD_32_MHZ_115200_BAUD, UART_1_FBRD_32_MHZ_115200_BAUD);


    /* Configure Interrupts */
    DL_UART_Main_enableInterrupt(UART_1_INST,
                                 DL_UART_MAIN_INTERRUPT_RX);


    DL_UART_Main_enable(UART_1_INST);
}

static const DL_SPI_Config gSPI_0_config = {
    .mode        = DL_SPI_MODE_CONTROLLER,
    .frameFormat = DL_SPI_FRAME_FORMAT_MOTO4_POL0_PHA0,
    .parity      = DL_SPI_PARITY_NONE,
    .dataSize    = DL_SPI_DATA_SIZE_8,
    .bitOrder    = DL_SPI_BIT_ORDER_MSB_FIRST,
    .chipSelectPin = DL_SPI_CHIP_SELECT_0,
};

static const DL_SPI_ClockConfig gSPI_0_clockConfig = {
    .clockSel    = DL_SPI_CLOCK_BUSCLK,
    .divideRatio = DL_SPI_CLOCK_DIVIDE_RATIO_1
};

SYSCONFIG_WEAK void SYSCFG_DL_SPI_0_init(void) {
    DL_SPI_setClockConfig(SPI_0_INST, (DL_SPI_ClockConfig *) &gSPI_0_clockConfig);

    DL_SPI_init(SPI_0_INST, (DL_SPI_Config *) &gSPI_0_config);

    /* Configure Controller mode */
    /*
     * Set the bit rate clock divider to generate the serial output clock
     *     outputBitRate = (spiInputClock) / ((1 + SCR) * 2)
     *     1000000 = (32000000)/((1 + 15) * 2)
     */
    DL_SPI_setBitRateSerialClockDivider(SPI_0_INST, 15);
    /* Set RX and TX FIFO threshold levels */
    DL_SPI_setFIFOThreshold(SPI_0_INST, DL_SPI_RX_FIFO_LEVEL_1_2_FULL, DL_SPI_TX_FIFO_LEVEL_1_2_EMPTY);

    /* Enable module */
    DL_SPI_enable(SPI_0_INST);
}

static const DL_MCAN_ClockConfig gMCAN0ClockConf = {
    .clockSel = DL_MCAN_FCLK_HFCLK,
    .divider  = DL_MCAN_FCLK_DIV_1,
};

static const DL_MCAN_InitParams gMCAN0InitParams= {

/* Initialize MCAN Init parameters.    */
    .fdMode            = false,
    .brsEnable         = false,
    .txpEnable         = false,
    .efbi              = false,
    .pxhddisable       = false,
    .darEnable         = false,
    .wkupReqEnable     = false,
    .autoWkupEnable    = false,
    .emulationEnable   = false,
    .tdcEnable         = false,
    .wdcPreload        = 255,

/* Transmitter Delay Compensation parameters. */
    .tdcConfig.tdcf    = 10,
    .tdcConfig.tdco    = 6,
};


static const DL_MCAN_MsgRAMConfigParams gMCAN0MsgRAMConfigParams ={

    /* Standard ID Filter List Start Address. */
    .flssa                = MCAN0_INST_MCAN_STD_ID_FILT_START_ADDR,
    /* List Size: Standard ID. */
    .lss                  = MCAN0_INST_MCAN_STD_ID_FILTER_NUM,
    /* Extended ID Filter List Start Address. */
    .flesa                = MCAN0_INST_MCAN_EXT_ID_FILT_START_ADDR,
    /* List Size: Extended ID. */
    .lse                  = MCAN0_INST_MCAN_EXT_ID_FILTER_NUM,
    /* Tx Buffers Start Address. */
    .txStartAddr          = MCAN0_INST_MCAN_TX_BUFF_START_ADDR,
    /* Number of Dedicated Transmit Buffers. */
    .txBufNum             = MCAN0_INST_MCAN_TX_BUFF_SIZE,
    .txFIFOSize           = 0,
    /* Tx Buffer Element Size. */
    .txBufMode            = 0,
    .txBufElemSize        = DL_MCAN_ELEM_SIZE_64BYTES,
    /* Tx Event FIFO Start Address. */
    .txEventFIFOStartAddr = MCAN0_INST_MCAN_TX_EVENT_START_ADDR,
    /* Event FIFO Size. */
    .txEventFIFOSize      = MCAN0_INST_MCAN_TX_EVENT_SIZE,
    /* Level for Tx Event FIFO watermark interrupt. */
    .txEventFIFOWaterMark = 3,
    /* Rx FIFO0 Start Address. */
    .rxFIFO0startAddr     = MCAN0_INST_MCAN_FIFO_0_START_ADDR,
    /* Number of Rx FIFO elements. */
    .rxFIFO0size          = MCAN0_INST_MCAN_FIFO_0_NUM,
    /* Rx FIFO0 Watermark. */
    .rxFIFO0waterMark     = 3,
    .rxFIFO0OpMode        = 0,
    /* Rx FIFO1 Start Address. */
    .rxFIFO1startAddr     = MCAN0_INST_MCAN_FIFO_1_START_ADDR,
    /* Number of Rx FIFO elements. */
    .rxFIFO1size          = MCAN0_INST_MCAN_FIFO_1_NUM,
    /* Level for Rx FIFO 1 watermark interrupt. */
    .rxFIFO1waterMark     = 3,
    /* FIFO blocking mode. */
    .rxFIFO1OpMode        = 0,
    /* Rx Buffer Start Address. */
    .rxBufStartAddr       = MCAN0_INST_MCAN_RX_BUFF_START_ADDR,
    /* Rx Buffer Element Size. */
    .rxBufElemSize        = DL_MCAN_ELEM_SIZE_64BYTES,
    /* Rx FIFO0 Element Size. */
    .rxFIFO0ElemSize      = DL_MCAN_ELEM_SIZE_64BYTES,
    /* Rx FIFO1 Element Size. */
    .rxFIFO1ElemSize      = DL_MCAN_ELEM_SIZE_64BYTES,
};



static const DL_MCAN_BitTimingParams   gMCAN0BitTimes = {
    /* Arbitration Baud Rate Pre-scaler. */
    .nomRatePrescalar   = 0,
    /* Arbitration Time segment before sample point. */
    .nomTimeSeg1        = 62,
    /* Arbitration Time segment after sample point. */
    .nomTimeSeg2        = 15,
    /* Arbitration (Re)Synchronization Jump Width Range. */
    .nomSynchJumpWidth  = 15,
    /* Data Baud Rate Pre-scaler. */
    .dataRatePrescalar  = 0,
    /* Data Time segment before sample point. */
    .dataTimeSeg1       = 0,
    /* Data Time segment after sample point. */
    .dataTimeSeg2       = 0,
    /* Data (Re)Synchronization Jump Width.   */
    .dataSynchJumpWidth = 0,
};


SYSCONFIG_WEAK void SYSCFG_DL_MCAN0_init(void) {
    DL_MCAN_RevisionId revid_MCAN0;

    DL_MCAN_enableModuleClock(MCAN0_INST);

    DL_MCAN_setClockConfig(MCAN0_INST, (DL_MCAN_ClockConfig *) &gMCAN0ClockConf);

    /* Get MCANSS Revision ID. */
    DL_MCAN_getRevisionId(MCAN0_INST, &revid_MCAN0);

    /* Wait for Memory initialization to be completed. */
    while(false == DL_MCAN_isMemInitDone(MCAN0_INST));

    /* Put MCAN in SW initialization mode. */

    DL_MCAN_setOpMode(MCAN0_INST, DL_MCAN_OPERATION_MODE_SW_INIT);

    /* Wait till MCAN is not initialized. */
    while (DL_MCAN_OPERATION_MODE_SW_INIT != DL_MCAN_getOpMode(MCAN0_INST));

    /* Initialize MCAN module. */
    DL_MCAN_init(MCAN0_INST, (DL_MCAN_InitParams *) &gMCAN0InitParams);


    /* Configure Bit timings. */
    DL_MCAN_setBitTime(MCAN0_INST, (DL_MCAN_BitTimingParams*) &gMCAN0BitTimes);

    /* Configure Message RAM Sections */
    DL_MCAN_msgRAMConfig(MCAN0_INST, (DL_MCAN_MsgRAMConfigParams*) &gMCAN0MsgRAMConfigParams);



    /* Set Extended ID Mask. */
    DL_MCAN_setExtIDAndMask(MCAN0_INST, MCAN0_INST_MCAN_EXT_ID_AND_MASK );

    /* Loopback mode */

    /* Take MCAN out of the SW initialization mode */
    DL_MCAN_setOpMode(MCAN0_INST, DL_MCAN_OPERATION_MODE_NORMAL);

    while (DL_MCAN_OPERATION_MODE_NORMAL != DL_MCAN_getOpMode(MCAN0_INST));


}

