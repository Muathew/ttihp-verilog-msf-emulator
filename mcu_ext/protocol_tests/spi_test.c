#include "ti_msp_dl_config.h"
#include "spi_test.h"

#define SPI_TEST_TIMEOUT 100000U
#define SPI_TEST_LENGTH  8U

/*
 * Between frames, from the end of one to the start of the next. The FPGA target
 * needs 10 us from a frame's last clock to the next frame's first to get its next
 * byte ready; 20 us leaves room.
 */
#define SPI_FRAME_GAP_CYCLES (CPUCLK_FREQ / 50000U)

static const uint8_t spiTxData[SPI_TEST_LENGTH] = {
    0x55, 0xAA, 0x12, 0x34,
    0xDE, 0xAD, 0xBE, 0xEF
};

/*
 * The FPGA target answers with the complement of each byte. Every bit differs from
 * what is on MOSI, so MISO can not pass by picking up MOSI from the wire next to it
 * (and the target's check of MOSI can not pass by picking up MISO).
 */
static const uint8_t spiExpectedRx[SPI_TEST_LENGTH] = {
    0xAA, 0x55, 0xED, 0xCB,
    0x21, 0x52, 0x41, 0x10
};

static uint8_t spiRxData[SPI_TEST_LENGTH];
static uint32_t spiRxCount;
static uint32_t spiStale;

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
    TestStatus status = TEST_PASS;

    spiRxCount = 0;
    spiStale = 0;
    while (!DL_SPI_isRXFIFOEmpty(SPI_0_INST)) {
        (void) DL_SPI_receiveData8(SPI_0_INST);
        spiStale++;
    }

    /*
     * Always all eight bytes, even after a wrong one: the target steps through
     * the same eight, so stopping early would leave the two out of step, and the
     * log shows the whole pattern (a shifted or stuck MISO is easy to see).
     */
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

        spiRxData[i] = DL_SPI_receiveData8(SPI_0_INST);
        spiRxCount++;

        if (spiRxData[i] != spiExpectedRx[i]) {
            status = TEST_FAIL;
        }

        timeout = SPI_TEST_TIMEOUT;

        while (DL_SPI_isBusy(SPI_0_INST) && (timeout > 0U)) {
            timeout--;
        }

        delay_cycles(SPI_FRAME_GAP_CYCLES);
    }

    return status;
}

static void logBytes(const char *label, const uint8_t *bytes)
{
    uint32_t i;

    Log_str(label);
    for (i = 0; i < SPI_TEST_LENGTH; i++) {
        Log_hex8(bytes[i]);
        Log_str(" ");
    }
    Log_str("\r\n");
}

void SPI_Test_report(TestStatus status)
{
    uint32_t i;

    logBytes("MCU SPI  tx=    ", spiTxData);
    logBytes("MCU SPI  expect=", spiExpectedRx);
    Log_str("MCU SPI  rx=    ");
    for (i = 0; i < SPI_TEST_LENGTH; i++) {
        if (i < spiRxCount) {
            Log_hex8(spiRxData[i]);
            Log_str(" ");
        } else {
            Log_str("-- ");
        }
    }
    Log_str("stale=");
    Log_dec(spiStale);
    Log_str(" -> ");
    Log_status(status);
    Log_str("\r\n");
}
