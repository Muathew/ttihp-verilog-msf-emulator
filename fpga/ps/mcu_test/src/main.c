// SPDX-License-Identifier: Apache-2.0
// Vitis standalone app: load the MSPM0 protocol-test peer into msfcore and
// connect all eight UIO pins to PMOD JA.
#include "firmware_image.h"
#include "msf_emio.h"
#include "msf_link.h"
#include "msf_log.h"

int main(void)
{
    int rc;
    uint8_t last_uart_byte = 0;
    int spi_error_reported = 0;

    if (FIRMWARE_LOAD_ADDRESS + FIRMWARE_WORD_COUNT > 1024U) {
        msf_printf("Firmware image exceeds the 1024-word address space.\r\n");
        return 1;
    }
    if (msf_link_init() != 0) {
        msf_printf("PS EMIO GPIO initialization failed.\r\n");
        return 1;
    }

    rc = msf_boot_enter(0);
    if (rc != MSF_OK) {
        msf_printf("Core reset/status did not initialize (%d).\r\n", rc);
        return 1;
    }

    msf_printf("Loading MSPM0 UART/I2C/SPI peer (%d words).\r\n",
               (int)FIRMWARE_WORD_COUNT);
    rc = msf_boot_write(FIRMWARE_LOAD_ADDRESS, firmware_words,
                        FIRMWARE_WORD_COUNT);
    if (rc != MSF_OK) {
        msf_printf(rc == MSF_ERR_NAK ? "Firmware write frame was rejected.\r\n"
                                     : "Firmware write frame timed out.\r\n");
        return 1;
    }
    rc = msf_boot_go(FIRMWARE_LOAD_ADDRESS);
    if (rc != MSF_OK) {
        msf_printf(rc == MSF_ERR_NAK ? "Firmware start frame was rejected.\r\n"
                                     : "Firmware start frame failed.\r\n");
        return 1;
    }

    msf_release_pins();
    msf_printf("PMOD JA now owns UIO[0:7]; run the MSPM0 tests.\r\n");
    msf_printf("Live FPGA UART monitor ready; send MSPM0 test bytes.\r\n");
    for (;;) {
        msf_pins pins = msf_sample();

        if ((pins.uo & 0x80U) != 0U) {
            if (!spi_error_reported) {
                msf_printf("FPGA SPI mismatch detected.\r\n");
                spi_error_reported = 1;
            }
        } else {
            spi_error_reported = 0;
            if (pins.uo != 0U && pins.uo != last_uart_byte) {
                last_uart_byte = pins.uo;
                msf_printf("FPGA UART RX/echo: 0x%02x\r\n",
                           (unsigned int)last_uart_byte);
            }
        }
        msf_emio_delay_us(1000U);
    }
}
