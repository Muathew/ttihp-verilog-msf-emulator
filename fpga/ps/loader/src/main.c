// SPDX-License-Identifier: Apache-2.0
// Vitis standalone loader: programs firmware_image.h into the msfcore over
// the boot ROM's parallel protocol on PS EMIO GPIO (fpga/ps/common), starts
// it and hands the pins to the board (switches -> ui_in, PMOD JA <-> uio).
#include "firmware_image.h"
#include "msf_link.h"
#include "msf_log.h"

int main(void)
{
    int rc;

    if (FIRMWARE_LOAD_ADDRESS + FIRMWARE_WORD_COUNT > 1024U) {
        msf_printf("Firmware image exceeds the 1024-word address space.\r\n");
        return 1;
    }
    if (msf_link_init() != 0) {
        msf_printf("PS EMIO GPIO initialization failed.\r\n");
        return 1;
    }

    /* Select parallel boot and claim the pins before releasing core reset. */
    rc = msf_boot_enter(0);
    if (rc != MSF_OK) {
        msf_printf("Core reset/status did not initialize (%d).\r\n", rc);
        return 1;
    }

    msf_printf("Sending %d words at address %d over parallel boot.\r\n",
               (int)FIRMWARE_WORD_COUNT, (int)FIRMWARE_LOAD_ADDRESS);
    rc = msf_boot_write(FIRMWARE_LOAD_ADDRESS, firmware_words, FIRMWARE_WORD_COUNT);
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

    /* The core has left its loader; reconnect all bidirectional pins to JA. */
    msf_release_pins();
    msf_printf("Firmware is running; PMOD JA is connected to the emulator.\r\n");
    return 0;
}
