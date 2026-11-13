// SPDX-License-Identifier: Apache-2.0
// msfcore link over PS EMIO GPIO (see msf_emio.h for the bit map).
#include "msf_link.h"

#include "msf_emio.h"

#define UI_MASK     0xffU
#define UIO_MASK    (0xffU << MSF_O_UIO_SHIFT)
#define POLL_LIMIT  200000U   /* status polls (free run) or core cycles (step mode) */
#define ACK_LIMIT   1000U     /* ACK polls per step request */

static uint32_t out;          /* shadow of the EMIO outputs */
static int stepping;
static int strobe;            /* strobe level of the last parallel byte */
static int fault;
static uint32_t digest_crc, digest_steps;   /* msf_digest() */

static void put(uint32_t value)
{
    out = value;
    msf_emio_write(out);
}

static msf_pins decode(uint32_t v)
{
    msf_pins p;

    p.uo  = (uint8_t)(v >> MSF_I_UO_SHIFT);
    p.out = (uint8_t)(v >> MSF_I_OUT_SHIFT);
    p.oe  = (uint8_t)(v >> MSF_I_OE_SHIFT);
    return p;
}

static int wait_inputs(uint32_t mask, uint32_t want)
{
    uint32_t n;

    for (n = 0; n < POLL_LIMIT; ++n)
        if ((msf_emio_read() & mask) == want)
            return MSF_OK;
    return MSF_ERR_TIMEOUT;
}

int msf_link_init(void)
{
    out = 0;
    stepping = 0;
    strobe = 0;
    fault = 0;
    return msf_emio_init();
}

int msf_link_fault(void)
{
    return fault;
}

static void digest_byte(uint8_t b)
{
    int i;

    digest_crc ^= b;
    for (i = 0; i < 8; ++i)
        digest_crc = (digest_crc >> 1) ^ (0xEDB88320U & (0U - (digest_crc & 1U)));
}

void msf_digest_reset(void)
{
    digest_crc = 0xFFFFFFFFU;
    digest_steps = 0;
}

uint32_t msf_digest(uint32_t *steps)
{
    if (steps)
        *steps = digest_steps;
    return ~digest_crc;
}

msf_pins msf_step(uint8_t uio)
{
    uint32_t v = 0, n;
    msf_pins p;

    put(((out & ~UIO_MASK) | ((uint32_t)uio << MSF_O_UIO_SHIFT)) ^ MSF_O_REQ);
    for (n = 0; n < ACK_LIMIT; ++n) {
        v = msf_emio_read();
        if (((v & MSF_I_ACK) != 0U) == ((out & MSF_O_REQ) != 0U))
            break;
    }
    if (n == ACK_LIMIT)
        fault = 1;
    p = decode(v);
    digest_byte((uint8_t)(out & UI_MASK));
    digest_byte(uio);
    digest_byte(p.uo);
    digest_byte(p.out);
    digest_byte(p.oe);
    ++digest_steps;
    return p;
}

msf_pins msf_sample(void)
{
    return decode(msf_emio_read());
}

int msf_reset(uint8_t ui, uint8_t uio, int step)
{
    int i, rc;

    stepping = step;
    strobe = 0;
    put((out & MSF_O_REQ) | MSF_O_OWN | ui | ((uint32_t)uio << MSF_O_UIO_SHIFT) |
        (step ? MSF_O_STEP : 0U));
    rc = wait_inputs(MSF_I_RSTN | MSF_I_STEP, step ? MSF_I_STEP : 0U);
    if (rc != MSF_OK)
        return rc;
    /* Like test/msf_tb.py reset(): 5 clocks in reset so the core's input
       synchronisers hold the new ui_in/uio_in before it starts. */
    if (step)
        for (i = 0; i < 5; ++i)
            msf_step(uio);
    else
        msf_emio_delay_us(2);
    return fault ? MSF_ERR_TIMEOUT : MSF_OK;
}

int msf_run(void)
{
    put(out | MSF_O_RUN);
    return wait_inputs(MSF_I_RSTN, MSF_I_RSTN);
}

void msf_set_ui(uint8_t ui)
{
    put((out & ~UI_MASK) | ui);
}

void msf_release_pins(void)
{
    put(out & ~(MSF_O_OWN | MSF_O_STB | UIO_MASK));
}

int msf_restart(void)
{
    int rc = msf_reset(MSF_BOOT_DIRECT, 0, 0);

    if (rc == MSF_OK)
        rc = msf_run();
    if (rc == MSF_OK) {
        msf_emio_delay_us(1);   /* boot select has read ui_in[0] = 1 */
        msf_release_pins();
    }
    return rc;
}

/* ---- boot ROM, parallel byte source ------------------------------------ */

static uint8_t crc8_update(uint8_t crc, uint8_t byte)
{
    unsigned bit;

    crc ^= byte;
    for (bit = 0; bit < 8U; ++bit)
        crc = (crc & 0x80U) ? (uint8_t)((crc << 1) ^ 0x07U) : (uint8_t)(crc << 1);
    return crc;
}

uint8_t msf_crc8(const uint8_t *data, uint32_t n)
{
    uint8_t crc = 0;
    uint32_t i;

    for (i = 0; i < n; ++i)
        crc = crc8_update(crc, data[i]);
    return crc;
}

static msf_pins advance(void)
{
    return stepping ? msf_step(0) : msf_sample();
}

static int wait_uo(uint8_t mask, uint8_t want)
{
    uint32_t n;

    for (n = 0; n < POLL_LIMIT; ++n)
        if ((advance().uo & mask) == want)
            return fault ? MSF_ERR_TIMEOUT : MSF_OK;
    return MSF_ERR_TIMEOUT;
}

/* Byte k is taken when the strobe (uio[1]) reaches level (k + 1) mod 2; the
   ROM acknowledges by copying that level to uo_out[7]. Data goes out first. */
static int par_byte(uint8_t b)
{
    msf_set_ui(b);
    if (stepping)
        msf_step(0);
    else
        msf_emio_delay_us(1);
    strobe ^= 1;
    put(strobe ? (out | MSF_O_STB) : (out & ~MSF_O_STB));
    return wait_uo(MSF_ST_PAR_ACK, strobe ? MSF_ST_PAR_ACK : 0U);
}

int msf_boot_send(const uint8_t *bytes, uint32_t n)
{
    uint32_t i;
    int rc;

    for (i = 0; i < n; ++i)
        if ((rc = par_byte(bytes[i])) != MSF_OK)
            return rc;
    return MSF_OK;
}

int msf_boot_result(void)
{
    uint32_t n;

    for (n = 0; n < POLL_LIMIT; ++n) {
        uint8_t uo = advance().uo;
        if (uo & MSF_ST_FRAME_BAD)
            return MSF_ERR_NAK;
        if (uo & MSF_ST_FRAME_OK)
            return fault ? MSF_ERR_TIMEOUT : MSF_OK;
    }
    return MSF_ERR_TIMEOUT;
}

uint32_t msf_boot_frame(uint8_t cmd, uint16_t addr, const uint16_t *words,
                        uint16_t count, uint8_t *buf, uint32_t cap)
{
    uint32_t n = 0, i;

    if (cap < 7U + 2U * count)
        return 0;
    buf[n++] = 0xa5U;
    buf[n++] = cmd;
    buf[n++] = (uint8_t)addr;
    buf[n++] = (uint8_t)(addr >> 8);
    buf[n++] = (uint8_t)count;
    buf[n++] = (uint8_t)(count >> 8);
    for (i = 0; i < count; ++i) {
        buf[n++] = (uint8_t)words[i];
        buf[n++] = (uint8_t)(words[i] >> 8);
    }
    buf[n] = msf_crc8(buf + 1, n - 1U);
    return n + 1U;
}

/* Streams a frame without a buffer: A5 cmd addr len payload crc8. */
static int send_frame(uint8_t cmd, uint16_t addr, const uint16_t *words, uint16_t count)
{
    uint8_t head[6];
    uint8_t crc;
    uint32_t i;
    int rc;

    head[0] = 0xa5U;
    head[1] = cmd;
    head[2] = (uint8_t)addr;
    head[3] = (uint8_t)(addr >> 8);
    head[4] = (uint8_t)count;
    head[5] = (uint8_t)(count >> 8);
    if ((rc = msf_boot_send(head, 6U)) != MSF_OK)
        return rc;
    crc = msf_crc8(head + 1, 5U);
    for (i = 0; i < count; ++i) {
        uint8_t w[2];
        w[0] = (uint8_t)words[i];
        w[1] = (uint8_t)(words[i] >> 8);
        if ((rc = msf_boot_send(w, 2U)) != MSF_OK)
            return rc;
        crc = crc8_update(crc8_update(crc, w[0]), w[1]);
    }
    if ((rc = msf_boot_send(&crc, 1U)) != MSF_OK)
        return rc;
    return msf_boot_result();
}

int msf_boot_enter(int step)
{
    int rc = msf_reset(MSF_BOOT_PARALLEL, 0, step);

    if (rc == MSF_OK)
        rc = msf_run();
    if (rc == MSF_OK)
        rc = wait_uo(MSF_ST_WAITING, MSF_ST_WAITING);
    return rc;
}

int msf_boot_write(uint16_t addr, const uint16_t *words, uint16_t count)
{
    if ((uint32_t)addr + count > MSF_SRAM_WORDS)
        return MSF_ERR_ARG;
    return send_frame(MSF_CMD_WRITE, addr, words, count);
}

int msf_boot_go(uint16_t addr)
{
    int rc = send_frame(MSF_CMD_GO, addr, 0, 0U);

    if (rc == MSF_OK)
        rc = wait_uo(MSF_ST_STARTING, MSF_ST_STARTING);
    return rc;
}
