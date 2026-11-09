#ifndef V3_LINES_H
#define V3_LINES_H

/*
 * Line signals for the v3 board test, drawn as an SPI bit stream (MOSI, MSB first,
 * 16-bit words). Plain C with no TI headers, so the PC builds the same code and the
 * simulation (test/test_mcu_v3_peer.py) drives the RTL with exactly these bits.
 *
 * Calibration: the FPGA (firmware/mcu_v3_peer.s) times V3_CAL_EDGES transitions,
 * 32 SPI bits apart, and returns T, the core cycles they took. One SPI bit is then
 * T / (32 x V3_CAL_EDGES) core cycles, and a line symbol of c core cycles is
 * c x 32 x V3_CAL_EDGES / T SPI bits, a fraction: each symbol edge goes to the
 * nearest SPI bit, so edges sit up to half an SPI bit (31 ns at 16 Mbit/s) from
 * where they belong, at random, and the average rate is exact.
 */
#include <stdint.h>

#define V3_CAL_EDGES   512U
#define V3_CAL_WORDS   (4U + 2U * (V3_CAL_EDGES + 1U) + 4U)
#define V3_LEAD_BITS   64U       /* idle (low) SPI bits before a frame or packet */
#define V3_TAIL_BITS   64U       /* and after */

#define V3_ETH_MAX     1518U     /* destination .. FCS */
#define V3_USB_BYTES   36U       /* SYNC, PID, 32 data bytes, CRC16 */
#define V3_USB_PID     0x4BU     /* DATA1 */

/* Core cycles a symbol, times 2: Manchester half-bit 2.5 (5 cycles a bit, as
 * 10BASE-T at 50 MHz), USB bit 4 (as full speed at 48 MHz). */
#define V3_ETH_HALF_X2 5U
#define V3_USB_BIT_X2  8U

/* Largest stream: a 1518-byte frame, 8 preamble/SFD bytes, 6 TP_IDL half-bits, at
 * up to 3.5 SPI bits a half-bit (a slow MCU clock), plus lead and tail. */
#define V3_MAX_WORDS   5800U

uint32_t v3_crc32(const uint8_t *data, uint32_t n);      /* Ethernet FCS (complemented) */
uint16_t v3_crc16_usb(const uint8_t *data, uint32_t n);  /* complemented, sent LSB first */

/* A pseudo-random sequence for payloads and phases (xorshift32; seed != 0). */
uint32_t v3_rand(uint32_t *state);

/* Calibration words: idle, then V3_CAL_EDGES + 1 transitions 32 bits apart, idle. */
uint32_t v3_cal_words(uint16_t *out);

/* SPI bits a symbol, in 16.16 fixed point, for a symbol of cycles_x2 / 2 core cycles
 * at a rate ppm parts per million fast (+) or slow (-), from the calibration's T.
 * 0 if T is 0. */
uint32_t v3_symbol_fp(uint32_t t_cal, uint32_t cycles_x2, int32_t ppm);

/* An Ethernet frame of n bytes (60 .. V3_ETH_MAX, FCS included): broadcast to this
 * test's address, type 0x88B5, random payload, its FCS; corrupt flips one bit after
 * the FCS was computed. Returns n. */
uint32_t v3_eth_frame(uint8_t *frame, uint32_t n, uint32_t *rng, int corrupt);

/* A USB DATA1 packet: SYNC, PID, 32 data bytes (random, with a run of ones that
 * needs stuffing), CRC16; corrupt flips one data bit after the CRC was computed. */
void v3_usb_packet(uint8_t *packet, uint32_t *rng, int corrupt);

/* The SPI words for a Manchester frame (IEEE 802.3: preamble 7 x 0x55, SFD 0xD5,
 * the frame LSB first, a 1 = low then high, TP_IDL after it) or a USB packet (NRZI,
 * a 0 stuffed after six 1s, EOP; drawn as D-, whose idle J is low). phase_fp: where
 * the first symbol starts within its SPI bit, 0 .. 65535. Returns the word count,
 * 0 if it would not fit in max_words. */
uint32_t v3_eth_words(const uint8_t *frame, uint32_t n, uint32_t sym_fp,
                      uint32_t phase_fp, uint16_t *out, uint32_t max_words);
uint32_t v3_usb_words(const uint8_t *packet, uint32_t sym_fp, uint32_t phase_fp,
                      uint16_t *out, uint32_t max_words);

#endif
