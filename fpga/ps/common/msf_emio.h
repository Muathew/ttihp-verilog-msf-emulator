// SPDX-License-Identifier: Apache-2.0
// msfcore <-> Zynq PS link: the EMIO GPIO bank 3 bit map (must match
// fpga/rtl/aup_zu3_io.v) and the four-call HAL the link code runs on.
// HAL implementations: msf_emio_xgpiops.c (board, PS GPIO controller) and
// fpga/ps/sim/msf_emio_sim.c (host, in lockstep with the RTL in cocotb).
#ifndef MSF_EMIO_H
#define MSF_EMIO_H

#include <stdint.h>

/* PS -> PL */
#define MSF_O_UI_SHIFT   0U           /* [7:0]   ui_in while the PS owns the pins */
#define MSF_O_OWN        (1U << 8)    /* PS owns ui_in/uio_in; PMOD JA released */
#define MSF_O_RUN        (1U << 9)    /* core reset release */
#define MSF_O_STB        (1U << 10)   /* parallel-boot strobe, ORed into uio_in[1] */
#define MSF_O_STEP       (1U << 11)   /* step mode: core clock only on requests */
#define MSF_O_REQ        (1U << 12)   /* step request: each toggle = one core cycle */
#define MSF_O_UIO_SHIFT  13U          /* [20:13] uio_in while the PS owns the pins */
#define MSF_O_MASK       0x001fffffU  /* EMIO bits driven by the PS */

/* PL -> PS */
#define MSF_I_UO_SHIFT   3U           /* [10:3]  uo_out */
#define MSF_I_ACK        (1U << 11)   /* = REQ once the requested cycle has run */
#define MSF_I_OUT_SHIFT  12U          /* [19:12] uio_out */
#define MSF_I_OE_SHIFT   20U          /* [27:20] uio_oe */
#define MSF_I_RSTN       (1U << 28)   /* core out of reset */
#define MSF_I_STEP       (1U << 29)   /* step mode active */

int      msf_emio_init(void);             /* 0 on success; outputs start at 0 */
void     msf_emio_write(uint32_t value);  /* drive the MSF_O_MASK bits */
uint32_t msf_emio_read(void);             /* sample the inputs */
void     msf_emio_delay_us(uint32_t us);

#endif
