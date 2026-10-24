// SPDX-License-Identifier: Apache-2.0
// Protocol models (see msf_proto.h). Each function names the Python model it
// ports; keep the two in step when either changes.
#include "msf_proto.h"

#include <string.h>

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

/* ======================================================================== */
/* UART host                                                                */

void msf_uart_host_init(msf_uart_host *u, uint32_t bit_cycles)
{
    memset(u, 0, sizeof *u);
    u->bit = bit_cycles;
    u->tbit = -1;
    u->rbit = -1;
    u->rprev = 1;
}

void msf_uart_host_send(msf_uart_host *u, const uint8_t *b, uint32_t n)
{
    uint32_t i;

    for (i = 0; i < n && u->ntx < sizeof u->tx; ++i)
        u->tx[u->ntx++] = b[i];
}

int msf_uart_host_idle(const msf_uart_host *u)
{
    return u->itx == u->ntx && u->tbit < 0;
}

int msf_uart_host_cycle(msf_uart_host *u, msf_pins p)
{
    /* the probe's TX (uio0) idles high through the line's pull-up until
     * the firmware drives it */
    int level = 1, line = (p.oe & 1U) ? (int)(p.out & 1U) : 1;

    /* receive the probe TX: sample mid-bit after a falling edge */
    if (u->rbit < 0) {
        if (u->rprev && !line) {
            u->rbit = 0;
            u->rcycle = 0;
            u->rbyte = 0;
        }
    } else if (++u->rcycle == u->bit / 2U + u->bit * (uint32_t)u->rbit) {
        if (u->rbit >= 1 && u->rbit <= 8)
            u->rbyte |= (unsigned)line << (u->rbit - 1);
        if (u->rbit == 9) {
            if (u->nrx < sizeof u->rx)
                u->rx[u->nrx++] = (uint8_t)u->rbyte;
            u->rbit = -1;
        } else {
            ++u->rbit;
        }
    }
    u->rprev = line;
    /* send: start bit, 8 data bits LSB first, stop bit, one idle bit */
    if (u->tbit < 0 && u->itx < u->ntx) {
        u->tbit = 0;
        u->tcycle = 0;
    }
    if (u->tbit >= 0) {
        unsigned v = u->tx[u->itx];
        level = u->tbit == 0 ? 0 : u->tbit <= 8 ? (int)((v >> (u->tbit - 1)) & 1U) : 1;
        if (++u->tcycle == u->bit) {
            u->tcycle = 0;
            if (++u->tbit == 11) {
                u->tbit = -1;
                ++u->itx;
            }
        }
    }
    return level;
}

/* ======================================================================== */
/* JTAG: IEEE 1149.1 TAP controller (test_debugports.Tap)                   */

enum { TLR, RTI, SDR, CDR, SHDR, E1DR, PDR, E2DR, UDR, SIR, CIR, SHIR, E1IR, PIR, E2IR, UIR };
static const uint8_t tap_next[16][2] = {
    {RTI, TLR}, {RTI, SDR}, {CDR, SIR}, {SHDR, E1DR}, {SHDR, E1DR}, {PDR, UDR}, {PDR, E2DR},
    {SHDR, UDR}, {RTI, SDR}, {CIR, TLR}, {SHIR, E1IR}, {SHIR, E1IR}, {PIR, UIR}, {PIR, E2IR},
    {SHIR, UIR}, {RTI, SDR},
};
#define TAP_IDCODE 0x1U

static void tap_rise(msf_jtag_tap *t, int tms, int tdi)
{
    int s = t->state;

    if (s == CDR) {
        if (t->ir == TAP_IDCODE) {
            t->sh = t->idcode;
            t->n = 32;
        } else {
            t->sh = 0;
            t->n = 1;
        }
    } else if (s == CIR) {
        t->sh = 0x1U;
        t->n = 4;
    } else if (s == SHDR || s == SHIR) {
        t->sh = (t->sh >> 1) | ((uint32_t)tdi << (t->n - 1));
    }
    t->state = tap_next[s][tms];
}

static void tap_fall(msf_jtag_tap *t)
{
    if (t->state == SHDR || t->state == SHIR)
        t->tdo = (int)(t->sh & 1U);
    else if (t->state == UIR)
        t->ir = t->sh;
    else if (t->state == TLR)
        t->ir = TAP_IDCODE;
}

void msf_jtag_init(msf_jtag_chain *c, const uint32_t *idcodes, int n)
{
    int i;

    memset(c, 0, sizeof *c);
    c->ntaps = n;
    for (i = 0; i < n; ++i) {
        c->tap[i].idcode = idcodes[i];
        c->tap[i].state = TLR;
        c->tap[i].ir = TAP_IDCODE;
        c->tap[i].n = 1;
    }
}

uint8_t msf_jtag_cycle(msf_jtag_chain *c, msf_pins p)
{
    int tck = (p.out >> 2) & 1, tms = (p.out >> 5) & 1, tdi = (p.out >> 3) & 1, i;

    if (tms != c->tms || tdi != c->tdi) {
        if (tck || c->tck)
            ++c->errors;                 /* TDI/TMS changed while TCK high */
        c->last_change = c->k;
    }
    if (tck && !c->tck) {
        int in[2];

        if (c->k - c->last_change < 2U)
            ++c->errors;                 /* TCK rose too soon after TDI/TMS */
        in[0] = tdi;
        in[1] = c->tap[0].tdo;
        for (i = 0; i < c->ntaps; ++i)
            tap_rise(&c->tap[i], tms, in[i]);
    } else if (!tck && c->tck) {
        for (i = 0; i < c->ntaps; ++i)
            tap_fall(&c->tap[i]);
    }
    c->tck = tck;
    c->tms = tms;
    c->tdi = tdi;
    ++c->k;
    return (uint8_t)(c->tap[c->ntaps - 1].tdo << 4);
}

/* ======================================================================== */
/* 1-Wire (Maxim), standard speed, 12 cycles per microsecond                */
/* (test_buses.OneWireDevice and OneWireBus)                                */

#define OW_US 12U

uint8_t msf_crc8_maxim(const uint8_t *d, uint32_t n)
{
    uint8_t crc = 0;
    uint32_t i;
    int b;

    for (i = 0; i < n; ++i) {
        uint8_t byte = d[i];
        for (b = 0; b < 8; ++b) {
            int mix = (crc ^ byte) & 1;
            crc >>= 1;
            if (mix)
                crc ^= 0x8CU;
            byte >>= 1;
        }
    }
    return crc;
}

enum { OW_IDLE, OW_ROM, OW_FUNC, OW_MATCH, OW_SEARCH, OW_SEARCH_DIR };

void msf_ow_dev_init(msf_ow_dev *d, uint8_t family, uint64_t serial)
{
    static const uint8_t sp[8] = {0x50, 0x05, 0x4B, 0x46, 0x7F, 0xFF, 0x0C, 0x10};
    int i;

    memset(d, 0, sizeof *d);
    d->rom[0] = family;
    for (i = 0; i < 6; ++i)
        d->rom[1 + i] = (uint8_t)(serial >> (8 * i));
    d->rom[7] = msf_crc8_maxim(d->rom, 7U);
    memcpy(d->scratch, sp, 8U);
    d->scratch[8] = msf_crc8_maxim(sp, 8U);
    d->state = OW_IDLE;
}

static int ow_rombit(const msf_ow_dev *d, int i)
{
    return (d->rom[i / 8] >> (i % 8)) & 1;
}

static void ow_send_bits(msf_ow_dev *d, const uint8_t *bytes, int nbits)
{
    int i;

    d->nout = 0;
    d->iout = 0;
    for (i = 0; i < nbits && i < (int)sizeof d->out; ++i)
        d->out[d->nout++] = (uint8_t)((bytes[i / 8] >> (i % 8)) & 1);
}

static void ow_search_pair(msf_ow_dev *d)
{
    int b = ow_rombit(d, d->si);

    d->out[0] = (uint8_t)b;
    d->out[1] = (uint8_t)(1 - b);
    d->nout = 2;
    d->iout = 0;
    d->state = OW_SEARCH;
}

/* 30 us into a slot: a read slot's bit went out, or a write slot's bit is `line` */
static void ow_slot(msf_ow_dev *d, int line)
{
    if (d->state == OW_IDLE)
        return;
    if (d->iout < d->nout) {
        if (++d->iout == d->nout && d->state == OW_SEARCH)
            d->state = OW_SEARCH_DIR;
        return;
    }
    if (d->state == OW_SEARCH_DIR) {
        if (line != ow_rombit(d, d->si)) {
            d->state = OW_IDLE;
        } else if (++d->si == 64) {
            d->state = OW_FUNC;
        } else {
            ow_search_pair(d);
        }
        return;
    }
    d->bits[d->nbits++] = (uint8_t)line;
    if ((d->state == OW_ROM || d->state == OW_FUNC) && d->nbits == 8U) {
        unsigned c = 0, i;
        for (i = 0; i < 8U; ++i)
            c |= (unsigned)d->bits[i] << i;
        d->nbits = 0;
        if (d->state == OW_ROM) {
            if (c == 0x33U) {
                ow_send_bits(d, d->rom, 64);
                d->state = OW_FUNC;
            } else if (c == 0xCCU) {
                d->state = OW_FUNC;
            } else if (c == 0x55U) {
                d->state = OW_MATCH;
            } else if (c == 0xF0U) {
                d->si = 0;
                ow_search_pair(d);
            } else {
                d->state = OW_IDLE;
            }
        } else if (c == 0xBEU) {
            ow_send_bits(d, d->scratch, 72);
        }
    } else if (d->state == OW_MATCH && d->nbits == 64U) {
        int i, same = 1;
        for (i = 0; i < 64; ++i)
            same &= d->bits[i] == ow_rombit(d, i);
        d->state = same ? OW_FUNC : OW_IDLE;
        d->nbits = 0;
    }
}

void msf_ow_bus_init(msf_ow_bus *b, msf_ow_dev *devs, int n)
{
    memset(b, 0, sizeof *b);
    b->dev = devs;
    b->ndev = n;
}

static int ow_pulling(const msf_ow_bus *b, uint32_t k)
{
    int i;

    for (i = 0; i < b->ndev; ++i)
        if (b->dev[i].pull_from <= k && k < b->dev[i].pull_to)
            return 1;
    return 0;
}

uint8_t msf_ow_bus_cycle(msf_ow_bus *b, msf_pins p)
{
    int chip = ((p.oe >> 3) & 1) && !((p.out >> 3) & 1), i;
    uint32_t k = b->k;

    if (chip && !b->chip_prev) {          /* a slot starts */
        b->fall_at = k;
        b->have_fall = 1;
        for (i = 0; i < b->ndev; ++i) {
            msf_ow_dev *d = &b->dev[i];
            if (d->state != OW_IDLE && d->iout < d->nout && d->out[d->iout] == 0) {
                d->pull_from = k;
                d->pull_to = k + 30U * OW_US;
            }
        }
    }
    if (!chip && b->chip_prev && k - b->fall_at >= 480U * OW_US) {
        for (i = 0; i < b->ndev; ++i) {   /* a reset: presence pulses */
            msf_ow_dev *d = &b->dev[i];
            d->state = OW_ROM;
            d->nbits = 0;
            d->nout = d->iout = 0;
            d->pull_from = k + 30U * OW_US;
            d->pull_to = k + 150U * OW_US;
        }
    }
    if (b->have_fall && k == b->fall_at + 30U * OW_US) {
        int line = (chip || ow_pulling(b, k)) ? 0 : 1;
        for (i = 0; i < b->ndev; ++i)
            ow_slot(&b->dev[i], line);
    }
    b->chip_prev = chip;
    ++b->k;
    return ow_pulling(b, k) ? 0 : 0x08U;
}

/* ======================================================================== */
/* SPI controller (test_targets.SpiController)                              */

#define SPI_SCK    0x04U
#define SPI_MOSI   0x08U
#define SPI_CS     0x20U
#define SPI_SAMPLE 0x80U

static int spi_put(msf_spi_ctl *c, uint8_t v, uint32_t n)
{
    while (n--) {
        if (c->n >= MSF_SPI_WAVE)
            return -1;
        c->wave[c->n++] = v;
    }
    return 0;
}

int msf_spi_ctl_init(msf_spi_ctl *c, int mode, const uint8_t *bytes, const uint8_t *lens,
                     uint32_t ntr, uint32_t half, uint32_t lead, uint32_t gap)
{
    uint8_t idle = (mode & 2) ? SPI_SCK : 0U, act = (mode & 2) ? 0U : SPI_SCK;
    int cpha = mode & 1, err = 0;
    uint32_t t, off = 0;

    memset(c, 0, sizeof *c);
    err |= spi_put(c, (uint8_t)(idle | SPI_CS), 30U);
    for (t = 0; t < ntr; ++t) {
        uint32_t nb = 8U * lens[t], b;
        uint8_t mosi = 0;

#define SPI_BIT(b) ((bytes[off + (b) / 8U] >> (7U - (b) % 8U)) & 1U)
        if (!cpha && nb)
            mosi = SPI_BIT(0U) ? SPI_MOSI : 0U;
        err |= spi_put(c, (uint8_t)(idle | mosi), lead);
        for (b = 0; b < nb; ++b) {
            if (cpha) {               /* shift on the leading edge, sample on the trailing */
                mosi = SPI_BIT(b) ? SPI_MOSI : 0U;
                err |= spi_put(c, (uint8_t)(act | mosi), half);
                err |= spi_put(c, (uint8_t)(idle | mosi | SPI_SAMPLE), 1U);
                err |= spi_put(c, (uint8_t)(idle | mosi), half - 1U);
            } else {                  /* sample on the leading edge, shift on the trailing */
                err |= spi_put(c, (uint8_t)(act | mosi | SPI_SAMPLE), 1U);
                err |= spi_put(c, (uint8_t)(act | mosi), half - 1U);
                mosi = (b + 1U < nb && SPI_BIT(b + 1U)) ? SPI_MOSI : 0U;
                err |= spi_put(c, (uint8_t)(idle | mosi), half);
            }
        }
#undef SPI_BIT
        err |= spi_put(c, (uint8_t)(idle | SPI_CS), gap);
        off += lens[t];
    }
    err |= spi_put(c, (uint8_t)(idle | SPI_CS), 20U);
    return err ? -1 : 0;
}

uint8_t msf_spi_ctl_cycle(msf_spi_ctl *c, msf_pins p)
{
    /* the chip's output in the cycle the last edge went in (SpiController.step) */
    if (c->i > 0 && (c->wave[c->i - 1U] & SPI_SAMPLE) && c->nbits < 8U * sizeof c->miso) {
        if ((p.out >> 4) & 1U)
            c->miso[c->nbits / 8U] |= (uint8_t)(0x80U >> (c->nbits % 8U));
        ++c->nbits;
    }
    if (c->i >= c->n)
        return (uint8_t)(c->wave[c->n - 1U] & 0x7FU);
    return (uint8_t)(c->wave[c->i++] & 0x7FU);
}

int msf_spi_ctl_done(const msf_spi_ctl *c)
{
    return c->i >= c->n;
}

uint8_t msf_spi_ctl_byte(const msf_spi_ctl *c, uint32_t k)
{
    return k < sizeof c->miso ? c->miso[k] : 0U;
}
