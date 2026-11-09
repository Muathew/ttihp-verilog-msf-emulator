/*
 * Line signals for the v3 board test as SPI bit streams: see v3_lines.h. No TI
 * headers: test/test_mcu_v3_peer.py builds this file on the PC and drives the RTL
 * with what it makes.
 */
#include "v3_lines.h"

uint32_t v3_crc32(const uint8_t *data, uint32_t n)
{
    uint32_t crc = 0xFFFFFFFFU;
    uint32_t i;
    int b;

    for (i = 0; i < n; i++) {
        crc ^= data[i];
        for (b = 0; b < 8; b++) {
            crc = (crc & 1U) ? (crc >> 1) ^ 0xEDB88320U : crc >> 1;
        }
    }
    return crc ^ 0xFFFFFFFFU;
}

uint16_t v3_crc16_usb(const uint8_t *data, uint32_t n)
{
    uint32_t crc = 0xFFFFU;
    uint32_t i;
    int b;

    for (i = 0; i < n; i++) {
        crc ^= data[i];
        for (b = 0; b < 8; b++) {
            crc = (crc & 1U) ? (crc >> 1) ^ 0xA001U : crc >> 1;
        }
    }
    return (uint16_t) (crc ^ 0xFFFFU);
}

uint32_t v3_rand(uint32_t *state)
{
    uint32_t x = *state;

    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    *state = x;
    return x;
}

uint32_t v3_cal_words(uint16_t *out)
{
    uint32_t n = 0;
    uint32_t j;

    for (j = 0; j < 4U; j++) {
        out[n++] = 0x0000U;
    }
    /* Pair j starts with transition j: rising for even j. */
    for (j = 0; j <= V3_CAL_EDGES; j++) {
        uint16_t w = (j & 1U) ? 0x0000U : 0xFFFFU;
        out[n++] = w;
        out[n++] = w;
    }
    for (j = 0; j < 4U; j++) {
        out[n++] = 0x0000U;
    }
    return n;
}

uint32_t v3_symbol_fp(uint32_t t_cal, uint32_t cycles_x2, int32_t ppm)
{
    uint64_t num;
    uint64_t den;

    if (t_cal == 0U) {
        return 0U;
    }
    /* SPI bits a symbol = (cycles_x2 / 2) x 32 x V3_CAL_EDGES / T; x 65536. */
    num = ((uint64_t) cycles_x2 * 16U * V3_CAL_EDGES * 65536U) * 1000000U;
    den = (uint64_t) t_cal * (uint64_t) (1000000 + ppm);
    return (uint32_t) ((num + den / 2U) / den);
}

uint32_t v3_eth_frame(uint8_t *frame, uint32_t n, uint32_t *rng, int corrupt)
{
    static const uint8_t head[14] = {
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,       /* broadcast */
        0x02, 0x4D, 0x53, 0x46, 0x33, 0x00,       /* locally administered */
        0x88, 0xB5                                /* local experimental type */
    };
    uint32_t i;
    uint32_t fcs;

    for (i = 0; i < 14U; i++) {
        frame[i] = head[i];
    }
    for (i = 14; i < n - 4U; i++) {
        frame[i] = (uint8_t) v3_rand(rng);
    }
    fcs = v3_crc32(frame, n - 4U);
    for (i = 0; i < 4U; i++) {
        frame[n - 4U + i] = (uint8_t) (fcs >> (8U * i));
    }
    if (corrupt) {
        frame[20] ^= 0x10U;
    }
    return n;
}

void v3_usb_packet(uint8_t *packet, uint32_t *rng, int corrupt)
{
    uint32_t i;
    uint16_t crc;

    packet[0] = 0x80U;                            /* SYNC */
    packet[1] = V3_USB_PID;
    for (i = 2; i < 30U; i++) {
        packet[i] = (uint8_t) v3_rand(rng);
    }
    packet[30] = 0xFFU;                           /* ones: stuffed bits */
    packet[31] = 0xFFU;
    packet[32] = 0xFFU;
    packet[33] = 0x00U;
    crc = v3_crc16_usb(&packet[2], 32U);
    packet[34] = (uint8_t) crc;
    packet[35] = (uint8_t) (crc >> 8);
    if (corrupt) {
        packet[10] ^= 0x04U;
    }
}

/* Writes SPI bits: words are cleared as they are reached, 1s set. */
typedef struct {
    uint16_t *out;
    uint32_t max_words;
    uint32_t bit;                                 /* next bit to write */
    uint32_t words;                               /* words cleared so far */
    int full;
} BitWriter;

static void put_bits(BitWriter *w, uint32_t count, int level)
{
    while (count-- > 0U) {
        uint32_t word = w->bit >> 4;

        if (word >= w->max_words) {
            w->full = 1;
            return;
        }
        while (w->words <= word) {
            w->out[w->words++] = 0U;
        }
        if (level) {
            w->out[word] |= (uint16_t) (0x8000U >> (w->bit & 15U));
        }
        w->bit++;
    }
}

/* Symbol k spans SPI bits [LEAD + (phase + k sym) >> 16, LEAD + (phase + (k+1) sym) >> 16). */
typedef struct {
    BitWriter w;
    uint64_t pos;                                 /* 16.16, from the first symbol's start */
    uint32_t sym;
} Raster;

static void raster_start(Raster *r, uint16_t *out, uint32_t max_words,
                         uint32_t sym_fp, uint32_t phase_fp)
{
    r->w.out = out;
    r->w.max_words = max_words;
    r->w.bit = 0;
    r->w.words = 0;
    r->w.full = 0;
    r->sym = sym_fp;
    r->pos = phase_fp & 0xFFFFU;
    put_bits(&r->w, V3_LEAD_BITS + (uint32_t) (r->pos >> 16), 0);
}

static void raster_symbol(Raster *r, int level)
{
    uint32_t end;

    r->pos += r->sym;
    end = V3_LEAD_BITS + (uint32_t) (r->pos >> 16);
    if (end > r->w.bit) {
        put_bits(&r->w, end - r->w.bit, level);
    }
}

static uint32_t raster_end(Raster *r)
{
    put_bits(&r->w, V3_TAIL_BITS, 0);
    put_bits(&r->w, (16U - (r->w.bit & 15U)) & 15U, 0);
    return r->w.full ? 0U : r->w.words;
}

uint32_t v3_eth_words(const uint8_t *frame, uint32_t n, uint32_t sym_fp,
                      uint32_t phase_fp, uint16_t *out, uint32_t max_words)
{
    Raster r;
    uint32_t i;
    int b;

    if (sym_fp == 0U) {
        return 0U;
    }
    raster_start(&r, out, max_words, sym_fp, phase_fp);
    for (i = 0; i < 8U + n; i++) {
        uint8_t byte = (i < 7U) ? 0x55U : (i == 7U) ? 0xD5U : frame[i - 8U];

        for (b = 0; b < 8; b++) {
            int bit = (byte >> b) & 1;

            raster_symbol(&r, !bit);              /* a 1 is low, then high */
            raster_symbol(&r, bit);
        }
    }
    for (i = 0; i < 6U; i++) {                    /* TP_IDL: high 300 ns, then idle */
        raster_symbol(&r, 1);
    }
    return raster_end(&r);
}

uint32_t v3_usb_words(const uint8_t *packet, uint32_t sym_fp, uint32_t phase_fp,
                      uint16_t *out, uint32_t max_words)
{
    Raster r;
    uint32_t i;
    int b;
    int ones = 0;
    int k = 0;                                    /* the line is K (D- high) */

    if (sym_fp == 0U) {
        return 0U;
    }
    raster_start(&r, out, max_words, sym_fp, phase_fp);
    for (i = 0; i < V3_USB_BYTES; i++) {
        for (b = 0; b < 8; b++) {
            int bit = (packet[i] >> b) & 1;

            if (!bit) {
                k = !k;                           /* NRZI: a 0 is a transition */
            }
            raster_symbol(&r, k);
            ones = bit ? ones + 1 : 0;
            if (ones == 6) {                      /* stuff a 0 */
                k = !k;
                raster_symbol(&r, k);
                ones = 0;
            }
        }
    }
    raster_symbol(&r, 0);                         /* EOP: SE0, SE0 (D- low), J */
    raster_symbol(&r, 0);
    raster_symbol(&r, 0);
    return raster_end(&r);
}
