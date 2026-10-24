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

/* ---- UART host: a PC on a probe firmware's UART (RX uio1, TX uio0) ------ */
/* Sends queued bytes 8N1 (one idle bit between bytes) and decodes the
 * probe's replies. Each cycle: pass the pins, apply the returned level to
 * uio_in[1]. */
typedef struct {
    uint32_t bit;
    uint8_t tx[64];
    uint32_t ntx, itx, tcycle;
    int tbit;                      /* -1 idle, 0..9 frame bit, 10 the gap */
    uint8_t rx[64];
    uint32_t nrx, rcycle;
    int rbit, rprev;
    unsigned rbyte;
} msf_uart_host;
void msf_uart_host_init(msf_uart_host *u, uint32_t bit_cycles);
void msf_uart_host_send(msf_uart_host *u, const uint8_t *b, uint32_t n);
int  msf_uart_host_idle(const msf_uart_host *u);           /* all sent */
int  msf_uart_host_cycle(msf_uart_host *u, msf_pins p);    /* level for uio_in[1] */

/* ---- JTAG: up to two IEEE 1149.1 TAPs, TDI -> tap 0 -> tap 1 -> TDO;
 *      TCK uio2, TDI uio3, TDO uio4, TMS uio5 (test_debugports.Chain) ------- */
typedef struct {
    uint32_t idcode;
    int state, tdo;
    uint32_t sh, ir;
    int n;
} msf_jtag_tap;
typedef struct {
    msf_jtag_tap tap[2];
    int ntaps, tck, tms, tdi;
    uint32_t last_change, k, errors;
} msf_jtag_chain;
void    msf_jtag_init(msf_jtag_chain *c, const uint32_t *idcodes, int n);
uint8_t msf_jtag_cycle(msf_jtag_chain *c, msf_pins p);     /* uio_in TDO bit */

/* ---- 1-Wire: devices with 64-bit ROMs on DQ uio3, open drain
 *      (test_buses.OneWireDevice / OneWireBus) -------------------------------- */
typedef struct {
    uint8_t rom[8];
    uint8_t scratch[9];
    int state, si;
    uint8_t out[80];               /* bits still to send in read slots */
    uint32_t nout, iout;
    uint8_t bits[64];
    uint32_t nbits;
    uint32_t pull_from, pull_to;
} msf_ow_dev;
typedef struct {
    msf_ow_dev *dev;
    int ndev, chip_prev;
    uint32_t k, fall_at;
    int have_fall;
} msf_ow_bus;
uint8_t msf_crc8_maxim(const uint8_t *d, uint32_t n);
void    msf_ow_dev_init(msf_ow_dev *d, uint8_t family, uint64_t serial);
void    msf_ow_bus_init(msf_ow_bus *b, msf_ow_dev *devs, int n);
uint8_t msf_ow_bus_cycle(msf_ow_bus *b, msf_pins p);       /* uio_in DQ level (with pull-up) */

/* ---- SPI controller, modes 0-3, for the chip as a target: SCK uio2, MOSI
 *      uio3, CS uio5 driven, MISO uio4 sampled (test_targets.SpiController) */
#define MSF_SPI_WAVE 65536U
typedef struct {
    uint8_t wave[MSF_SPI_WAVE];    /* uio_in per cycle; bit 7: sample MISO here */
    uint32_t n, i;
    uint8_t miso[512];             /* the bits sampled, all transfers, MSB first */
    uint32_t nbits;
} msf_spi_ctl;
/* One CS-low frame per transfer: lens[t] bytes, back to back in `bytes`.
 * half = SCK half period, lead = CS low -> first edge, gap = CS high, in
 * core cycles. -1 if the waveform does not fit. */
int     msf_spi_ctl_init(msf_spi_ctl *c, int mode, const uint8_t *bytes, const uint8_t *lens,
                         uint32_t ntr, uint32_t half, uint32_t lead, uint32_t gap);
uint8_t msf_spi_ctl_cycle(msf_spi_ctl *c, msf_pins p);     /* uio_in for the next cycle */
int     msf_spi_ctl_done(const msf_spi_ctl *c);
uint8_t msf_spi_ctl_byte(const msf_spi_ctl *c, uint32_t k); /* k-th byte read, in order */

/* ---- I2C controller, open drain with clock stretching, for the chip as a
 *      target: SDA uio5, SCL uio6 (test_targets.I2cController) ------------ */
#define MSF_I2C_START    0x100U    /* ops: a START (repeated if SCL is low) */
#define MSF_I2C_STOP     0x200U
#define MSF_I2C_BYTE(v)  (0x300U | ((v) & 0xFFU))   /* write a byte, record the ACK */
#define MSF_I2C_READ(ack) (0x400U | ((ack) ? 1U : 0U)) /* read a byte, then ACK or NACK */
#define MSF_I2C_PROG 8192U
typedef struct {
    uint16_t prog[MSF_I2C_PROG];   /* micro-steps, op << 12 | argument */
    uint32_t n, pc, wait;
    int sda_low, scl_low, first;
    unsigned acc;
    uint8_t results[128];          /* per byte: the ACK bit seen (0 = ACK); per read: the byte */
    uint32_t nres, stretched, violations;
} msf_i2c_ctl;
/* tlow, thigh = SCL low and high; thd = SDA change after SCL falls; tss =
 * START/STOP setup and hold; tbuf = bus free after a STOP (core cycles).
 * -1 if the program does not fit. */
int     msf_i2c_ctl_init(msf_i2c_ctl *c, const uint16_t *ops, uint32_t nops, uint32_t tlow,
                         uint32_t thigh, uint32_t thd, uint32_t tss, uint32_t tbuf);
uint8_t msf_i2c_ctl_cycle(msf_i2c_ctl *c, msf_pins p);     /* uio_in SDA and SCL (pulled up) */
int     msf_i2c_ctl_done(const msf_i2c_ctl *c);

/* ---- SWD: an ADIv5 SW-DP with one MEM-AP; SWCLK uio2, SWDIO uio3 with a
 *      pull-up (test_debugports.SwdTarget) -------------------------------- */
#define MSF_SWD_DPIDR 0x2BA01477U
#define MSF_SWD_IDR   0x24770011U
typedef struct {
    uint32_t addr[8], val[8];      /* the target's memory, word by word */
    int nmem;
    uint32_t wait_drw;             /* DRW accesses still to answer WAIT */
    int clk, ones, mode, ready, phase, drive;  /* drive: -1 released */
    uint32_t hist;
    int bits[8], nbits, n, ap, rnw, a, ack;
    uint32_t data, word, select, csw, tar, ctrl, rdbuff, errors;
} msf_swd_target;
void     msf_swd_init(msf_swd_target *t, const uint32_t *addr, const uint32_t *val, int n,
                      uint32_t wait_drw);
uint8_t  msf_swd_cycle(msf_swd_target *t, msf_pins p);     /* uio_in SWDIO bit */
uint32_t msf_swd_mem(const msf_swd_target *t, uint32_t addr);

#endif
