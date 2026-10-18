// SPDX-License-Identifier: Apache-2.0
// msfcore link: reset, pin ownership and single-step control of the FPGA
// core, plus the boot ROM's parallel loader protocol (test/bootloader.s,
// docs/info.md "Memory map and boot").
#ifndef MSF_LINK_H
#define MSF_LINK_H

#include <stdint.h>

typedef struct {
    uint8_t uo;   /* uo_out  */
    uint8_t out;  /* uio_out */
    uint8_t oe;   /* uio_oe  */
} msf_pins;

/* ui_in at reset */
#define MSF_BOOT_DIRECT    0x01U  /* run the firmware already in SRAM at 128 */
#define MSF_BOOT_PARALLEL  0x02U  /* boot ROM, bytes on ui_in, strobe on uio[1] */

/* boot ROM status on uo_out */
#define MSF_ST_WAITING     0x01U
#define MSF_ST_FRAME_OK    0x02U
#define MSF_ST_FRAME_BAD   0x04U
#define MSF_ST_STARTING    0x08U
#define MSF_ST_PAR_ACK     0x80U

#define MSF_CMD_WRITE      0x57U  /* 'W' */
#define MSF_CMD_GO         0x47U  /* 'G' */

enum { MSF_OK = 0, MSF_ERR_TIMEOUT = -1, MSF_ERR_NAK = -2, MSF_ERR_ARG = -3 };

int      msf_link_init(void);
int      msf_link_fault(void);   /* 1 once a step request went unacknowledged */

/* Hold the core in reset with the PS owning the pins (ui_in = ui,
 * uio_in = uio). step != 0 selects step mode: the core clock then only runs
 * in msf_step(). The input synchronisers are clocked 5 times in reset. */
int      msf_reset(uint8_t ui, uint8_t uio, int step);
int      msf_run(void);                 /* release reset */
msf_pins msf_step(uint8_t uio);         /* one core cycle with uio_in = uio */
msf_pins msf_sample(void);              /* pins now (free run) */
void     msf_set_ui(uint8_t ui);
void     msf_release_pins(void);        /* ui_in <- switches, uio <-> PMOD JA */
/* Re-run the firmware already in SRAM (direct boot to 128, free run) and
 * hand the pins to the board. Nothing is reloaded. */
int      msf_restart(void);

/* Boot ROM, parallel byte source. Every call works in both modes. */
int      msf_boot_enter(int step);      /* reset into the ROM, wait for "waiting" */
int      msf_boot_write(uint16_t addr, const uint16_t *words, uint16_t count);
int      msf_boot_go(uint16_t addr);    /* 'G' frame + wait for "starting firmware" */
int      msf_boot_send(const uint8_t *bytes, uint32_t n);   /* raw bytes */
int      msf_boot_result(void);         /* MSF_OK, MSF_ERR_NAK or MSF_ERR_TIMEOUT */
uint32_t msf_boot_frame(uint8_t cmd, uint16_t addr, const uint16_t *words,
                        uint16_t count, uint8_t *buf, uint32_t cap);
uint8_t  msf_crc8(const uint8_t *data, uint32_t n);   /* CRC-8/0x07, init 0 */

#endif
