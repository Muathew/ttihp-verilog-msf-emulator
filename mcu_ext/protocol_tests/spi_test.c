#include "ti_msp_dl_config.h"
#include "spi_test.h"

#define SPI_TEST_TIMEOUT 100000U
#define SPI_TEST_LENGTH  8U

static const uint8_t spiTxData[SPI_TEST_LENGTH] = {
    0x55, 0xAA, 0x12, 0x34,
    0xDE, 0xAD, 0xBE, 0xEF
};

static uint8_t spiRxData[SPI_TEST_LENGTH];

void SPI_Test_init(void)
{
    uint32_t i;

    for (i = 0; i < SPI_TEST_LENGTH; i++) {
        spiRxData[i] = 0;
    }
}

TestStatus SPI_Test_run(void)
{
    uint32_t i;
    uint32_t timeout;
    uint8_t received;

    for (i = 0; i < SPI_TEST_LENGTH; i++) {

        timeout = SPI_TEST_TIMEOUT;

        while (DL_SPI_isTXFIFOFull(SPI_0_INST) && (timeout > 0U)) {
            timeout--;
        }

        if (timeout == 0U) {
            return TEST_TIMEOUT;
        }

        DL_SPI_transmitData8(SPI_0_INST, spiTxData[i]);

        timeout = SPI_TEST_TIMEOUT;

        while (DL_SPI_isRXFIFOEmpty(SPI_0_INST) && (timeout > 0U)) {
            timeout--;
        }

        if (timeout == 0U) {
            return TEST_TIMEOUT;
        }

        received = DL_SPI_receiveData8(SPI_0_INST);
        spiRxData[i] = received;

        if (received != spiTxData[i]) {
            return TEST_FAIL;
        }
    }

    return TEST_PASS;
}