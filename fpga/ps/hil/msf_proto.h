// SPDX-License-Identifier: Apache-2.0
// Protocol models for the PS-side tests: CRCs, line decoders and peer
// (slave) models that advance one core cycle at a time. They are C ports of
// the cocotb models in test/test_firmware.py, test/msf_tb.py and
// test/golden_model.py, so a board run checks exactly what simulation checks.
//
// A peer model's *_cycle() takes the pins after a core cycle and returns the
// uio_in value to apply for the next one (pass it to msf_step()).
#ifndef MSF_PROTO_H
#define MSF_PROTO_H

#include <stdint.h>

#include "msf_link.h"

/* ---- CRCs (golden_model.py); msf_crc8() is in msf_link.h ---------------- */
uint16_t msf_usb_crc16(const uint8_t *d, uint32_t n);  /* sent value (~CRC) */
uint32_t msf_eth_fcs(const uint8_t *d, uint32_t n);    /* CRC-32 = FCS */

/* ---- deterministic PRNG (xorshift32) ----------------------------------- */
typedef struct { uint32_t s; } msf_rng;
void     msf_rng_seed(msf_rng *r, uint32_t seed);
uint32_t msf_rng_next(msf_rng *r);
uint32_t msf_rng_range(msf_rng *r, uint32_t lo, uint32_t hi);  /* [lo, hi) */

/* ---- per-cycle pin trace ------------------------------------------------ */
typedef struct {
    msf_pins *p;
    uint32_t n, cap;
    int overflow;
} msf_trace;
void msf_trace_init(msf_trace *t, msf_pins *buf, uint32_t cap);
void msf_trace_add(msf_trace *t, msf_pins p);

/* ---- UART 8N1 ----------------------------------------------------------- */
/* Decode frames on uio_out[pin] (msf_tb.uart_decode): out[i] = byte, or -1
 * for a bad stop bit. Returns the number of frames. */
uint32_t msf_uart_decode(const msf_trace *t, uint32_t bit_cycles, unsigned pin,
                         int *out, uint32_t max);
/* Level of uio_in[pin] while sending `msg` (msf_tb.uart_send): k = cycles
 * since the first start bit; idle (1) after the last stop bit. */
int      msf_uart_level(const uint8_t *msg, uint32_t n, uint32_t bit_cycles, uint32_t k);

/* ---- SPI slave, modes 0-3: SCK uio2, MOSI uio3, MISO uio4, CS uio5 ------ */
typedef struct {
    int cpol, cpha;
    const uint8_t *reply;
    uint32_t nreply, ireply;
    uint8_t got[64];
    uint32_t ngot;
    int prev_sck, prev_cs, nbits, miso;
    uint8_t tx, rx;
} msf_spi_slave;
void    msf_spi_slave_init(msf_spi_slave *s, int mode, const uint8_t *reply, uint32_t n);
uint8_t msf_spi_slave_cycle(msf_spi_slave *s, msf_pins p);

/* ---- I2C slave, wired-AND, optional clock stretching: SDA uio5, SCL uio6 */
typedef struct {
    uint8_t addr;
    const uint8_t *rdata;
    uint32_t nrdata, irdata;
    unsigned stretch;
    msf_rng rng;
    uint8_t written[64];
    uint32_t nwritten;
    char events[16];          /* "S" start, "P" stop, NUL terminated */
    uint32_t nevents;
    uint8_t master_acks[64];  /* 1 = the master ACKed a read byte */
    uint32_t nacks;
    int state, read_mode, bits, sda_low, hold, psda, pscl, prev_master_scl_low;
    unsigned byte;
} msf_i2c_slave;
void    msf_i2c_slave_init(msf_i2c_slave *s, uint8_t addr, const uint8_t *rdata,
                           uint32_t n, unsigned stretch, uint32_t seed);
uint8_t msf_i2c_slave_cycle(msf_i2c_slave *s, msf_pins p);

/* ---- USB low speed: D- uio0, D+ uio1 (J = D- high) ---------------------- */
char     msf_usb_state(msf_pins p);   /* 'J' 'K' '0' (SE0) '1' (SE1) 'z' (released) */
/* NRZI-decode, de-stuff and pack the packet starting at the first K
 * (cycle `start`, 8 cycles/bit). *eop = first SE0 cycle. 0 on success,
 * -1 if a stuffed bit is not 0. */
int      msf_usb_decode(const msf_trace *t, uint32_t start, uint8_t *bytes, uint32_t max,
                        uint32_t *nbytes, uint32_t *eop);
/* D- level per bit (1 = J) for sending `pkt` with bit stuffing; corrupt
 * flips stuffed bit 40 (test_fw_usb_ls_rx_packet). Returns the bit count. */
uint32_t msf_usb_levels(const uint8_t *pkt, uint32_t n, int corrupt, uint8_t *lv, uint32_t max);

/* ---- 10BASE-T: TD+ uio2, TD- uio3, one cycle per Manchester half-bit ---- */
/* Decode from the first active cycle until TP_IDL (equal half-bits).
 * *end = first TP_IDL cycle. 0 on success, -1 if TD- is not the complement
 * of TD+, -2 if the trace ends first. */
int      msf_eth_decode(const msf_trace *t, uint8_t *bytes, uint32_t max,
                        uint32_t *nbytes, uint32_t *end);

#endif
