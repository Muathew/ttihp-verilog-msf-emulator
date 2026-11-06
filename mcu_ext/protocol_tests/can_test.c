#include "ti_msp_dl_config.h"
#include "can_test.h"

#define CAN_TEST_ID       0x123U
#define CAN_TEST_DLC      8U
#define CAN_TEST_TIMEOUT  1000000U
#define CAN_TX_BUFFER     0U

static const uint8_t canTxData[CAN_TEST_DLC] = {
    0x10, 0x20, 0x30, 0x40,
    0x50, 0x60, 0x70, 0x80
};

static bool canTestInitialised;

void CAN_Test_init(void)
{
    canTestInitialised = true;
}

TestStatus CAN_Test_run(void)
{
    uint32_t timeout;
    uint32_t txStatus;
    uint32_t i;

    DL_MCAN_TxBufElement txMessage = {0};
    DL_MCAN_RxBufElement rxMessage = {0};
    DL_MCAN_RxFIFOStatus fifoStatus = {0};

    if (!canTestInitialised) {
        return TEST_NOT_READY;
    }

    /*
     * Build a standard 11-bit Classical CAN data frame.
     * Standard IDs are shifted left by 18 bits in the MCAN
     * message RAM representation.
     */
    txMessage.id  = (CAN_TEST_ID << 18U);
    txMessage.rtr = 0U;
    txMessage.xtd = 0U;
    txMessage.esi = 0U;
    txMessage.dlc = CAN_TEST_DLC;
    txMessage.brs = 0U;
    txMessage.fdf = 0U;
    txMessage.efc = 0U;
    txMessage.mm  = 0U;

    for (i = 0; i < CAN_TEST_DLC; i++) {
        txMessage.data[i] = canTxData[i];
    }

    /*
     * Write the frame into TX buffer 0 and request transmission.
     */
    DL_MCAN_writeMsgRam(
        CANFD0,
        DL_MCAN_MEM_TYPE_BUF,
        CAN_TX_BUFFER,
        &txMessage
    );

    if (DL_MCAN_TXBufAddReq(CANFD0, CAN_TX_BUFFER) != 0) {
        return TEST_FAIL;
    }

    /*
     * Wait for the transmission-completed bit.
     */
    timeout = CAN_TEST_TIMEOUT;

    while (timeout > 0U) {
        txStatus = DL_MCAN_getTxBufTransmissionStatus(CANFD0);

        if ((txStatus & (1UL << CAN_TX_BUFFER)) != 0U) {
            break;
        }

        timeout--;
    }

    if (timeout == 0U) {
        return TEST_TIMEOUT;
    }

    /*
     * Wait for a response in RX FIFO 0.
     */
    timeout = CAN_TEST_TIMEOUT;

    do {
        fifoStatus.num = DL_MCAN_RX_FIFO_NUM_0;
        DL_MCAN_getRxFIFOStatus(CANFD0, &fifoStatus);

        if (fifoStatus.fillLvl > 0U) {
            break;
        }

        timeout--;
    } while (timeout > 0U);

    if (timeout == 0U) {
        return TEST_TIMEOUT;
    }

    /*
     * Read the received frame from FIFO 0.
     */
    DL_MCAN_readMsgRam(
        CANFD0,
        DL_MCAN_MEM_TYPE_FIFO,
        0U,
        DL_MCAN_RX_FIFO_NUM_0,
        &rxMessage
    );

    DL_MCAN_writeRxFIFOAck(
        CANFD0,
        DL_MCAN_RX_FIFO_NUM_0,
        fifoStatus.getIdx
    );

    /*
     * Validate identifier, frame format, length and payload.
     */
    if (rxMessage.xtd != 0U) {
        return TEST_FAIL;
    }

    if ((rxMessage.id >> 18U) != CAN_TEST_ID) {
        return TEST_FAIL;
    }

    if (rxMessage.dlc != CAN_TEST_DLC) {
        return TEST_FAIL;
    }

    for (i = 0; i < CAN_TEST_DLC; i++) {
        if ((uint8_t) rxMessage.data[i] != canTxData[i]) {
            return TEST_FAIL;
        }
    }

    return TEST_PASS;
}