// SPDX-License-Identifier: Apache-2.0
// Protocol models (see msf_proto.h). Each function names the Python model it
// ports; keep the two in step when either changes.
#include "msf_proto.h"

/* ---- CRCs ---------------------------------------------------------------- */

uint16_t msf_usb_crc16(const uint8_t *d, uint32_t n)
{
    uint16_t crc = 0xffffU;
    uint32_t i;
    unsigned b;

    for (i = 0; i < n; ++i) {
        crc ^= d[i];
        for (b = 0; b < 8U; ++b)
            crc = (crc & 1U) ? (uint16_t)((crc >> 1) ^ 0xa001U) : (uint16_t)(crc >> 1);
    }
    return (uint16_t)(crc ^ 0xffffU);
}

uint32_t msf_eth_fcs(const uint8_t *d, uint32_t n)
{
    uint32_t crc = 0xffffffffUL;
    uint32_t i;
    unsigned b;

    for (i = 0; i < n; ++i) {
        crc ^= d[i];
        for (b = 0; b < 8U; ++b)
            crc = (crc & 1U) ? (crc >> 1) ^ 0xedb88320UL : crc >> 1;
    }
    return ~crc;
}

/* ---- PRNG ---------------------------------------------------------------- */

void msf_rng_seed(msf_rng *r, uint32_t seed)
{
    r->s = seed * 2654435761UL + 0x9e3779b9UL;
    if (r->s == 0U)
        r->s = 1U;
}

uint32_t msf_rng_next(msf_rng *r)
{
    uint32_t x = r->s;

    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    r->s = x;
    return x;
}

uint32_t msf_rng_range(msf_rng *r, uint32_t lo, uint32_t hi)
{
    return lo + msf_rng_next(r) % (hi - lo);
}

/* ---- trace --------------------------------------------------------------- */

void msf_trace_init(msf_trace *t, msf_pins *buf, uint32_t cap)
{
    t->p = buf;
    t->n = 0;
    t->cap = cap;
    t->overflow = 0;
}

void msf_trace_add(msf_trace *t, msf_pins p)
{
    if (t->n < t->cap)
        t->p[t->n++] = p;
    else
        t->overflow = 1;
}

/* ---- UART (msf_tb.uart_decode / uart_send) ------------------------------- */

static int out_bit(const msf_trace *t, uint32_t i, unsigned pin)
{
    return (t->p[i].out >> pin) & 1;
}

uint32_t msf_uart_decode(const msf_trace *t, uint32_t bit_cycles, unsigned pin,
                         int *out, uint32_t max)
{
    uint32_t n = 0, i = 0;

    while (t->n > 0 && i < t->n - 1U && n < max) {
        if (out_bit(t, i, pin) == 1 && out_bit(t, i + 1U, pin) == 0) {
            uint32_t mid = i + 1U + bit_cycles / 2U;
            int v = 0;
            unsigned b;

            if (mid + 9U * bit_cycles >= t->n)
                break;
            if (out_bit(t, mid, pin) != 0) {
                ++i;
                continue;
            }
            for (b = 0; b < 8U; ++b)
                v |= out_bit(t, mid + (b + 1U) * bit_cycles, pin) << b;
            out[n++] = out_bit(t, mid + 9U * bit_cycles, pin) == 1 ? v : -1;
            i = mid + 9U * bit_cycles;
        } else {
            ++i;
        }
    }
    return n;
}

int msf_uart_level(const uint8_t *msg, uint32_t n, uint32_t bit_cycles, uint32_t k)
{
    uint32_t byte = k / (10U * bit_cycles);
    uint32_t bit = (k % (10U * bit_cycles)) / bit_cycles;

    if (byte >= n || bit == 9U)
        return 1;
    if (bit == 0U)
        return 0;
    return (msg[byte] >> (bit - 1U)) & 1;
}

/* ---- SPI slave (test_firmware.SpiSlave) ---------------------------------- */

static uint8_t spi_next(msf_spi_slave *s)
{
    return s->ireply < s->nreply ? s->reply[s->ireply++] : 0U;
}

void msf_spi_slave_init(msf_spi_slave *s, int mode, const uint8_t *reply, uint32_t n)
{
    s->cpol = (mode >> 1) & 1;
    s->cpha = mode & 1;
    s->reply = reply;
    s->nreply = n;
    s->ireply = 0;
    s->ngot = 0;
    s->prev_sck = s->cpol;
    s->prev_cs = 1;
    s->nbits = 0;
    s->miso = 0;
    s->tx = 0;
    s->rx = 0;
}

uint8_t msf_spi_slave_cycle(msf_spi_slave *s, msf_pins p)
{
    int sck = (p.out >> 2) & 1, mosi = (p.out >> 3) & 1;
    int cs = ((p.oe >> 5) & 1) ? (p.out >> 5) & 1 : 1;   /* pulled up until driven */

    if (s->prev_cs && !cs) {                             /* select: load first byte */
        s->tx = spi_next(s);
        s->nbits = 0;
        s->rx = 0;
        if (!s->cpha)
            s->miso = (s->tx >> 7) & 1;
    }
    if (!cs && sck != s->prev_sck) {
        int leading = sck != s->cpol;
        int sample = s->cpha ? !leading : leading;

        if (sample) {
            s->rx = (uint8_t)((s->rx << 1) | mosi);
            if (++s->nbits == 8) {
                if (s->ngot < sizeof s->got)
                    s->got[s->ngot++] = s->rx;
                s->rx = 0;
                s->nbits = 0;
                s->tx = spi_next(s);
            }
        } else {
            s->miso = (s->tx >> (7 - s->nbits)) & 1;     /* shift edge: next bit out */
        }
    }
    s->prev_sck = sck;
    s->prev_cs = cs;
    return (uint8_t)(s->miso << 4);
}

/* ---- I2C slave (test_firmware.I2cSlave) ---------------------------------- */

enum { I2C_IDLE, I2C_ADDR, I2C_WDATA, I2C_ACK, I2C_NACK, I2C_RDATA, I2C_MACK };

void msf_i2c_slave_init(msf_i2c_slave *s, uint8_t addr, const uint8_t *rdata,
                        uint32_t n, unsigned stretch, uint32_t seed)
{
    s->addr = addr;
    s->rdata = rdata;
    s->nrdata = n;
    s->irdata = 0;
    s->stretch = stretch;
    msf_rng_seed(&s->rng, seed);
    s->nwritten = 0;
    s->events[0] = '\0';
    s->nevents = 0;
    s->nacks = 0;
    s->state = I2C_IDLE;
    s->read_mode = 0;
    s->bits = 0;
    s->sda_low = 0;
    s->hold = 0;
    s->psda = 1;
    s->pscl = 1;
    s->prev_master_scl_low = 0;
    s->byte = 0;
}

static void i2c_event(msf_i2c_slave *s, char e)
{
    if (s->nevents + 1U < sizeof s->events) {
        s->events[s->nevents++] = e;
        s->events[s->nevents] = '\0';
    }
}

static void i2c_load_read_byte(msf_i2c_slave *s)
{
    s->state = I2C_RDATA;
    s->bits = 0;
    s->byte = s->irdata < s->nrdata ? s->rdata[s->irdata++] : 0xffU;
    s->sda_low = !((s->byte >> 7) & 1U);
}

uint8_t msf_i2c_slave_cycle(msf_i2c_slave *s, msf_pins p)
{
    int m_sda_low = (p.oe >> 5) & 1, m_scl_low = (p.oe >> 6) & 1;
    int scl_hold, sda, scl;

    /* clock stretching: when the master releases SCL, sometimes hold it */
    if (s->prev_master_scl_low && !m_scl_low && s->stretch &&
        msf_rng_next(&s->rng) % 1000U < 300U)
        s->hold = 1 + (int)(msf_rng_next(&s->rng) % s->stretch);
    s->prev_master_scl_low = m_scl_low;
    scl_hold = s->hold > 0;
    if (s->hold)
        s->hold--;
    sda = (m_sda_low || s->sda_low) ? 0 : 1;
    scl = (m_scl_low || scl_hold) ? 0 : 1;

    if (scl && s->pscl && s->psda && !sda) {
        i2c_event(s, 'S');
        s->state = I2C_ADDR;
        s->byte = 0;
        s->bits = 0;
        s->sda_low = 0;
    } else if (scl && s->pscl && !s->psda && sda) {
        i2c_event(s, 'P');
        s->state = I2C_IDLE;
        s->sda_low = 0;
    } else if (scl && !s->pscl) {                          /* SCL rising: sample */
        if (s->state == I2C_ADDR || s->state == I2C_WDATA) {
            s->byte = (s->byte << 1) | (unsigned)sda;
            s->bits++;
        } else if (s->state == I2C_MACK && s->nacks < sizeof s->master_acks) {
            s->master_acks[s->nacks++] = (uint8_t)(sda == 0);
        }
    } else if (s->pscl && !scl) {                          /* SCL falling: drive */
        if ((s->state == I2C_ADDR || s->state == I2C_WDATA) && s->bits == 8) {
            int ok = 1;
            if (s->state == I2C_ADDR) {
                ok = (s->byte >> 1) == s->addr;
                s->read_mode = s->byte & 1U;
            } else if (s->nwritten < sizeof s->written) {
                s->written[s->nwritten++] = (uint8_t)s->byte;
            }
            s->state = ok ? I2C_ACK : I2C_NACK;
            s->sda_low = ok;
        } else if (s->state == I2C_ACK) {
            s->sda_low = 0;
            if (s->read_mode) {
                i2c_load_read_byte(s);
            } else {
                s->state = I2C_WDATA;
                s->byte = 0;
                s->bits = 0;
            }
        } else if (s->state == I2C_RDATA) {
            s->bits++;
            if (s->bits < 8) {
                s->sda_low = !((s->byte >> (7 - s->bits)) & 1U);
            } else {
                s->sda_low = 0;
                s->state = I2C_MACK;
            }
        } else if (s->state == I2C_MACK) {
            if (s->nacks && s->master_acks[s->nacks - 1U])
                i2c_load_read_byte(s);
            else
                s->state = I2C_IDLE;
        } else if (s->state == I2C_NACK) {
            s->state = I2C_IDLE;
        }
    }
    s->psda = sda;
    s->pscl = scl;
    return (uint8_t)((sda << 5) | (scl << 6));
}

/* ---- USB low speed (test_fw_usb_ls_tx_packet / usb_levels) --------------- */

char msf_usb_state(msf_pins p)
{
    int dm = p.out & 1, dp = (p.out >> 1) & 1;

    if ((p.oe & 3U) != 3U)
        return 'z';
    if (!dm && !dp)
        return '0';
    if (dm && !dp)
        return 'J';
    if (!dm && dp)
        return 'K';
    return '1';
}

int msf_usb_decode(const msf_trace *t, uint32_t start, uint8_t *bytes, uint32_t max,
                   uint32_t *nbytes, uint32_t *eop)
{
    uint32_t i = start + 4U, nb = 0;
    char prev = 'J';
    int ones = 0, skip = 0;
    unsigned acc = 0, nacc = 0;

    *nbytes = 0;
    for (;;) {
        char s;
        int bit;

        if (i >= t->n)
            return -2;
        s = msf_usb_state(t->p[i]);
        if (s != 'J' && s != 'K')
            break;
        bit = s == prev;                 /* NRZI: no transition = 1 */
        prev = s;
        i += 8U;
        if (skip) {                      /* de-stuff */
            if (bit != 0)
                return -1;
            skip = 0;
            ones = 0;
            continue;
        }
        acc |= (unsigned)bit << nacc;
        if (++nacc == 8U) {
            if (nb < max)
                bytes[nb++] = (uint8_t)acc;
            acc = 0;
            nacc = 0;
        }
        ones = bit ? ones + 1 : 0;
        skip = ones == 6;
    }
    *nbytes = nb;
    *eop = i - 4U;
    return 0;
}

uint32_t msf_usb_levels(const uint8_t *pkt, uint32_t n, int corrupt, uint8_t *lv, uint32_t max)
{
    uint32_t i, k = 0;
    unsigned b;
    int ones = 0, cur = 1;

    for (i = 0; i < n; ++i) {
        for (b = 0; b < 8U; ++b) {
            int bit = (pkt[i] >> b) & 1, pass;
            for (pass = 0; pass < 2; ++pass) {
                int sb = bit;
                if (pass == 1) {         /* stuffed 0 after six 1s */
                    if (ones != 6)
                        break;
                    sb = 0;
                    ones = 0;
                } else {
                    ones = bit ? ones + 1 : 0;
                }
                if (corrupt && k == 40U)
                    sb ^= 1;
                cur = sb ? cur : 1 - cur;
                if (k < max)
                    lv[k] = (uint8_t)cur;
                ++k;
            }
        }
    }
    return k < max ? k : max;
}

/* ---- 10BASE-T (test_fw_10baset_frame_fcs_tpidl_nlp) ---------------------- */

int msf_eth_decode(const msf_trace *t, uint8_t *bytes, uint32_t max,
                   uint32_t *nbytes, uint32_t *end)
{
    uint32_t i = 0, nbits = 0;
    unsigned acc = 0;

    *nbytes = 0;
    while (i < t->n && !((t->p[i].out >> 2) & 1) && !((t->p[i].out >> 3) & 1))
        ++i;
    for (;;) {
        int a, b;

        if (i + 1U >= t->n)
            return -2;
        a = (t->p[i].out >> 2) & 1;
        b = (t->p[i + 1U].out >> 2) & 1;
        if (((t->p[i].out >> 3) & 1) != 1 - a || ((t->p[i + 1U].out >> 3) & 1) != 1 - b)
            return -1;
        if (a == b)
            break;
        acc |= (unsigned)b << (nbits % 8U);  /* IEEE 802.3: 1 = low -> high */
        if (++nbits % 8U == 0U) {
            if (*nbytes < max)
                bytes[(*nbytes)++] = (uint8_t)acc;
            acc = 0;
        }
        i += 2U;
    }
    *end = i;
    return nbits % 8U == 0U ? 0 : -3;
}
