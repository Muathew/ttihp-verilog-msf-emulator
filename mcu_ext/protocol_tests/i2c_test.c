#include "ti_msp_dl_config.h"
#include "i2c_test.h"

#define I2C_TEST_ADDRESS  0x50U
#define I2C_TEST_REGISTER 0x00U
#define I2C_TEST_VALUE    0xA5U
#define I2C_TEST_TIMEOUT  100000U

/*
 * TI errata I2C_ERR_13: BUSY is not set straight after a transfer is started, so
 * polling it at once can see "finished" before anything happened. TI's examples
 * wait 3 I2C clock cycles; this is 32 at a 32 MHz I2C clock.
 */
#define I2C_BUSY_DELAY_CYCLES 32U

/* Controller status (MSR) at the three points of a read, for the log */
typedef enum {
    I2C_AT_START = 0,
    I2C_AFTER_WRITE,
    I2C_AFTER_READ,
    I2C_POINTS
} I2cPoint;

static const char *const i2cPointName[I2C_POINTS] = {
    "before", "write ", "read  "
};

static uint32_t i2cStatus[I2C_POINTS];
static bool i2cReached[I2C_POINTS];
static uint8_t i2cReadByte;
static bool i2cByteValid;
static uint32_t i2cStale;

void I2C_Test_init(void)
{
    i2cReadByte = 0;
    i2cByteValid = false;
    i2cStale = 0;
}

/* Note the status where a wait ended, and what it means. */
static TestStatus i2cResult(I2cPoint point, uint32_t status, bool ended)
{
    i2cStatus[point] = status;
    i2cReached[point] = true;

    if ((status & (DL_I2C_CONTROLLER_STATUS_ERROR |
                   DL_I2C_CONTROLLER_STATUS_ARBITRATION_LOST)) != 0U) {
        return TEST_FAIL;
    }

    return ended ? TEST_PASS : TEST_TIMEOUT;
}

/* Wait for the write just started to end: BUSY falls, with no STOP on the bus. */
static TestStatus i2cFinishWrite(void)
{
    uint32_t timeout = I2C_TEST_TIMEOUT;
    uint32_t status;

    delay_cycles(I2C_BUSY_DELAY_CYCLES);

    do {
        status = DL_I2C_getControllerStatus(I2C_0_INST);
        timeout--;
    } while (((status & DL_I2C_CONTROLLER_STATUS_BUSY) != 0U) && (timeout > 0U));

    return i2cResult(I2C_AFTER_WRITE, status,
                     (status & DL_I2C_CONTROLLER_STATUS_BUSY) == 0U);
}

/*
 * Wait for the read just started to end with its STOP. Not by BUSY: the manual
 * (SLAU846, "Bus Status Flags") has BUSY set only after the repeated START, about
 * 12 us after the read is started here, against the 1 us of the delay above. So BUSY
 * looked finished at once and the byte was looked for about 190 us too soon: on the
 * board that was rx=-- on pass 1, then each pass read the byte of the pass before.
 * IDLE stays clear from the end of the write until the STOP.
 */
static TestStatus i2cFinishRead(void)
{
    uint32_t timeout = I2C_TEST_TIMEOUT;
    uint32_t status;

    do {
        status = DL_I2C_getControllerStatus(I2C_0_INST);
        timeout--;
    } while (((status & (DL_I2C_CONTROLLER_STATUS_IDLE |
                         DL_I2C_CONTROLLER_STATUS_ERROR |
                         DL_I2C_CONTROLLER_STATUS_ARBITRATION_LOST)) == 0U) &&
             (timeout > 0U));

    return i2cResult(I2C_AFTER_READ, status,
                     (status & DL_I2C_CONTROLLER_STATUS_IDLE) != 0U);
}

/* After a failure: leave the bus and the controller ready for the next pass. */
static void i2cRecover(void)
{
    DL_I2C_flushControllerTXFIFO(I2C_0_INST);
    DL_I2C_flushControllerRXFIFO(I2C_0_INST);
    DL_I2C_disableController(I2C_0_INST);
    DL_I2C_resetControllerTransfer(I2C_0_INST);
    DL_I2C_enableController(I2C_0_INST);
}

TestStatus I2C_Test_run(void)
{
    uint8_t reg = I2C_TEST_REGISTER;
    uint32_t i;
    TestStatus status;

    i2cByteValid = false;
    for (i = 0; i < I2C_POINTS; i++) {
        i2cStatus[i] = 0;
        i2cReached[i] = false;
    }

    /* Whatever is still in the FIFO is not the answer to this read. */
    i2cStale = 0;
    while (!DL_I2C_isControllerRXFIFOEmpty(I2C_0_INST)) {
        (void) DL_I2C_receiveControllerData(I2C_0_INST);
        i2cStale++;
    }

    i2cStatus[I2C_AT_START] = DL_I2C_getControllerStatus(I2C_0_INST);
    i2cReached[I2C_AT_START] = true;

    /*
     * START, address + write, the register, and no STOP.
     */
    DL_I2C_fillControllerTXFIFO(I2C_0_INST, &reg, 1);

    DL_I2C_startControllerTransferAdvanced(
        I2C_0_INST,
        I2C_TEST_ADDRESS,
        DL_I2C_CONTROLLER_DIRECTION_TX,
        1,
        DL_I2C_CONTROLLER_START_ENABLE,
        DL_I2C_CONTROLLER_STOP_DISABLE,
        DL_I2C_CONTROLLER_ACK_DISABLE
    );

    status = i2cFinishWrite();
    if (status != TEST_PASS) {
        i2cRecover();
        return status;
    }

    /*
     * Repeated START, address + read, one byte, NACK, STOP.
     *
     * ACK_DISABLE is what makes that a NACK, whatever the comments in dl_i2c.h
     * say: they are the wrong way round. The register header hw_i2c.h has it right
     * (MCTR.ACK = 1: "the last received data byte is acknowledged"), and TI's plain
     * DL_I2C_startControllerTransfer() leaves the bit at 0 for its reads. ACKing
     * the last byte tells the target another is wanted, and its next bit can then
     * hold SDA low through the STOP.
     */
    DL_I2C_startControllerTransferAdvanced(
        I2C_0_INST,
        I2C_TEST_ADDRESS,
        DL_I2C_CONTROLLER_DIRECTION_RX,
        1,
        DL_I2C_CONTROLLER_START_ENABLE,
        DL_I2C_CONTROLLER_STOP_ENABLE,
        DL_I2C_CONTROLLER_ACK_DISABLE
    );

    status = i2cFinishRead();

    if (!DL_I2C_isControllerRXFIFOEmpty(I2C_0_INST)) {
        i2cReadByte = DL_I2C_receiveControllerData(I2C_0_INST);
        i2cByteValid = true;
    }

    if (status != TEST_PASS) {
        i2cRecover();
        return status;
    }

    if (!i2cByteValid) {
        return TEST_FAIL;
    }

    return (i2cReadByte == I2C_TEST_VALUE) ? TEST_PASS : TEST_FAIL;
}

static void logStatusFlags(uint32_t status)
{
    if ((status & DL_I2C_CONTROLLER_STATUS_BUSY) != 0U) {
        Log_str(" BUSY");
    }
    if ((status & DL_I2C_CONTROLLER_STATUS_ERROR) != 0U) {
        Log_str(" ERROR");
    }
    /* These two bits are set when the ACK did not come. */
    if ((status & DL_I2C_CONTROLLER_STATUS_ADDR_ACK) != 0U) {
        Log_str(" ADDRESS-NOT-ACKED");
    }
    if ((status & DL_I2C_CONTROLLER_STATUS_DATA_ACK) != 0U) {
        Log_str(" DATA-NOT-ACKED");
    }
    if ((status & DL_I2C_CONTROLLER_STATUS_ARBITRATION_LOST) != 0U) {
        Log_str(" ARBITRATION-LOST");
    }
    if ((status & DL_I2C_CONTROLLER_STATUS_IDLE) != 0U) {
        Log_str(" IDLE");
    }
    if ((status & DL_I2C_CONTROLLER_STATUS_BUSY_BUS) != 0U) {
        Log_str(" BUS-BUSY");
    }
}

void I2C_Test_report(TestStatus status)
{
    uint32_t i;

    for (i = 0; i < I2C_POINTS; i++) {
        if (i2cReached[i]) {
            Log_str("MCU I2C  ");
            Log_str(i2cPointName[i]);
            Log_str(" MSR=");
            Log_hex32(i2cStatus[i]);
            logStatusFlags(i2cStatus[i]);
            Log_str("\r\n");
        }
    }
    Log_str("MCU I2C  expect=");
    Log_hex8(I2C_TEST_VALUE);
    Log_str(" rx=");
    if (i2cByteValid) {
        Log_hex8(i2cReadByte);
    } else {
        Log_str("--");
    }
    Log_str(" stale=");
    Log_dec(i2cStale);
    Log_str(" -> ");
    Log_status(status);
    Log_str("\r\n");
}
