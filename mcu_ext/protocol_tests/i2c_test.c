#include "ti_msp_dl_config.h"
#include "i2c_test.h"

#define I2C_TEST_ADDRESS  0x50U
#define I2C_TEST_REGISTER 0x00U
#define I2C_TEST_TIMEOUT  100000U

static uint8_t i2cReadByte;

void I2C_Test_init(void)
{
    i2cReadByte = 0;
}

TestStatus I2C_Test_run(void)
{
    uint32_t timeout;

    /*
     * Write the register address to the target.
     */
    DL_I2C_fillControllerTXFIFO(I2C_0_INST, &((uint8_t){I2C_TEST_REGISTER}), 1);

    DL_I2C_startControllerTransferAdvanced(
        I2C_0_INST,
        I2C_TEST_ADDRESS,
        DL_I2C_CONTROLLER_DIRECTION_TX,
        1,
        DL_I2C_CONTROLLER_START_ENABLE,
        DL_I2C_CONTROLLER_STOP_DISABLE,
        DL_I2C_CONTROLLER_ACK_DISABLE
    );

    timeout = I2C_TEST_TIMEOUT;

    while (((DL_I2C_getControllerStatus(I2C_0_INST) &
                DL_I2C_CONTROLLER_STATUS_BUSY) != 0U) &&
           (timeout > 0U)) {
        timeout--;
    }

    if (timeout == 0U) {
        return TEST_TIMEOUT;
    }

    /*
     * Read one byte from the selected register.
     */
    DL_I2C_startControllerTransferAdvanced(
        I2C_0_INST,
        I2C_TEST_ADDRESS,
        DL_I2C_CONTROLLER_DIRECTION_RX,
        1,
        DL_I2C_CONTROLLER_START_ENABLE,
        DL_I2C_CONTROLLER_STOP_ENABLE,
        DL_I2C_CONTROLLER_ACK_ENABLE
    );

    timeout = I2C_TEST_TIMEOUT;

    while (DL_I2C_isControllerRXFIFOEmpty(I2C_0_INST) &&
           ((DL_I2C_getControllerStatus(I2C_0_INST) &
                DL_I2C_CONTROLLER_STATUS_BUSY) != 0U) &&
           (timeout > 0U)) {
        timeout--;
    }

    if (timeout == 0U) {
        return TEST_TIMEOUT;
    }

    if (DL_I2C_isControllerRXFIFOEmpty(I2C_0_INST)) {
        return TEST_FAIL;
    }

    i2cReadByte = DL_I2C_receiveControllerData(I2C_0_INST);

    timeout = I2C_TEST_TIMEOUT;

    while (((DL_I2C_getControllerStatus(I2C_0_INST) &
                DL_I2C_CONTROLLER_STATUS_BUSY) != 0U) &&
           (timeout > 0U)) {
        timeout--;
    }

    if (timeout == 0U) {
        return TEST_TIMEOUT;
    }

    return (i2cReadByte == 0xA5U) ? TEST_PASS : TEST_FAIL;
}