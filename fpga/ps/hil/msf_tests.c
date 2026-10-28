// SPDX-License-Identifier: Apache-2.0
// Hardware-in-the-loop protocol tests: every firmware library demo
// (firmware/*.s) runs on the FPGA core in step mode and is checked at the
// pins by the C protocol models in msf_proto.c. These are C ports of
// test/test_firmware.py plus the parallel cases of test/test_boot.py; the
// data-RAM checks read results back with firmware/dump_ram.s.
#include "msf_tests.h"

#include <string.h>

#include "msf_emio.h"
#include "msf_images.h"
#include "msf_link.h"
#include "msf_log.h"
#include "msf_proto.h"

#define DONE       0x55U
#define TRACE_CAP  524288U

static msf_pins trace_buf[TRACE_CAP];
static msf_trace tr;

#define CHECK(cond, ...)                                         \
    do {                                                         \
        if (!(cond)) {                                           \
            msf_printf("    FAIL (line %d): ", __LINE__);        \
            msf_printf(__VA_ARGS__);                             \
            msf_printf("\r\n");                                  \
            return -1;                                           \
        }                                                        \
    } while (0)

#define TRY(expr)                                                \
    do {                                                         \
        int rc_ = (expr);                                        \
        if (rc_ != MSF_OK) {                                     \
            msf_printf("    FAIL (line %d): %s returned %d\r\n", \
                       __LINE__, #expr, rc_);                    \
            return -1;                                           \
        }                                                        \
    } while (0)

/* test_firmware.fw_boot: zero the data RAM, load the parameters and the
 * image through the boot ROM (free run: fast), then reset into the image
 * (ui_in[0] = 1) in step mode with uio_in = uio. Starts a new trace. */
static int fw_boot(const msf_image *img, const uint16_t *data, uint16_t ndata, uint8_t uio)
{
    static const uint16_t zeros[128];

    TRY(msf_boot_enter(0));
    TRY(msf_boot_write(0, zeros, 128));
    if (ndata)
        TRY(msf_boot_write(0, data, ndata));
    TRY(msf_boot_write(img->addr, img->words, img->count));
    TRY(msf_reset(MSF_BOOT_DIRECT, uio, 1));
    TRY(msf_run());
    msf_trace_init(&tr, trace_buf, TRACE_CAP);
    return 0;
}

/* One recorded core cycle (test_firmware.Recorder). */
static msf_pins cycle(uint8_t uio)
{
    msf_pins p = msf_step(uio);

    msf_trace_add(&tr, p);
    return p;
}

/* Read data RAM back: load dump_ram.s with its START/COUNT immediates
 * patched, run it from reset and collect a byte per uio_out[7] toggle. */
static int dump_ram(uint16_t addr, uint16_t count, uint16_t *words)
{
    uint16_t code[64];
    uint8_t bytes[256];
    uint32_t got = 0, c, i;
    int prev = 0;

    CHECK(img_dump_ram.count <= 64U && addr + count <= 1024U && count >= 1U && count <= 128U,
          "dump_ram: bad arguments");
    memcpy(code, img_dump_ram.words, img_dump_ram.count * sizeof code[0]);
    CHECK((code[0] & 0xff00U) == 0x0300U && (code[1] & 0xff00U) == 0xfb00U &&
              (code[2] & 0xff00U) == 0x0400U,
          "dump_ram.s must start with LDR R3 / LDH R3 / LDR R4");
    code[0] = (uint16_t)(0x0300U | (addr & 0xffU));
    code[1] = (uint16_t)(0xfb00U | addr >> 8);
    code[2] = (uint16_t)(0x0400U | count);
    TRY(msf_boot_enter(0));
    TRY(msf_boot_write(img_dump_ram.addr, code, img_dump_ram.count));
    TRY(msf_reset(MSF_BOOT_DIRECT, 0, 1));
    TRY(msf_run());
    for (c = 0; c < 32U * count + 64U && got < 2U * count; ++c) {
        msf_pins p = msf_step(0);
        int b = (p.out >> 7) & 1;
        if (b != prev) {
            bytes[got++] = p.uo;
            prev = b;
        }
    }
    CHECK(got == 2U * count, "RAM dump returned %d of %d bytes", (int)got, 2 * (int)count);
    for (i = 0; i < count; ++i)
        words[i] = (uint16_t)(bytes[2U * i] | bytes[2U * i + 1U] << 8);
    return 0;
}

/* ======================================================================== */

static int test_crc_check_values(void)
{
    static const uint8_t check[9] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};

    CHECK(msf_crc8(check, 9) == 0xf4U, "CRC-8/0x07 = %x", (unsigned)msf_crc8(check, 9));
    CHECK(msf_usb_crc16(check, 9) == 0xb4c8U, "USB CRC16 = %x",
          (unsigned)msf_usb_crc16(check, 9));
    CHECK(msf_eth_fcs(check, 9) == 0xcbf43926UL, "Ethernet FCS = %x",
          (unsigned)msf_eth_fcs(check, 9));
    return 0;
}

/* test_boot.test_parallel_load_and_run: W + G through the boot ROM. */
static int test_boot_parallel_go(void)
{
    msf_pins p = {0, 0, 0};
    uint32_t c;

    TRY(msf_boot_enter(1));
    TRY(msf_boot_write(img_boot_probe.addr, img_boot_probe.words, img_boot_probe.count));
    TRY(msf_boot_go(img_boot_probe.addr));
    for (c = 0; c < 3000U; ++c)
        p = msf_step(0);
    CHECK((p.out & 0xfeU) == 0x42U, "uio_out = %x, want 0x42", (unsigned)p.out);
    CHECK((p.uo & 0x0aU) == 0x0aU, "uo_out = %x, want frame OK + starting firmware",
          (unsigned)p.uo);
    return 0;
}

/* test_boot.test_uart_bad_crc_nak_then_retry, on the parallel byte source. */
static int test_boot_bad_crc_then_retry(void)
{
    static const uint8_t junk[2] = {0x00, 0x13};
    uint8_t frame[7 + 2 * 16];
    msf_pins p = {0, 0, 0};
    uint32_t n, c;

    CHECK(img_boot_probe.count <= 16U, "boot_probe image too large");
    n = msf_boot_frame(MSF_CMD_WRITE, img_boot_probe.addr, img_boot_probe.words,
                       img_boot_probe.count, frame, sizeof frame);
    CHECK(n > 8U, "frame build failed");
    frame[8] ^= 0x10U;                                 /* corrupt a payload byte */
    TRY(msf_boot_enter(1));
    TRY(msf_boot_send(junk, 2U));                      /* junk before the sync byte */
    TRY(msf_boot_send(frame, n));
    CHECK(msf_boot_result() == MSF_ERR_NAK, "corrupted frame was not rejected");
    TRY(msf_boot_write(img_boot_probe.addr, img_boot_probe.words, img_boot_probe.count));
    TRY(msf_boot_go(img_boot_probe.addr));
    for (c = 0; c < 3000U; ++c)
        p = msf_step(0);
    CHECK((p.out & 0xfeU) == 0x42U, "uio_out = %x after the retry, want 0x42",
          (unsigned)p.out);
    return 0;
}

/* msf_restart(): re-runs what is in SRAM through direct boot, so the boot
 * ROM status bits (set by a W + G load) are gone and the firmware runs again. */
static int test_restart_direct_boot(void)
{
    msf_pins p;

    TRY(msf_boot_enter(0));
    TRY(msf_boot_write(img_boot_probe.addr, img_boot_probe.words, img_boot_probe.count));
    TRY(msf_boot_go(img_boot_probe.addr));
    TRY(msf_restart());
    msf_emio_delay_us(5);
    p = msf_sample();
    CHECK((p.out & 0xfeU) == 0x42U && (p.oe & 0xfeU) == 0xfeU,
          "uio_out = %x, oe = %x after restart, want 0x42 / 0xfe", (unsigned)p.out,
          (unsigned)p.oe);
    CHECK(p.uo == 0U, "uo_out = %x: the boot ROM ran instead of direct boot", (unsigned)p.uo);
    return 0;
}

/* test_fw_uart_tx_drift_free */
static int test_uart_tx_drift_free(void)
{
    static const char msg[] = "msfcore says hi! 0123456789";
    const uint32_t len = sizeof msg - 1U, bit = 104U;
    uint16_t data[sizeof msg];
    int dec[64];
    uint32_t i, n, c, first = 0, e0 = 0, elast = 0, nedges = 0;

    for (i = 0; i < sizeof msg; ++i)                   /* includes the terminator */
        data[i] = (uint8_t)msg[i];
    if (fw_boot(&img_uart_tx, data, sizeof msg, 0))
        return -1;
    for (c = 0; c < 40000U; ++c)
        if (cycle(0).uo == DONE)
            break;
    CHECK(c < 40000U, "uo_out never showed DONE");
    for (c = 0; c < 200U; ++c)
        cycle(0);
    CHECK(!tr.overflow, "trace overflow");

    n = msf_uart_decode(&tr, bit, 0, dec, 64);
    CHECK(n == len, "decoded %d bytes, want %d", (int)n, (int)len);
    for (i = 0; i < len; ++i)
        CHECK(dec[i] == (uint8_t)msg[i], "byte %d = %x, want %x", (int)i, dec[i],
              (unsigned)(uint8_t)msg[i]);

    /* every edge sits on the 104-cycle grid of the first start bit: no drift */
    for (i = 1; i < tr.n && !first; ++i)
        if ((tr.p[i - 1U].out & 1U) && !(tr.p[i].out & 1U))
            first = i;
    CHECK(first, "no start bit");
    for (i = first; i < tr.n; ++i) {
        if ((tr.p[i].out & 1U) != (tr.p[i - 1U].out & 1U)) {
            if (nedges++ == 0U)
                e0 = i;
            CHECK((i - e0) % bit == 0U, "edge %d cycles after the first is off the grid",
                  (int)(i - e0));
            elast = i;
        }
    }
    CHECK(elast - e0 > bit * 10U * (len - 1U), "edges span only %d cycles", (int)(elast - e0));
    return 0;
}

/* test_fw_spi_all_modes_full_duplex */
static int test_spi_all_modes_full_duplex(void)
{
    msf_rng rng;
    int mode;

    msf_rng_seed(&rng, 42U);
    for (mode = 0; mode < 4; ++mode) {
        uint8_t out[6], back[6], uio = 0;
        uint16_t data[8], got[6];
        msf_spi_slave s;
        msf_pins p = {0, 0, 0};
        uint32_t i, c;

        for (i = 0; i < 6U; ++i)
            out[i] = (uint8_t)msf_rng_next(&rng);
        for (i = 0; i < 6U; ++i)
            back[i] = (uint8_t)msf_rng_next(&rng);
        data[0] = (uint16_t)mode;
        data[1] = 6U;
        for (i = 0; i < 6U; ++i)
            data[2U + i] = out[i];
        if (fw_boot(&img_spi_master, data, 8U, 0))
            return -1;
        msf_spi_slave_init(&s, mode, back, 6U);
        for (c = 0; c < 20000U; ++c) {
            p = cycle(uio);
            uio = msf_spi_slave_cycle(&s, p);
            if (p.uo == DONE)
                break;
        }
        CHECK(c < 20000U, "mode %d: uo_out never showed DONE", mode);
        CHECK(s.ngot == 6U && memcmp(s.got, out, 6U) == 0,
              "mode %d: the slave received %d bytes, not what the master sent", mode,
              (int)s.ngot);
        CHECK(((p.out >> 2) & 1U) == (unsigned)(mode >> 1), "mode %d: SCK idle level != CPOL",
              mode);
        if (dump_ram(64U, 6U, got))
            return -1;
        for (i = 0; i < 6U; ++i)
            CHECK(got[i] == back[i], "mode %d: master stored %x, slave sent %x", mode,
                  (unsigned)got[i], (unsigned)back[i]);
    }
    return 0;
}

/* test_firmware.i2c_run: one i2c_master.s transaction against the slave model */
static int i2c_run(uint8_t addr, const uint8_t *wr, uint32_t nwr, uint32_t nrd,
                   uint8_t slave_addr, const uint8_t *rdata, unsigned stretch,
                   uint32_t seed, msf_i2c_slave *s, uint8_t *status)
{
    uint16_t data[3 + 16];
    uint8_t uio = 0x60;                                /* SDA, SCL pulled up */
    msf_pins p = {0, 0, 0};
    uint32_t i, c;

    CHECK(nwr <= 16U, "too many write bytes");
    data[0] = addr;
    data[1] = (uint16_t)nwr;
    for (i = 0; i < nwr; ++i)
        data[2U + i] = wr[i];
    data[2U + nwr] = (uint16_t)nrd;
    if (fw_boot(&img_i2c_master, data, (uint16_t)(3U + nwr), uio))
        return -1;
    msf_i2c_slave_init(s, slave_addr, rdata, nrd, stretch, seed);
    for (c = 0; c < 200000U; ++c) {
        p = msf_step(uio);
        uio = msf_i2c_slave_cycle(s, p);
        if (p.uo & 0x80U)
            break;
    }
    CHECK(c < 200000U, "uo_out never showed the completion bit");
    for (c = 0; c < 50U; ++c) {
        p = msf_step(uio);
        uio = msf_i2c_slave_cycle(s, p);
    }
    *status = p.uo;
    return 0;
}

static int test_i2c_write_ack(void)
{
    static const uint8_t wr[3] = {0x12, 0xa5, 0xff};
    msf_i2c_slave s;
    uint8_t st;

    if (i2c_run(0x50, wr, 3, 0, 0x50, 0, 0, 0, &s, &st))
        return -1;
    CHECK(st == 0x83U, "status %x, want 0x83", (unsigned)st);
    CHECK(s.nwritten == 3U && memcmp(s.written, wr, 3U) == 0, "slave received the wrong bytes");
    CHECK(strcmp(s.events, "SP") == 0, "bus events %s, want SP", s.events);
    return 0;
}

static int test_i2c_address_nack(void)
{
    static const uint8_t wr[1] = {0x12};
    msf_i2c_slave s;
    uint8_t st;

    if (i2c_run(0x51, wr, 1, 0, 0x50, 0, 0, 0, &s, &st))
        return -1;
    CHECK(st == 0x80U, "status %x: the address must be NACKed", (unsigned)st);
    CHECK(s.nwritten == 0U && strcmp(s.events, "SP") == 0, "bus events %s, want SP", s.events);
    return 0;
}

static int test_i2c_write_then_read_repeated_start(void)
{
    static const uint8_t wr[1] = {0x00};
    static const uint8_t rd[4] = {0xde, 0xad, 0xbe, 0xef};
    msf_i2c_slave s;
    uint16_t got[4];
    uint8_t st;
    uint32_t i;

    if (i2c_run(0x50, wr, 1, 4, 0x50, rd, 0, 0, &s, &st))
        return -1;
    CHECK(st == 0x83U, "status %x, want 0x83", (unsigned)st);
    CHECK(s.nacks == 4U && s.master_acks[0] && s.master_acks[1] && s.master_acks[2] &&
          !s.master_acks[3], "the master must ACK all but the last byte");
    CHECK(strcmp(s.events, "SSP") == 0, "bus events %s, want SSP (repeated START)", s.events);
    if (dump_ram(64U, 4U, got))
        return -1;
    for (i = 0; i < 4U; ++i)
        CHECK(got[i] == rd[i], "read byte %d = %x, want %x", (int)i, (unsigned)got[i],
              (unsigned)rd[i]);
    return 0;
}

static int test_i2c_constrained_random_with_stretching(void)
{
    msf_rng rng;
    uint32_t k;

    msf_rng_seed(&rng, 2027U);
    for (k = 0; k < 6U; ++k) {
        uint8_t wr[3], rd[2], addr, st;
        uint16_t got[2];
        uint32_t nwr, nrd, i;
        int hit;
        msf_i2c_slave s;

        addr = (uint8_t)msf_rng_range(&rng, 0x08, 0x78);
        hit = msf_rng_next(&rng) % 1000U < 700U;
        nwr = msf_rng_range(&rng, 1, 4);
        for (i = 0; i < nwr; ++i)
            wr[i] = (uint8_t)msf_rng_next(&rng);
        nrd = msf_rng_range(&rng, 0, 3);
        for (i = 0; i < nrd; ++i)
            rd[i] = (uint8_t)msf_rng_next(&rng);
        if (i2c_run(addr, wr, nwr, nrd, hit ? addr : (uint8_t)(addr ^ 1U), rd, 40, k, &s, &st))
            return -1;
        if (hit) {
            CHECK(st == 0x83U && s.nwritten == nwr && memcmp(s.written, wr, nwr) == 0,
                  "case %d: status %x, %d bytes written", (int)k, (unsigned)st,
                  (int)s.nwritten);
            if (nrd) {
                if (dump_ram(64U, (uint16_t)nrd, got))
                    return -1;
                for (i = 0; i < nrd; ++i)
                    CHECK(got[i] == rd[i], "case %d: read byte %d = %x, want %x", (int)k,
                          (int)i, (unsigned)got[i], (unsigned)rd[i]);
            }
        } else {
            CHECK(st == 0x80U && s.nwritten == 0U, "case %d: status %x on a missing slave",
                  (int)k, (unsigned)st);
        }
    }
    return 0;
}

/* test_fw_usb_ls_tx_packet */
static int test_usb_ls_tx_packet(void)
{
    static const uint8_t payload[8] = {0x80, 0x06, 0x00, 0x01, 0xff, 0xff, 0x12, 0x00};
    const uint8_t pid = 0xc3;                          /* DATA0 */
    uint16_t data[10];
    uint8_t want[12], got[16];
    uint16_t crc = msf_usb_crc16(payload, 8);
    uint32_t i, c, start = 0, nbytes = 0, eop = 0, se0 = 0, j = 0;
    int found = 0;

    data[0] = pid;
    data[1] = 8U;
    for (i = 0; i < 8U; ++i)
        data[2U + i] = payload[i];
    if (fw_boot(&img_usb_ls_tx, data, 10U, 0))
        return -1;
    for (c = 0; c < 20000U; ++c)
        if (cycle(0).uo == DONE)
            break;
    CHECK(c < 20000U, "uo_out never showed DONE");

    for (i = 0; i < tr.n && !found; ++i)
        if (msf_usb_state(tr.p[i]) == 'K') {
            start = i;
            found = 1;
        }
    CHECK(found, "no K state on the bus");
    for (i = start + 1U; i < tr.n; ++i)               /* every change on the 8-cycle grid */
        if (msf_usb_state(tr.p[i]) != msf_usb_state(tr.p[i - 1U]))
            CHECK((i - start) % 8U == 0U, "line change %d cycles after SYNC is off the grid",
                  (int)(i - start));
    CHECK(msf_usb_decode(&tr, start, got, sizeof got, &nbytes, &eop) == 0,
          "NRZI / bit-stuffing decode failed");
    want[0] = 0x80;
    want[1] = pid;
    memcpy(want + 2, payload, 8);
    want[10] = (uint8_t)crc;
    want[11] = (uint8_t)(crc >> 8);
    CHECK(nbytes == 12U && memcmp(got, want, 12U) == 0, "packet bytes differ (%d decoded)",
          (int)nbytes);
    while (eop + se0 < tr.n && msf_usb_state(tr.p[eop + se0]) == '0')
        ++se0;
    CHECK(se0 == 16U, "SE0 lasted %d cycles, want 16 (2 bit times)", (int)se0);
    while (eop + se0 + j < tr.n && msf_usb_state(tr.p[eop + se0 + j]) == 'J')
        ++j;
    CHECK(j == 8U, "J after the EOP lasted %d cycles, want 8", (int)j);
    CHECK(eop + se0 + j < tr.n && msf_usb_state(tr.p[eop + se0 + j]) == 'z',
          "pins not released after the EOP");
    return 0;
}

/* test_fw_usb_ls_rx_packet: the PS drives D-/D+ (with +-1.5 % clock error) */
static int test_usb_ls_rx_packet(void)
{
    static const struct { int corrupt; int32_t ppm; } runs[4] = {
        {0, 0}, {0, 15000}, {0, -15000}, {1, 0}};
    uint8_t payload[10], pkt[14], lv[160];
    msf_rng rng;
    uint16_t crc;
    uint32_t i, r;

    msf_rng_seed(&rng, 9U);
    for (i = 0; i < 8U; ++i)
        payload[i] = (uint8_t)msf_rng_next(&rng);
    payload[8] = 0xff;
    payload[9] = 0xff;
    crc = msf_usb_crc16(payload, 10);
    pkt[0] = 0x80;                                     /* SYNC */
    pkt[1] = 0x4b;                                     /* DATA1 */
    memcpy(pkt + 2, payload, 10);
    pkt[12] = (uint8_t)crc;
    pkt[13] = (uint8_t)(crc >> 8);

    for (r = 0; r < 4U; ++r) {
        uint32_t nlv = msf_usb_levels(pkt, 14, runs[r].corrupt, lv, sizeof lv), k, c;
        int64_t num = 8LL * (1000000LL + runs[r].ppm);  /* cycles per bit * 1e6 */
        msf_pins p = {0, 0, 0};
        uint16_t got[15];

        if (fw_boot(&img_usb_ls_rx, 0, 0, 0x01))       /* idle J */
            return -1;
        for (c = 0; c < 2000U; ++c)
            if (cycle(0x01).uo == 0x40U)
                break;
        CHECK(c < 2000U, "run %d: receiver never armed", (int)r);
        for (c = 0; c < 40U; ++c)
            cycle(0x01);
        for (k = 0; k < nlv + 4U; ++k) {               /* packet, SE0 SE0, J J */
            uint32_t n = (uint32_t)(((int64_t)(k + 1U) * num + 500000) / 1000000 -
                                    ((int64_t)k * num + 500000) / 1000000);
            uint8_t uio = k < nlv ? (lv[k] ? 0x01U : 0x02U) : (k < nlv + 2U ? 0x00U : 0x01U);
            for (c = 0; c < n; ++c)
                cycle(uio);
        }
        for (c = 0; c < 3000U; ++c)
            if ((p = cycle(0x01)).uo & 0x80U)
                break;
        CHECK(c < 3000U, "run %d: no result on uo_out", (int)r);
        if (runs[r].corrupt) {
            CHECK(p.uo == 0x82U, "corrupted packet accepted (status %x)", (unsigned)p.uo);
        } else {
            CHECK(p.uo == 0x81U, "ppm %d: status %x, want 0x81", (int)runs[r].ppm,
                  (unsigned)p.uo);
            if (dump_ram(63U, 15U, got))
                return -1;
            CHECK(got[0] == 14U, "ppm %d: received %d bytes, want 14", (int)runs[r].ppm,
                  (int)got[0]);
            for (i = 0; i < 14U; ++i)
                CHECK(got[1U + i] == pkt[i], "ppm %d: byte %d = %x, want %x",
                      (int)runs[r].ppm, (int)i, (unsigned)got[1U + i], (unsigned)pkt[i]);
        }
    }
    return 0;
}

/* test_fw_10baset_frame_fcs_tpidl_nlp (NLP interval scaled as in cocotb) */
static int test_eth_10baset_frame_fcs_tpidl_nlp(void)
{
    static const char text[] = "msfcore 10BASE-T: bit-banged by a 16-bit protocol CPU!";
    uint8_t frame[60], got[80];
    uint16_t data[31];
    uint32_t i, c, nbytes = 0, end = 0, tp = 0, npulse = 0, pulse[2] = {0, 0};
    uint32_t fcs;

    memset(frame, 0xff, 6);                             /* broadcast DA */
    frame[6] = 0x02;                                    /* SA 02:00:00:00:00:01 */
    memset(frame + 7, 0, 4);
    frame[11] = 0x01;
    frame[12] = 0x88;                                   /* EtherType 0x88B5 */
    frame[13] = 0xb5;
    memcpy(frame + 14, text, 46);
    data[0] = 30U;
    for (i = 0; i < 30U; ++i)
        data[1U + i] = (uint16_t)(frame[2U * i] | frame[2U * i + 1U] << 8);
    if (fw_boot(&img_eth_10baset_tx, data, 31U, 0))
        return -1;
    for (c = 0; c < 30000U; ++c)
        if (cycle(0).uo == 2U)                          /* two link pulses sent */
            break;
    CHECK(c < 30000U, "uo_out never counted two link pulses");

    CHECK(msf_eth_decode(&tr, got, sizeof got, &nbytes, &end) == 0,
          "Manchester decode failed (TD- not complementary, or not whole bytes)");
    CHECK(nbytes == 72U, "decoded %d bytes, want 72", (int)nbytes);
    for (i = 0; i < 7U; ++i)
        CHECK(got[i] == 0x55U, "preamble byte %d = %x", (int)i, (unsigned)got[i]);
    CHECK(got[7] == 0xd5U, "SFD = %x", (unsigned)got[7]);
    CHECK(memcmp(got + 8, frame, 60) == 0, "frame bytes differ");
    fcs = (uint32_t)got[68] | (uint32_t)got[69] << 8 | (uint32_t)got[70] << 16 |
          (uint32_t)got[71] << 24;
    CHECK(fcs == msf_eth_fcs(frame, 60), "FCS %x, want %x", (unsigned)fcs,
          (unsigned)msf_eth_fcs(frame, 60));
    while (end + tp < tr.n && ((tr.p[end + tp].out >> 2) & 1U) && !((tr.p[end + tp].out >> 3) & 1U))
        ++tp;
    CHECK(tp == 6U, "TP_IDL lasted %d cycles, want 6 (300 ns at 20 MHz)", (int)tp);
    for (i = end + tp; i < end + tp + 100U && i < tr.n; ++i)
        CHECK(!(tr.p[i].out & 0x0cU), "line not idle after TP_IDL");
    for (i = end + tp; i < tr.n; ++i) {
        if (((tr.p[i].out >> 2) & 1U) && !((tr.p[i - 1U].out >> 2) & 1U)) {
            uint32_t w = 0;
            CHECK(npulse < 2U, "more than two link pulses");
            while (i + w < tr.n && ((tr.p[i + w].out >> 2) & 1U))
                ++w;
            CHECK(w == 2U, "link pulse %d is %d cycles wide, want 2", (int)npulse, (int)w);
            pulse[npulse++] = i;
        }
    }
    CHECK(npulse == 2U, "%d link pulses, want 2", (int)npulse);
    CHECK(pulse[1] - pulse[0] == MSF_ETH_NLP_CHUNK * MSF_ETH_NLP_CHUNKS,
          "link pulse period %d cycles, want %d", (int)(pulse[1] - pulse[0]),
          (int)(MSF_ETH_NLP_CHUNK * MSF_ETH_NLP_CHUNKS));
    return 0;
}

/* test_fw_threads_blink_and_echo: the PS is the UART host on uio[1] */
static int test_threads_blink_and_echo(void)
{
    static const uint8_t msg[3] = {'h', 'i', '!'};
    int dec[8];
    uint32_t c, n, i, nedges = 0, prev = 0;

    if (fw_boot(&img_thread_demo, 0, 0, 0x02))         /* RX line idles high */
        return -1;
    for (c = 0; c < 300U; ++c)
        cycle(0x02);
    for (c = 0; c < 3U * 10U * 104U; ++c)
        cycle(msf_uart_level(msg, 3, 104, c) ? 0x02U : 0x00U);
    for (c = 0; c < 2500U; ++c)
        cycle(0x02);
    CHECK(!tr.overflow, "trace overflow");

    n = msf_uart_decode(&tr, 104, 0, dec, 8);
    CHECK(n == 3U && dec[0] == 'h' && dec[1] == 'i' && dec[2] == '!',
          "T1 echoed %d bytes, want \"hi!\"", (int)n);
    for (i = 1; i < tr.n; ++i) {
        if (((tr.p[i].out ^ tr.p[i - 1U].out) & 0x80U) != 0U) {
            if (nedges++)
                CHECK(i - prev == 500U, "T0 half period %d cycles, want exactly 500",
                      (int)(i - prev));
            prev = i;
        }
    }
    CHECK(nedges > 10U, "only %d edges on uio[7]", (int)nedges);
    return 0;
}

/* ======================================================================== */
/* Debug ports and buses: the probes are driven over the UART like a PC
 * would (one command, then its reply), against the C device models.       */

#define PROBE_BIT 104U

/* Run `img`; send each command once the previous one's replies are in.
 * cmds: a list of {length, bytes...} entries, nrep[i] the replies each
 * expects. Returns the replies in u->rx. */
static int probe_run(const msf_image *img, msf_uart_host *u, const uint8_t *cmds,
                     const uint8_t *nrep, int ncmd, uint8_t pull,
                     uint8_t (*dev)(void *, msf_pins), void *ctx, uint32_t limit)
{
    uint32_t c, want = 0;
    uint8_t uio = (uint8_t)(0x02U | pull);
    int i = 0;
    const uint8_t *cp = cmds;

    if (fw_boot(img, 0, 0, uio))
        return -1;
    msf_uart_host_init(u, PROBE_BIT);
    for (c = 0; c < limit; ++c) {
        msf_pins p;
        uint8_t devin;

        /* (the first command after 400 cycles, once the firmware is set up) */
        if (c >= 400U && i < ncmd && msf_uart_host_idle(u) && u->nrx >= want) {
            msf_uart_host_send(u, cp + 1, cp[0]);
            want += nrep[i];
            cp += 1 + cp[0];
            ++i;
        }
        p = cycle(uio);
        devin = dev ? dev(ctx, p) : pull;
        uio = (uint8_t)((msf_uart_host_cycle(u, p) << 1) | devin);
        if (i == ncmd && u->nrx >= want && msf_uart_host_idle(u))
            break;
    }
    CHECK(c < limit, "only %d of %d reply bytes", (int)u->nrx, (int)want);
    return 0;
}

static uint8_t jtag_dev(void *ctx, msf_pins p)
{
    return msf_jtag_cycle((msf_jtag_chain *)ctx, p);
}

static uint8_t ow_dev(void *ctx, msf_pins p)
{
    return msf_ow_bus_cycle((msf_ow_bus *)ctx, p);
}

/* test_debugports.test_jtag_idcode, _ir_bypass_and_partial_bytes, _chain */
static int test_jtag_probe(void)
{
    static const uint8_t cmds1[] = {1, 'R', 6, 'D', 32, 0, 0, 0, 0, 3, 'I', 4, 0x0F,
                                    3, 'D', 8, 0xA5, 4, 'D', 13, 0xFF, 0xFF};
    static const uint8_t rep1[] = {1, 4, 1, 1, 2};
    static const uint8_t exp1[] = {'R', 0x79, 0x56, 0x34, 0x12, 0x01, 0x4A, 0xFE, 0x1F};
    static const uint8_t cmds2[] = {1, 'R', 10, 'D', 64, 0, 0, 0, 0, 0, 0, 0, 0, 3, 'I', 8, 0xFF,
                                    4, 'D', 16, 0xFF, 0xFF};
    static const uint8_t rep2[] = {1, 8, 1, 2};
    static const uint8_t exp2[] = {'R', 0x77, 0x04, 0xA0, 0x4B, 0x77, 0x04, 0xA0, 0x0B,
                                   0x11, 0xFC, 0xFF};
    uint32_t one = 0x12345679U, two[2] = {0x0BA00477U, 0x4BA00477U};
    msf_jtag_chain ch;
    msf_uart_host u;

    msf_jtag_init(&ch, &one, 1);
    if (probe_run(&img_jtag, &u, cmds1, rep1, 5, 0, jtag_dev, &ch, 400000U))
        return -1;
    CHECK(u.nrx == sizeof exp1 && memcmp(u.rx, exp1, sizeof exp1) == 0,
          "one TAP: replies %02x %02x %02x %02x %02x ...", u.rx[0], u.rx[1], u.rx[2], u.rx[3],
          u.rx[4]);
    CHECK(ch.errors == 0, "TDI/TMS changed while TCK was high, or too close to its rise");
    msf_jtag_init(&ch, two, 2);
    if (probe_run(&img_jtag, &u, cmds2, rep2, 4, 0, jtag_dev, &ch, 600000U))
        return -1;
    CHECK(u.nrx == sizeof exp2 && memcmp(u.rx, exp2, sizeof exp2) == 0,
          "two TAPs: replies %02x %02x %02x ... %02x %02x", u.rx[0], u.rx[1], u.rx[2], u.rx[9],
          u.rx[10]);
    return 0;
}

/* test_buses.test_onewire_reset_read_rom_scratchpad and _search_rom */
static int test_onewire_probe(void)
{
    static const uint8_t cmds1[] = {1, 'R', 3, 'W', 1, 0x33, 2, 'r', 8};
    static const uint8_t rep1[] = {1, 1, 8};
    static const uint8_t cmds2[] = {1, 'S'};
    static const uint8_t rep2[] = {3 * 9 + 1};
    msf_ow_dev devs[3];
    msf_ow_bus bus;
    msf_uart_host u;
    int i, j, found[3] = {0, 0, 0};

    msf_ow_dev_init(&devs[0], 0x28, 0x0000123456ABULL);
    msf_ow_bus_init(&bus, devs, 1);
    if (probe_run(&img_onewire, &u, cmds1, rep1, 3, 0x08U, ow_dev, &bus, 400000U))
        return -1;
    CHECK(u.rx[0] == 'P' && u.rx[1] == 'W' && memcmp(u.rx + 2, devs[0].rom, 8U) == 0,
          "READ ROM: replies %02x %02x %02x ...", u.rx[0], u.rx[1], u.rx[2]);
    CHECK(msf_crc8_maxim(u.rx + 2, 7U) == u.rx[9], "ROM CRC-8");
    msf_ow_dev_init(&devs[0], 0x28, 0x0000123456ABULL);
    msf_ow_dev_init(&devs[1], 0x28, 0x0000123456ACULL);
    msf_ow_dev_init(&devs[2], 0x10, 0x00000000BEEFULL);
    msf_ow_bus_init(&bus, devs, 3);
    if (probe_run(&img_onewire, &u, cmds2, rep2, 1, 0x08U, ow_dev, &bus, 3000000U))
        return -1;
    CHECK(u.rx[27] == 'E', "SEARCH ROM did not end with E");
    for (i = 0; i < 3; ++i) {
        CHECK(u.rx[9 * i] == 'D', "SEARCH ROM: device %d not reported", i);
        for (j = 0; j < 3; ++j)
            if (memcmp(u.rx + 9 * i + 1, devs[j].rom, 8U) == 0)
                ++found[j];
    }
    CHECK(found[0] == 1 && found[1] == 1 && found[2] == 1, "SEARCH ROM found %d %d %d",
          found[0], found[1], found[2]);
    return 0;
}

/* ======================================================================== */
/* Device emulation: the chip as an SPI target or SPI flash, clocked by the */
/* C SPI controller.                                                        */

/* uio_in while deselected: CS high, SCK at its idle level */
#define SPI_IDLE(mode) ((uint8_t)((((mode) & 2) ? 0x04U : 0U) | 0x20U))

static int spi_ctl_run(msf_spi_ctl *ctl, int mode)
{
    uint8_t uio = SPI_IDLE(mode);
    uint32_t c;

    for (c = 0; c < MSF_SPI_WAVE + 100U && !msf_spi_ctl_done(ctl); ++c)
        uio = msf_spi_ctl_cycle(ctl, cycle(uio));
    CHECK(msf_spi_ctl_done(ctl), "the SPI controller did not finish");
    return 0;
}

/* test_targets.test_spi_target_all_modes (modes 0 and 3, SCK = clk/20) */
static int test_spi_target_regs(void)
{
    static msf_spi_ctl ctl;
    static const int modes[2] = {0, 3};
    const msf_image *imgs[2] = {&img_spi_target_m0, &img_spi_target_m3};
    msf_rng rng;
    int m;

    msf_rng_seed(&rng, 7U);
    for (m = 0; m < 2; ++m) {
        uint16_t data[65];
        uint8_t regs[64], bytes[64], lens[12], want[64];
        uint32_t ntr = 0, nb = 0, k = 0, off = 0, i, t;

        for (i = 0; i < 64U; ++i) {
            regs[i] = (uint8_t)msf_rng_next(&rng);
            data[i] = regs[i];
        }
        data[64] = 0xA5U;
        /* random_spi_case: a write across the wrap, its read-back, a command
         * alone, then random reads and writes (bit 6 of the address ignored) */
        lens[ntr++] = 6U;
        bytes[nb++] = 0x3DU;
        for (i = 0; i < 5U; ++i)
            bytes[nb++] = (uint8_t)msf_rng_next(&rng);
        lens[ntr++] = 6U;
        bytes[nb++] = 0xBDU;
        for (i = 0; i < 5U; ++i)
            bytes[nb++] = 0U;
        lens[ntr++] = 1U;
        bytes[nb++] = (uint8_t)(0x80U | msf_rng_range(&rng, 0U, 64U));
        for (t = 0; t < 6U; ++t) {
            uint32_t n = msf_rng_range(&rng, 1U, 5U);

            bytes[nb++] = (uint8_t)((msf_rng_next(&rng) & 0xC0U) | msf_rng_range(&rng, 0U, 64U));
            for (i = 0; i < n; ++i)
                bytes[nb++] = (uint8_t)msf_rng_next(&rng);
            lens[ntr++] = (uint8_t)(n + 1U);
        }
        /* spi_ref: the status during the command byte, then the registers */
        for (t = 0; t < ntr; ++t) {
            unsigned addr = bytes[off] & 0x3FU, rd = bytes[off] >> 7;

            want[k++] = 0xA5U;
            for (i = 1; i < lens[t]; ++i) {
                want[k++] = regs[addr];
                if (!rd)
                    regs[addr] = bytes[off + i];
                addr = (addr + 1U) & 0x3FU;
            }
            off += lens[t];
        }
        if (fw_boot(imgs[m], data, 65U, SPI_IDLE(modes[m])))
            return -1;
        CHECK(msf_spi_ctl_init(&ctl, modes[m], bytes, lens, ntr, 10U, 8U, 40U) == 0,
              "SPI waveform too long");
        if (spi_ctl_run(&ctl, modes[m]))
            return -1;
        for (i = 0; i < k; ++i)
            CHECK(msf_spi_ctl_byte(&ctl, i) == want[i], "mode %d: MISO byte %d = %02x, want %02x",
                  modes[m], (int)i, msf_spi_ctl_byte(&ctl, i), want[i]);
    }
    return 0;
}

/* fw_boot with a 512-byte flash store loaded at SRAM 512 */
static int flash_boot(const msf_image *img, const uint8_t *store, uint8_t uio)
{
    static const uint16_t zeros[128];
    uint16_t w[128];
    uint32_t i, j;

    TRY(msf_boot_enter(0));
    TRY(msf_boot_write(0, zeros, 128));
    for (j = 0; j < 4U; ++j) {
        for (i = 0; i < 128U; ++i)
            w[i] = store[128U * j + i];
        TRY(msf_boot_write((uint16_t)(512U + 128U * j), w, 128));
    }
    TRY(msf_boot_write(img->addr, img->words, img->count));
    TRY(msf_reset(MSF_BOOT_DIRECT, uio, 1));
    TRY(msf_run());
    msf_trace_init(&tr, trace_buf, TRACE_CAP);
    return 0;
}

/* test_targets.test_flash_id_status_read, _program and _erase, in short
 * (modes 0 and 3, SCK = clk/32, CS high 800 cycles so the erase advances) */
static int test_spi_flash(void)
{
    static msf_spi_ctl ctl;
    static const int modes[2] = {0, 3};
    static const uint8_t bytes[] = {
        0x9F, 0, 0, 0, 0, 0, 0,                   /*  0: ID, twice */
        0x05, 0,                                  /*  1: status */
        0x03, 0x12, 0x01, 0xFC, 0, 0, 0, 0, 0, 0, 0, /* 2: READ across the 512 wrap */
        0x06,                                     /*  3: WREN */
        0x02, 0, 0x00, 0x40, 0xF0, 0x3C, 0x5A,    /*  4: page program 40..42 */
        0x03, 0, 0x00, 0x40, 0, 0, 0,             /*  5: read it back */
        0x05, 0,                                  /*  6: WEL off again */
        0x06,                                     /*  7: WREN */
        0x20, 0, 0, 0,                            /*  8: sector erase */
        0x05, 0, 0x05, 0, 0x05, 0, 0x05, 0, 0x05, 0, 0x05, 0, /* 9-20: polls */
        0x05, 0, 0x05, 0, 0x05, 0, 0x05, 0, 0x05, 0, 0x05, 0,
        0x03, 0, 0x01, 0xF0, 0, 0, 0, 0,          /* 21: erased */
    };
    static const uint8_t lens[] = {7, 2, 11, 1, 7, 7, 2, 1, 4, 2, 2, 2, 2, 2, 2,
                                   2, 2, 2, 2, 2, 2, 8};
    static const uint8_t id[3] = {0xEF, 0x40, 0x09}, prog[3] = {0xF0, 0x3C, 0x5A};
    const msf_image *imgs[2] = {&img_spi_flash_m0, &img_spi_flash_m3};
    uint32_t start[sizeof lens];
    msf_rng rng;
    int m;

    msf_rng_seed(&rng, 11U);
    start[0] = 0;
    for (m = 1; m < (int)sizeof lens; ++m)
        start[m] = start[m - 1] + lens[m - 1];
    for (m = 0; m < 2; ++m) {
        uint8_t store[512], st[12];
        uint32_t i;
        int busy = 1;

        for (i = 0; i < 512U; ++i)
            store[i] = (uint8_t)msf_rng_next(&rng);
        if (flash_boot(imgs[m], store, SPI_IDLE(modes[m])))
            return -1;
        CHECK(msf_spi_ctl_init(&ctl, modes[m], bytes, lens, sizeof lens, 16U, 32U, 800U) == 0,
              "SPI waveform too long");
        if (spi_ctl_run(&ctl, modes[m]))
            return -1;
#define FB(t, i) msf_spi_ctl_byte(&ctl, start[t] + (i))
        for (i = 0; i < 6U; ++i)
            CHECK(FB(0, 1U + i) == id[i % 3U], "mode %d: ID byte %d = %02x", modes[m], (int)i,
                  FB(0, 1U + i));
        CHECK(FB(1, 1) == 0x00U, "mode %d: status %02x at reset", modes[m], FB(1, 1));
        for (i = 0; i < 7U; ++i)
            CHECK(FB(2, 4U + i) == store[(0x1FCU + i) % 512U], "mode %d: READ byte %d = %02x",
                  modes[m], (int)i, FB(2, 4U + i));
        for (i = 0; i < 3U; ++i)
            CHECK(FB(5, 4U + i) == (store[0x40U + i] & prog[i]),
                  "mode %d: programmed byte %d = %02x", modes[m], (int)i, FB(5, 4U + i));
        CHECK(FB(6, 1) == 0x00U, "mode %d: WEL still set after the program", modes[m]);
        for (i = 0; i < 12U; ++i) {
            st[i] = FB(9U + i, 1);
            CHECK(st[i] == 0x03U || st[i] == 0x00U, "mode %d: status poll %d = %02x", modes[m],
                  (int)i, st[i]);
            CHECK(!(st[i] == 0x03U && !busy), "mode %d: WIP set again at poll %d", modes[m],
                  (int)i);
            busy = st[i] == 0x03U;
        }
        CHECK(st[0] == 0x03U && st[11] == 0x00U, "mode %d: polls %02x .. %02x", modes[m], st[0],
              st[11]);
        for (i = 0; i < 4U; ++i)
            CHECK(FB(21, 4U + i) == 0xFFU, "mode %d: not erased: %02x", modes[m], FB(21, 4U + i));
#undef FB
    }
    return 0;
}

/* the chip as an I2C target (24C01-style EEPROM), clocked by the C I2C
 * controller; ops as in test_targets.i2c_write / i2c_read */
static void i2c_write_ops(uint16_t *ops, uint32_t *n, unsigned addr, unsigned ptr,
                          const uint8_t *d, uint32_t nd)
{
    uint32_t i;

    ops[(*n)++] = MSF_I2C_START;
    ops[(*n)++] = (uint16_t)MSF_I2C_BYTE(addr << 1);
    ops[(*n)++] = (uint16_t)MSF_I2C_BYTE(ptr);
    for (i = 0; i < nd; ++i)
        ops[(*n)++] = (uint16_t)MSF_I2C_BYTE(d[i]);
    ops[(*n)++] = MSF_I2C_STOP;
}

/* ptr < 0: a current-address read; else a random read (pointer, repeated START) */
static void i2c_read_ops(uint16_t *ops, uint32_t *n, unsigned addr, uint32_t cnt, int ptr)
{
    uint32_t i;

    if (ptr >= 0) {
        ops[(*n)++] = MSF_I2C_START;
        ops[(*n)++] = (uint16_t)MSF_I2C_BYTE(addr << 1);
        ops[(*n)++] = (uint16_t)MSF_I2C_BYTE((unsigned)ptr);
    }
    ops[(*n)++] = MSF_I2C_START;
    ops[(*n)++] = (uint16_t)MSF_I2C_BYTE(addr << 1 | 1U);
    for (i = 0; i < cnt; ++i)
        ops[(*n)++] = (uint16_t)MSF_I2C_READ(i + 1U < cnt);
    ops[(*n)++] = MSF_I2C_STOP;
}

/* test_targets.i2c_ref: expected ACK bits / read bytes, and the memory after */
static uint32_t i2c_ref(uint8_t *mem, unsigned addr, const uint16_t *ops, uint32_t n,
                        uint8_t *out)
{
    enum { IDLE, ADR, RD, PTR, DAT } st = IDLE;
    unsigned ptr = 0;
    uint32_t i, k = 0;

    for (i = 0; i < n; ++i) {
        unsigned op = ops[i] & 0xF00U, v = ops[i] & 0xFFU;

        if (op == MSF_I2C_START) {
            st = ADR;
        } else if (op == MSF_I2C_STOP) {
            st = IDLE;
        } else if (op == 0x300U) {
            if (st == ADR) {
                int us = (v >> 1) == addr;
                out[k++] = us ? 0U : 1U;
                st = us ? ((v & 1U) ? RD : PTR) : IDLE;
            } else if (st == PTR) {
                ptr = v & 0x7FU;
                st = DAT;
                out[k++] = 0U;
            } else if (st == DAT) {
                mem[ptr] = (uint8_t)v;
                ptr = (ptr + 1U) & 0x7FU;
                out[k++] = 0U;
            } else {
                out[k++] = 1U;
            }
        } else if (op == 0x400U) {
            if (st == RD) {
                out[k++] = mem[ptr];
                ptr = (ptr + 1U) & 0x7FU;
            } else {
                out[k++] = 0xFFU;
            }
        }
    }
    return k;
}

static int i2c_target_case(const msf_image *img, msf_rng *rng, uint32_t tlow, uint32_t thigh,
                           uint32_t thd, uint32_t tss, uint32_t tbuf, int stretch)
{
    static msf_i2c_ctl ctl;
    static const uint8_t first[3] = {0x11, 0x22, 0x33};
    uint16_t ops[160], data[128], got[128];
    uint8_t mem[128], want[128];
    uint32_t n = 0, nwant, i, t, c;
    uint8_t uio = 0x60U;                       /* SDA and SCL pulled up */

    for (i = 0; i < 128U; ++i) {
        mem[i] = (uint8_t)msf_rng_next(rng);
        data[i] = mem[i];
    }
    /* test_i2c_target_eeprom: a write across the wrap, its read-back, then
     * writes, current and random reads, and a transfer for another address */
    i2c_write_ops(ops, &n, 0x50U, 0x7EU, first, 3U);
    i2c_read_ops(ops, &n, 0x50U, 4U, 0x7E);
    for (t = 0; t < 6U; ++t) {
        uint8_t d[3];
        unsigned ptr = msf_rng_range(rng, 0U, 128U), kind = msf_rng_range(rng, 0U, 4U);
        uint32_t nd = msf_rng_range(rng, 1U, 4U);

        for (i = 0; i < 3U; ++i)
            d[i] = (uint8_t)msf_rng_next(rng);
        if (kind == 0U)
            i2c_write_ops(ops, &n, 0x50U, ptr, d, nd);
        else if (kind == 1U)
            i2c_read_ops(ops, &n, 0x50U, nd, -1);
        else if (kind == 2U)
            i2c_read_ops(ops, &n, 0x50U, nd, (int)ptr);
        else
            i2c_write_ops(ops, &n, 0x51U, ptr, d, 2U);
    }
    nwant = i2c_ref(mem, 0x50U, ops, n, want);
    if (fw_boot(img, data, 128U, uio))
        return -1;
    CHECK(msf_i2c_ctl_init(&ctl, ops, n, tlow, thigh, thd, tss, tbuf) == 0,
          "I2C program too long");
    for (c = 0; c < 2000000U && !msf_i2c_ctl_done(&ctl); ++c)
        uio = msf_i2c_ctl_cycle(&ctl, cycle(uio));
    CHECK(msf_i2c_ctl_done(&ctl), "the I2C controller did not finish");
    CHECK(ctl.nres == nwant, "%d results, want %d", (int)ctl.nres, (int)nwant);
    for (i = 0; i < nwant; ++i)
        CHECK(ctl.results[i] == want[i], "result %d = %02x, want %02x", (int)i, ctl.results[i],
              want[i]);
    CHECK(ctl.violations == 0U, "SDA moved %d times while SCL was high", (int)ctl.violations);
    CHECK(stretch ? ctl.stretched > 0U : ctl.stretched == 0U, "SCL held low for %d cycles",
          (int)ctl.stretched);
    if (dump_ram(0U, 128U, got))
        return -1;
    for (i = 0; i < 128U; ++i)
        CHECK(got[i] == mem[i], "register %d = %02x after the writes, want %02x", (int)i,
              (unsigned)got[i], mem[i]);
    return 0;
}

/* test_targets.test_i2c_target_eeprom (100 kHz at 12 MHz, no stretching) and
 * test_i2c_target_fast_mode (400 kHz, STRETCH=1 holds SCL low to keep up) */
static int test_i2c_target_eeprom(void)
{
    msf_rng rng;

    msf_rng_seed(&rng, 21U);
    if (i2c_target_case(&img_i2c_target, &rng, 60U, 60U, 6U, 60U, 80U, 0))
        return -1;
    return i2c_target_case(&img_i2c_target_stretch, &rng, 16U, 14U, 2U, 8U, 16U, 1);
}

static uint8_t swd_dev(void *ctx, msf_pins p)
{
    return msf_swd_cycle((msf_swd_target *)ctx, p);
}

/* one 'T' command for probe_run: {length, 'T', header[, 4 data bytes LE]} */
static uint32_t swd_cmd(uint8_t *buf, uint32_t at, int ap, int rnw, int a, uint32_t v)
{
    uint32_t i;

    buf[at++] = (uint8_t)(rnw ? 2U : 6U);
    buf[at++] = 'T';
    buf[at++] = (uint8_t)(ap | rnw << 1 | ((a >> 2) & 1) << 2 | ((a >> 3) & 1) << 3);
    if (!rnw)
        for (i = 0; i < 4U; ++i)
            buf[at++] = (uint8_t)(v >> (8U * i));
    return at;
}

/* the replies to an OK read: ACK 1, the data LE, the parity bit */
static uint32_t swd_ok(uint8_t *buf, uint32_t at, uint32_t v)
{
    uint32_t i;
    int p = 0;

    buf[at++] = 1U;
    for (i = 0; i < 4U; ++i)
        buf[at++] = (uint8_t)(v >> (8U * i));
    for (i = 0; i < 32U; ++i)
        p ^= (int)((v >> i) & 1U);
    buf[at++] = (uint8_t)p;
    return at;
}

#define SWD_CHECK_REPLIES(u, exp, ne, what)                                           \
    do {                                                                              \
        uint32_t k_;                                                                  \
        CHECK((u).nrx == (ne), "%s: %d replies, want %d", what, (int)(u).nrx, (int)(ne)); \
        for (k_ = 0; k_ < (ne); ++k_)                                                 \
            CHECK((u).rx[k_] == (exp)[k_], "%s: reply %d = %02x, want %02x", what,       \
                  (int)k_, (u).rx[k_], (exp)[k_]);                                    \
    } while (0)

/* test_debugports.test_swd_dpidr, _memap_read_write, _wait_and_no_target */
static int test_swd_probe(void)
{
    static const uint32_t a0 = 0x20000000U, v0 = 0xDEADBEEFU, a1 = 0x0U, v1 = 0x12345678U;
    uint8_t cmds[96], nrep[16], exp[64];
    uint32_t nc, ne;
    int k;
    msf_swd_target t;
    msf_uart_host u;

    /* line reset and the JTAG-to-SWD switch, then DPIDR */
    nc = 0;
    cmds[nc++] = 1U;
    cmds[nc++] = 'L';
    nc = swd_cmd(cmds, nc, 0, 1, 0x0, 0);
    nrep[0] = 1U;
    nrep[1] = 6U;
    ne = 0;
    exp[ne++] = 'L';
    ne = swd_ok(exp, ne, MSF_SWD_DPIDR);
    msf_swd_init(&t, 0, 0, 0, 0);
    if (probe_run(&img_swd, &u, cmds, nrep, 2, 0x08U, swd_dev, &t, 400000U))
        return -1;
    SWD_CHECK_REPLIES(u, exp, ne, "DPIDR");
    CHECK(t.mode == 2 && t.errors == 0U, "target mode %d, %d errors", t.mode, (int)t.errors);

    /* SELECT bank F, the AP's IDR (posted: RDBUFF returns it), then CSW,
     * TAR, a posted DRW read and a DRW write */
    nc = 0;
    cmds[nc++] = 1U;
    cmds[nc++] = 'L';
    nc = swd_cmd(cmds, nc, 0, 0, 0x8, 0xF0U);
    nc = swd_cmd(cmds, nc, 1, 1, 0xC, 0);
    nc = swd_cmd(cmds, nc, 0, 1, 0xC, 0);
    nc = swd_cmd(cmds, nc, 0, 0, 0x8, 0x00U);
    nc = swd_cmd(cmds, nc, 1, 0, 0x0, 0x23000002U);
    nc = swd_cmd(cmds, nc, 1, 0, 0x4, a0);
    nc = swd_cmd(cmds, nc, 1, 1, 0xC, 0);
    nc = swd_cmd(cmds, nc, 0, 1, 0xC, 0);
    nc = swd_cmd(cmds, nc, 1, 0, 0xC, 0xCAFEF00DU);
    {
        static const uint8_t r[10] = {1, 1, 6, 6, 1, 1, 1, 6, 6, 1};
        memcpy(nrep, r, sizeof r);
    }
    ne = 0;
    exp[ne++] = 'L';
    exp[ne++] = 1U;
    ne = swd_ok(exp, ne, 0);
    ne = swd_ok(exp, ne, MSF_SWD_IDR);
    for (k = 0; k < 3; ++k)
        exp[ne++] = 1U;
    ne = swd_ok(exp, ne, MSF_SWD_IDR);
    ne = swd_ok(exp, ne, v0);
    exp[ne++] = 1U;
    msf_swd_init(&t, &a0, &v0, 1, 0);
    if (probe_run(&img_swd, &u, cmds, nrep, 10, 0x08U, swd_dev, &t, 2000000U))
        return -1;
    SWD_CHECK_REPLIES(u, exp, ne, "MEM-AP");
    CHECK(t.csw == 0x23000002U && t.tar == a0 && msf_swd_mem(&t, a0) == 0xCAFEF00DU &&
          t.errors == 0U, "CSW %08x TAR %08x mem %08x, %d errors", (unsigned)t.csw,
          (unsigned)t.tar, (unsigned)msf_swd_mem(&t, a0), (int)t.errors);

    /* a WAIT ends the transaction with no data phase; the retry succeeds */
    nc = 0;
    cmds[nc++] = 1U;
    cmds[nc++] = 'L';
    nc = swd_cmd(cmds, nc, 1, 1, 0xC, 0);
    nc = swd_cmd(cmds, nc, 1, 1, 0xC, 0);
    nc = swd_cmd(cmds, nc, 0, 1, 0xC, 0);
    nrep[0] = 1U;
    nrep[1] = 1U;
    nrep[2] = 6U;
    nrep[3] = 6U;
    ne = 0;
    exp[ne++] = 'L';
    exp[ne++] = 2U;
    ne = swd_ok(exp, ne, 0);
    ne = swd_ok(exp, ne, v1);
    msf_swd_init(&t, &a1, &v1, 1, 1U);
    if (probe_run(&img_swd, &u, cmds, nrep, 4, 0x08U, swd_dev, &t, 1000000U))
        return -1;
    SWD_CHECK_REPLIES(u, exp, ne, "WAIT");

    /* no target: the ACK reads 7 (SWDIO pulled high) */
    nc = 0;
    cmds[nc++] = 1U;
    cmds[nc++] = 'L';
    nc = swd_cmd(cmds, nc, 0, 1, 0x0, 0);
    nrep[0] = 1U;
    nrep[1] = 1U;
    if (probe_run(&img_swd, &u, cmds, nrep, 2, 0x08U, 0, 0, 400000U))
        return -1;
    CHECK(u.nrx >= 2U && u.rx[0] == 'L' && u.rx[1] == 7U, "no target: replies %02x %02x",
          u.rx[0], u.rx[1]);
    return 0;
}

/* four hardware threads (test_threads): three exact square waves at once,
 * and the FIFO handing T0's counts to T3 in order (four_threads.s) */
static int test_four_threads_fifo(void)
{
    static const uint32_t half[3] = {400U, 600U, 1000U};
    uint32_t c, i, k, prev[3] = {0, 0, 0}, nedge[3] = {0, 0, 0}, nuo = 0;
    unsigned last = 0;

    if (fw_boot(&img_four_threads, 0, 0, 0))
        return -1;
    for (c = 0; c < 12000U; ++c)
        cycle(0);
    CHECK(!tr.overflow, "trace overflow");
    for (i = 1; i < tr.n; ++i) {
        for (k = 0; k < 3U; ++k) {
            if (((tr.p[i].out ^ tr.p[i - 1U].out) & (0x10U << k)) != 0U) {
                if (nedge[k]++)
                    CHECK(i - prev[k] == half[k], "T%d half period %d cycles, want exactly %d",
                          (int)k, (int)(i - prev[k]), (int)half[k]);
                prev[k] = i;
            }
        }
        if (tr.p[i].uo != tr.p[i - 1U].uo) {
            CHECK(tr.p[i].uo == ((last + 1U) & 0xFFU),
                  "uo_out %d -> %d: the FIFO lost or repeated a word", (int)last,
                  (int)tr.p[i].uo);
            last = tr.p[i].uo;
            ++nuo;
        }
    }
    for (k = 0; k < 3U; ++k)
        CHECK(nedge[k] > 10U, "only %d edges from T%d", (int)nedge[k], (int)k);
    CHECK(nuo > 25U, "only %d words through the FIFO", (int)nuo);
    return 0;
}

/* ======================================================================== */
/* Bus monitors (test_monitors): the chip listens to a bus it does not
 * drive; thread 1 reports each event as two UART bytes, code then data, on
 * uio[0] at 12 cycles a bit (firmware/report.inc).                         */

#define MON_BIT    104U
#define REPORT_BIT 12U

/* one item on the monitored UART line (uio[1]): a byte at `bit` cycles a
 * bit with its stop bit at level `stop`, or (bit == 0) a low pulse of `v`
 * cycles; then `idle` cycles high (test_monitors.uart_wave) */
typedef struct {
    uint16_t v, bit;
    uint8_t stop;
    uint16_t idle;
} mon_item;

static uint32_t mon_item_len(const mon_item *it)
{
    return (it->bit ? 10U * it->bit : it->v) + it->idle;
}

static int mon_line(const mon_item *items, uint32_t n, uint32_t k)
{
    uint32_t i, b;

    for (i = 0; i < n; ++i) {
        const mon_item *it = &items[i];
        uint32_t len = mon_item_len(it);

        if (k < len) {
            if (!it->bit)
                return k >= it->v;
            if (k >= 10U * it->bit)
                return 1;
            b = k / it->bit;
            return b == 0U ? 0 : b == 9U ? (int)it->stop : (int)((it->v >> (b - 1U)) & 1U);
        }
        k -= len;
    }
    return 1;
}

/* the report stream as (code, data) pairs; -1 on a framing error or an
 * odd number of bytes */
static int mon_events(char *code, int *data, uint32_t max)
{
    static int by[512];
    uint32_t n = msf_uart_decode(&tr, REPORT_BIT, 0, by, 512), i;

    if (n % 2U || n / 2U > max)
        return -1;
    for (i = 0; i < n; ++i)
        if (by[i] < 0)
            return -1;
    for (i = 0; i < n / 2U; ++i) {
        code[i] = (char)by[2U * i];
        data[i] = by[2U * i + 1U];
    }
    return (int)(n / 2U);
}

/* mon_uart.s watching `items` from cycle 60 (test_monitors.mon_run) */
static int mon_uart_run(const mon_item *items, uint32_t n, char *code, int *data, uint32_t max)
{
    uint32_t k, total = 60U + 400U, i;

    for (i = 0; i < n; ++i)
        total += mon_item_len(&items[i]);
    if (fw_boot(&img_mon_uart, 0, 0, 0x02U))
        return -1;
    for (k = 0; k < total; ++k)
        cycle(k >= 60U && !mon_line(items, n, k - 60U) ? 0x00U : 0x02U);
    CHECK(!tr.overflow, "trace overflow");
    return mon_events(code, data, max);
}

/* the data bits a frame is low for before its first rising edge, with the
 * start bit (test_monitors.first_rise_cells) */
static int first_rise_cells(unsigned v)
{
    int n = 1;

    while (n < 9 && !((v >> (n - 1)) & 1U))
        ++n;
    return n;
}

static int wrap_diff(int a, int b)
{
    return ((a - b + 128) & 0xFF) - 128;
}

/* test_monitors.test_mon_uart_decodes_and_times: bytes, a framing error, a
 * glitch and frames at the wrong rate; a frame's timing offset is reported
 * when it exceeds BIT >> 4 */
static int test_mon_uart_decodes_and_times(void)
{
    static const mon_item items[8] = {
        {0x55, MON_BIT, 1, 200},     {0xA6, MON_BIT, 1, 200},
        {0x10, MON_BIT + 5U, 1, 200}, {0x01, MON_BIT + 5U, 1, 200},
        {0x80, MON_BIT - 3U, 1, 200}, {0x3C, MON_BIT, 0, 300},
        {20, 0, 1, 1500},            {0x42, MON_BIT, 1, 200},
    };
    char code[32], wc[32];
    int data[32], wd[32], n, i, nw = 0, tol = (int)(MON_BIT >> 4);

    for (i = 0; i < 8; ++i) {
        const mon_item *it = &items[i];

        if (!it->bit) {
            wc[nw] = 'G';
            wd[nw++] = it->v;
            wc[nw] = 'U';
            wd[nw++] = 0xFF;
            continue;
        }
        {
            int off = first_rise_cells(it->v) * ((int)it->bit - (int)MON_BIT);

            if (off > tol || off < -tol) {
                wc[nw] = 'B';
                wd[nw++] = off & 0xFF;
            }
        }
        wc[nw] = it->stop ? 'U' : 'F';
        wd[nw++] = it->v;
    }
    n = mon_uart_run(items, 8, code, data, 32);
    CHECK(n == nw, "%d events, want %d", n, nw);
    for (i = 0; i < n; ++i) {
        CHECK(code[i] == wc[i], "event %d is '%c', want '%c'", i, code[i], wc[i]);
        /* edges are seen in thread 0's own slots: a time may be one cycle off */
        if (wc[i] == 'B' || wc[i] == 'G')
            CHECK(wrap_diff(data[i], wd[i]) >= -1 && wrap_diff(data[i], wd[i]) <= 1,
                  "event %d ('%c') %d, want %d +-1", i, wc[i], data[i], wd[i]);
        else
            CHECK(data[i] == wd[i], "event %d ('%c') %02x, want %02x", i, wc[i], data[i],
                  wd[i]);
    }
    return 0;
}

/* test_monitors.test_mon_uart_break: a break is a framing error with data
 * 0, and the byte after it is decoded and timed */
static int test_mon_uart_break(void)
{
    static const mon_item items[3] = {
        {0x31, MON_BIT, 1, 300}, {20U * MON_BIT, 0, 1, 600}, {0x10, MON_BIT + 5U, 1, 300}};
    char code[8];
    int data[8], n;

    n = mon_uart_run(items, 3, code, data, 8);
    CHECK(n == 4 && code[0] == 'U' && code[1] == 'F' && code[2] == 'B' && code[3] == 'U',
          "%d events, want U F B U", n);
    CHECK(data[0] == 0x31 && data[1] == 0 && data[3] == 0x10, "data %02x %02x %02x",
          data[0], data[1], data[3]);
    CHECK(wrap_diff(data[2], 25) >= -1 && wrap_diff(data[2], 25) <= 1,
          "timing offset %d, want 25 +-1", data[2]);
    return 0;
}

/* test_monitors.test_mon_uart_back_to_back: no idle between frames; every
 * byte is decoded and no error is invented */
static int test_mon_uart_back_to_back(void)
{
    mon_item items[21];
    char code[32];
    int data[32], n, i;
    msf_rng rng;

    msf_rng_seed(&rng, 3U);
    for (i = 0; i < 21; ++i) {
        items[i].v = (uint16_t)(i < 20 ? msf_rng_range(&rng, 0U, 256U) : 0U);
        items[i].bit = MON_BIT;
        items[i].stop = 1;
        items[i].idle = 0;
    }
    n = mon_uart_run(items, 21, code, data, 32);
    CHECK(n == 21, "%d events, want 21", n);
    for (i = 0; i < 21; ++i)
        CHECK(code[i] == 'U' && data[i] == items[i].v, "event %d ('%c', %02x), want ('U', %02x)",
              i, code[i], data[i], items[i].v);
    return 0;
}

/* I2C bus levels with exact phase lengths, and the events the monitor must
 * report for them (test_monitors.I2cWave). The model drives both lines:
 * the monitor only listens. SDA uio5, SCL uio6. */
#define I2W_SDA    5
#define I2W_SCL    6
#define I2W_IDLE   (1U << I2W_SDA | 1U << I2W_SCL)
#define I2W_MAX    131072U
#define I2W_LMIN   56U
#define I2W_HMIN   48U
#define I2W_BOOT   400U         /* the I2C monitor clears its queue first */
#define I2C_LSLACK 10           /* mon_i2c.s: see its header */
#define I2C_HFLOOR 16
#define I2W_EVENTS 160U

typedef struct {
    uint32_t n, tlow, thigh, thd, tss, tbuf, nw;
    int sda, scl, nbits, byte, overflow;
    char wc[I2W_EVENTS];
    int wd[I2W_EVENTS];
} i2c_wave;

static uint8_t i2w_lv[I2W_MAX];

static void i2w_init(i2c_wave *w, uint32_t tlow, uint32_t thigh)
{
    w->n = 0;
    w->tlow = tlow;
    w->thigh = thigh;
    w->thd = 6;
    w->tss = 60;
    w->tbuf = 100;
    w->nw = 0;
    w->sda = w->scl = 1;
    w->nbits = w->byte = 0;
    w->overflow = 0;
}

static void i2w_hold(i2c_wave *w, uint32_t n)
{
    uint8_t v = (uint8_t)((unsigned)w->sda << I2W_SDA | (unsigned)w->scl << I2W_SCL);

    while (n--) {
        if (w->n < I2W_MAX)
            i2w_lv[w->n++] = v;
        else
            w->overflow = 1;
    }
}

static void i2w_want(i2c_wave *w, char c, int d)
{
    if (w->nw < I2W_EVENTS) {
        w->wc[w->nw] = c;
        w->wd[w->nw++] = d;
    } else {
        w->overflow = 1;
    }
}

static void i2w_start(i2c_wave *w)
{
    if (!w->scl) {                       /* repeated START: SDA up, SCL up */
        i2w_hold(w, w->thd);
        w->sda = 1;
        i2w_hold(w, w->tlow - w->thd);
        w->scl = 1;
    }
    i2w_hold(w, w->tss);
    w->sda = 0;
    if (w->nbits)
        i2w_want(w, 'X', w->nbits);
    i2w_want(w, 'S', 0);
    i2w_hold(w, w->tss);
    w->scl = 0;
    w->nbits = w->byte = 0;
}

static void i2w_stop(i2c_wave *w)
{
    i2w_hold(w, w->thd);
    w->sda = 0;
    i2w_hold(w, w->tlow - w->thd);
    w->scl = 1;
    i2w_hold(w, w->tss);
    w->sda = 1;
    if (w->nbits)
        i2w_want(w, 'X', w->nbits);
    i2w_want(w, 'P', 0);
    w->nbits = 0;
    i2w_hold(w, w->tbuf);
}

/* one bit; low / high 0: the wave's own phase lengths */
static void i2w_bit(i2c_wave *w, int b, uint32_t low, uint32_t high)
{
    uint32_t h;

    if (!low)
        low = w->tlow;
    if (!high)
        high = w->thigh;
    h = w->thd < low - 1U ? w->thd : low - 1U;
    i2w_hold(w, h);
    w->sda = b;
    i2w_hold(w, low - h);
    w->scl = 1;
    i2w_hold(w, high);
    w->scl = 0;
    w->nbits++;
    w->byte = w->byte << 1 | b;
    if (w->nbits == 9) {
        i2w_want(w, b ? 'N' : 'A', w->byte >> 1);
        w->nbits = w->byte = 0;
    }
}

/* a byte, MSB first, and its ACK bit; low[i] / high[i] (0: as the wave)
 * override bit i, 8 being the ACK; either may be NULL */
static void i2w_byte(i2c_wave *w, unsigned v, int ack, const uint32_t *low, const uint32_t *high)
{
    int i;

    for (i = 0; i < 9; ++i)
        i2w_bit(w, i < 8 ? (int)((v >> (7 - i)) & 1U) : ack, low ? low[i] : 0U,
                high ? high[i] : 0U);
}

/* mon_i2c.s watching the wave from cycle 60 */
static int mon_i2c_run(const i2c_wave *w, uint32_t extra, char *code, int *data, uint32_t max)
{
    uint32_t k;

    CHECK(!w->overflow, "the wave does not fit");
    if (fw_boot(&img_mon_i2c, 0, 0, I2W_IDLE))
        return -1;
    for (k = 0; k < 60U + w->n + extra; ++k)
        cycle(k >= 60U && k - 60U < w->n ? i2w_lv[k - 60U] : (uint8_t)I2W_IDLE);
    CHECK(!tr.overflow, "trace overflow");
    return mon_events(code, data, max);
}

static void print_events(const char *what, const char *c, const int *d, int n)
{
    int i;

    msf_printf("    %s:", what);
    for (i = 0; i < n; ++i)
        msf_printf(" %c%d", c[i], d[i]);
    msf_printf("\r\n");
}

/* test_monitors.check_i2c_events: codes exact; values exact, except a
 * phase length 'H' / 'L', checked against the monitor's stated accuracy
 * (a high to +-1, HFLOOR for anything shorter; a low from -1 to +LSLACK,
 * or below LMIN - 1 for one shorter than thread 0's turnaround); 0 = a
 * short phase on a 9th bit, flagged without its length */
static int mon_i2c_check(const char *gc, const int *gd, int n, const char *wc, const int *wd,
                         int nw)
{
    int i, ok;

    if (n != nw) {
        print_events("got", gc, gd, n < 0 ? 0 : n);
        print_events("want", wc, wd, nw);
    }
    CHECK(n == nw, "%d events, want %d", n, nw);
    for (i = 0; i < n; ++i) {
        if (gc[i] != wc[i]) {
            print_events("got", gc, gd, n);
            print_events("want", wc, wd, nw);
        }
        CHECK(gc[i] == wc[i], "event %d is '%c', want '%c'", i, gc[i], wc[i]);
        if ((wc[i] == 'H' || wc[i] == 'L') && wd[i] == 0) {
            ok = gd[i] == 0;
        } else if (wc[i] == 'H') {
            int want = wd[i] > I2C_HFLOOR ? wd[i] : I2C_HFLOOR;

            ok = gd[i] >= want - 1 && gd[i] <= want + 1;
        } else if (wc[i] == 'L') {
            int hi = wd[i] + I2C_LSLACK;

            if (wd[i] < 45 && hi < (int)I2W_LMIN - 2)
                hi = (int)I2W_LMIN - 2;
            ok = gd[i] >= wd[i] - 1 && gd[i] <= hi;
        } else {
            ok = gd[i] == wd[i];
        }
        CHECK(ok, "event %d ('%c' %d), want %d", i, gc[i], gd[i], wd[i]);
    }
    return 0;
}

static i2c_wave i2w;
static char ev_c[I2W_EVENTS];
static int ev_d[I2W_EVENTS];

/* test_monitors.test_mon_i2c_transfers: a write, a random read with a
 * repeated START, a NACK and a STOP, at 100 kHz and at the legal minimum:
 * decoded exactly, with no timing event */
static int test_mon_i2c_transfers(void)
{
    static const uint32_t ph[2][2] = {{60U, 60U}, {I2W_LMIN, I2W_HMIN}};
    int k, n;

    for (k = 0; k < 2; ++k) {
        i2w_init(&i2w, ph[k][0], ph[k][1]);
        i2w_hold(&i2w, I2W_BOOT);
        i2w_start(&i2w);
        i2w_byte(&i2w, 0x50U << 1, 0, 0, 0);
        i2w_byte(&i2w, 0x12U, 0, 0, 0);
        i2w_byte(&i2w, 0x34U, 0, 0, 0);
        i2w_stop(&i2w);
        i2w_start(&i2w);
        i2w_byte(&i2w, 0x50U << 1, 0, 0, 0);
        i2w_byte(&i2w, 0x07U, 0, 0, 0);
        i2w_start(&i2w);
        i2w_byte(&i2w, 0x50U << 1 | 1U, 0, 0, 0);
        i2w_byte(&i2w, 0xC3U, 0, 0, 0);
        i2w_byte(&i2w, 0x3CU, 1, 0, 0);
        i2w_stop(&i2w);
        n = mon_i2c_run(&i2w, 3000U, ev_c, ev_d, I2W_EVENTS);
        if (mon_i2c_check(ev_c, ev_d, n, i2w.wc, i2w.wd, (int)i2w.nw))
            return -1;
    }
    return 0;
}

/* test_monitors.test_mon_i2c_random_legal: random legal phases on every bit
 * (lows LMIN..3 LMIN, now and then a few hundred cycles; highs HMIN..3
 * HMIN), random data, ACKs and NACKs, repeated STARTs: decoded exactly,
 * no timing event */
static int test_mon_i2c_random_legal(void)
{
    uint32_t low[9], high[9];
    int t, b, i, n, nbytes, stopped = 1;
    msf_rng rng;

    msf_rng_seed(&rng, 7U);
    i2w_init(&i2w, I2W_LMIN, I2W_HMIN);
    i2w_hold(&i2w, I2W_BOOT);
    for (t = 0; t < 4; ++t) {
        i2w_start(&i2w);
        stopped = 0;
        nbytes = (int)msf_rng_range(&rng, 1U, 5U);
        for (b = 0; b < nbytes; ++b) {
            unsigned v = msf_rng_range(&rng, 0U, 256U);

            for (i = 0; i < 9; ++i) {
                low[i] = msf_rng_range(&rng, 0U, 10U) == 0U && msf_rng_range(&rng, 0U, 2U)
                             ? msf_rng_range(&rng, 150U, 400U)
                             : msf_rng_range(&rng, I2W_LMIN, 3U * I2W_LMIN);
                high[i] = msf_rng_range(&rng, I2W_HMIN, 3U * I2W_HMIN);
            }
            i2w_byte(&i2w, v, (int)msf_rng_range(&rng, 0U, 2U), low, high);
        }
        if (msf_rng_range(&rng, 0U, 2U)) {
            i2w_stop(&i2w);
            stopped = 1;
        }
    }
    if (!stopped)
        i2w_stop(&i2w);
    n = mon_i2c_run(&i2w, 5000U, ev_c, ev_d, I2W_EVENTS);
    for (i = 0; i < n; ++i)
        CHECK(ev_c[i] != 'H' && ev_c[i] != 'L', "timing event %d ('%c' %d) on a legal bus", i,
              ev_c[i], ev_d[i]);
    return mon_i2c_check(ev_c, ev_d, n, i2w.wc, i2w.wd, (int)i2w.nw);
}

/* test_monitors.test_mon_i2c_protocol_errors: each byte's first short
 * phase with its length; a short phase on a 9th bit flagged with 0; a STOP
 * after 3 bits of a byte and a repeated START after 5 */
static int test_mon_i2c_protocol_errors(void)
{
    static const char wc[] = "SHALALHAAHALXPSAXSAP";
    static const int wd[] = {0, 30, 0xA0, 40, 0x5A, 50, 20, 0x33, 0xC4, 0, 0x96, 0, 3, 0,
                             0, 0xA0, 5, 0, 0xA1, 0};
    uint32_t low[9], high[9];
    int n;

    i2w_init(&i2w, 60U, 60U);
    i2w_hold(&i2w, I2W_BOOT);
    i2w_start(&i2w);
    memset(low, 0, sizeof low);
    memset(high, 0, sizeof high);
    high[3] = 30U;
    low[6] = 30U;                                         /* 'H' 30 only */
    i2w_byte(&i2w, 0x50U << 1, 0, low, high);
    memset(low, 0, sizeof low);
    memset(high, 0, sizeof high);
    low[4] = 40U;
    i2w_byte(&i2w, 0x5AU, 0, low, 0);                     /* 'L' 40 */
    memset(low, 0, sizeof low);
    low[2] = 50U;
    high[2] = 20U;
    i2w_byte(&i2w, 0x33U, 0, low, high);                  /* both, one word */
    memset(low, 0, sizeof low);
    memset(high, 0, sizeof high);
    high[8] = 30U;
    i2w_byte(&i2w, 0xC4U, 0, 0, high);                    /* 9th bit: flagged */
    low[8] = 44U;
    i2w_byte(&i2w, 0x96U, 0, low, 0);
    i2w_bit(&i2w, 1, 0, 0);
    i2w_bit(&i2w, 0, 0, 0);
    i2w_bit(&i2w, 1, 0, 0);
    i2w_stop(&i2w);                                       /* STOP after 3 bits */
    i2w_start(&i2w);
    i2w_byte(&i2w, 0x50U << 1, 0, 0, 0);
    i2w_bit(&i2w, 0, 0, 0);
    i2w_bit(&i2w, 1, 0, 0);
    i2w_bit(&i2w, 1, 0, 0);
    i2w_bit(&i2w, 0, 0, 0);
    i2w_bit(&i2w, 1, 0, 0);
    i2w_start(&i2w);                                      /* repeated START after 5 */
    i2w_byte(&i2w, 0x50U << 1 | 1U, 0, 0, 0);
    i2w_stop(&i2w);
    n = mon_i2c_run(&i2w, 4000U, ev_c, ev_d, I2W_EVENTS);
    return mon_i2c_check(ev_c, ev_d, n, wc, wd, (int)(sizeof wd / sizeof wd[0]));
}

/* test_monitors.test_mon_i2c_single_faults: one short phase per byte, on
 * each data bit from the second, at 100 kHz and at the legal minimum: each
 * reported once with its length, and every byte still decodes */
static int test_mon_i2c_single_faults(void)
{
    static const char kinds[4] = {'L', 'L', 'H', 'H'};
    static const uint32_t vals[4] = {30U, 50U, 12U, 40U};
    static const uint32_t base[2][2] = {{60U, 60U}, {I2W_LMIN, I2W_HMIN}};
    static char wc[I2W_EVENTS];
    static int wd[I2W_EVENTS];
    uint32_t low[9], high[9];
    int nw = 0, k, bit, f, n;
    msf_rng rng;

    msf_rng_seed(&rng, 11U);
    i2w_init(&i2w, 60U, 60U);
    i2w_hold(&i2w, I2W_BOOT);
    i2w_start(&i2w);
    wc[nw] = 'S';
    wd[nw++] = 0;
    for (k = 0; k < 2; ++k) {
        i2w.tlow = base[k][0];
        i2w.thigh = base[k][1];
        for (bit = 1; bit < 8; ++bit) {
            for (f = 0; f < 4; ++f) {
                unsigned v = msf_rng_range(&rng, 0U, 256U);

                memset(low, 0, sizeof low);
                memset(high, 0, sizeof high);
                if (kinds[f] == 'L')
                    low[bit] = vals[f];
                else
                    high[bit] = vals[f];
                i2w_byte(&i2w, v, 0, low, high);
                wc[nw] = kinds[f];
                wd[nw++] = (int)vals[f];
                wc[nw] = 'A';
                wd[nw++] = (int)v;
            }
        }
    }
    i2w_stop(&i2w);
    wc[nw] = 'P';
    wd[nw++] = 0;
    n = mon_i2c_run(&i2w, 40000U, ev_c, ev_d, I2W_EVENTS);
    return mon_i2c_check(ev_c, ev_d, n, wc, wd, nw);
}

/* test_monitors.test_mon_i2c_fast_master: a master out of spec on every
 * bit, at the monitor's limits (lows of 16 cycles, bits of 72): every byte
 * decodes, and each reports its first short phase */
static int test_mon_i2c_fast_master(void)
{
    static const uint32_t ph[4][2] = {{16U, 56U}, {34U, 38U}, {40U, 32U}, {56U, 16U}};
    static const unsigned data[5] = {0x50U << 1, 0x00U, 0xFFU, 0x5AU, 0xA5U};
    int k, i, n, m, nt;

    for (k = 0; k < 4; ++k) {
        i2w_init(&i2w, ph[k][0], ph[k][1]);
        i2w_hold(&i2w, I2W_BOOT);
        i2w_start(&i2w);
        for (i = 0; i < 5; ++i)
            i2w_byte(&i2w, data[i], 0, 0, 0);
        i2w_stop(&i2w);
        n = mon_i2c_run(&i2w, 6000U, ev_c, ev_d, I2W_EVENTS);
        CHECK(n > 0, "low %d high %d: no events", (int)ph[k][0], (int)ph[k][1]);
        m = nt = 0;
        for (i = 0; i < n; ++i) {
            if (ev_c[i] == 'H' || ev_c[i] == 'L') {
                ++nt;
                continue;
            }
            CHECK(m <= 6 && ev_c[i] == (m == 0 ? 'S' : m == 6 ? 'P' : 'A') &&
                      ev_d[i] == (m == 0 || m == 6 ? 0 : (int)data[m - 1]),
                  "low %d high %d: event %d ('%c' %d) out of order", (int)ph[k][0],
                  (int)ph[k][1], i, ev_c[i], ev_d[i]);
            ++m;
        }
        CHECK(m == 7, "low %d high %d: %d bus events, want 7", (int)ph[k][0], (int)ph[k][1], m);
        CHECK(nt >= 5, "low %d high %d: %d timing events, want one a byte", (int)ph[k][0],
              (int)ph[k][1], nt);
    }
    return 0;
}

/* test_monitors.test_mon_i2c_long_low: an SCL low of 70000 cycles inside a
 * byte (past the 16-bit cycle counter's wrap) reads as fine */
static int test_mon_i2c_long_low(void)
{
    uint32_t low[9];
    int n;

    memset(low, 0, sizeof low);
    low[4] = 70000U;
    i2w_init(&i2w, 60U, 60U);
    i2w_hold(&i2w, I2W_BOOT);
    i2w_start(&i2w);
    i2w_byte(&i2w, 0xA6U, 0, low, 0);
    i2w_byte(&i2w, 0x3CU, 0, 0, 0);
    i2w_stop(&i2w);
    n = mon_i2c_run(&i2w, 3000U, ev_c, ev_d, I2W_EVENTS);
    return mon_i2c_check(ev_c, ev_d, n, i2w.wc, i2w.wd, (int)i2w.nw);
}

/* SPI bus levels in a given mode, controller and device both modelled (the
 * monitor only listens), and the events the monitor must report
 * (test_monitors.SpiWave). SCK uio2, MOSI uio3, CS uio5, MISO uio7. */
#define SPW_SCK    2
#define SPW_MOSI   3
#define SPW_CS     5
#define SPW_MISO   7
#define SPW_MAX    32768U
#define SPW_EVENTS 96U

typedef struct {
    int cpol, cpha, cs, sck, mosi, miso, mm;   /* mm: SCK level at CS low if wrong, else -1 */
    uint32_t n, nw;
    int overflow;
    char wc[SPW_EVENTS];
    int wd[SPW_EVENTS];
} spi_wave;

static uint8_t spw_lv[SPW_MAX];

static void spw_init(spi_wave *w, int mode)
{
    w->cpol = mode >> 1;
    w->cpha = mode & 1;
    w->cs = 1;
    w->sck = w->cpol;
    w->mosi = w->miso = 0;
    w->mm = -1;
    w->n = w->nw = 0;
    w->overflow = 0;
}

static uint8_t spw_idle(const spi_wave *w)
{
    return (uint8_t)(1U << SPW_CS | (unsigned)w->cpol << SPW_SCK);
}

static void spw_hold(spi_wave *w, uint32_t n)
{
    uint8_t v = (uint8_t)((unsigned)w->cs << SPW_CS | (unsigned)w->sck << SPW_SCK |
                          (unsigned)w->mosi << SPW_MOSI | (unsigned)w->miso << SPW_MISO);

    while (n--) {
        if (w->n < SPW_MAX)
            spw_lv[w->n++] = v;
        else
            w->overflow = 1;
    }
}

static void spw_want(spi_wave *w, char c, int d)
{
    if (w->nw < SPW_EVENTS) {
        w->wc[w->nw] = c;
        w->wd[w->nw++] = d;
    } else {
        w->overflow = 1;
    }
}

static void spw_select(spi_wave *w, uint32_t setup)
{
    w->cs = 0;
    w->mm = w->sck != w->cpol ? w->sck : -1;   /* reported as 'M' at the end */
    spw_hold(w, setup);
}

static void spw_bit(spi_wave *w, int o, int i, uint32_t half)
{
    w->sck = w->cpha ? 1 - w->cpol : w->cpol;
    w->mosi = o;
    w->miso = i;
    spw_hold(w, half);
    w->sck = 1 - w->sck;                       /* the sampling edge */
    spw_hold(w, half);
}

static void spw_byte(spi_wave *w, unsigned o, unsigned i, uint32_t half, uint32_t gap)
{
    int k;

    for (k = 0; k < 8; ++k)
        spw_bit(w, (int)((o >> (7 - k)) & 1U), (int)((i >> (7 - k)) & 1U), half);
    spw_want(w, '>', (int)o);
    spw_want(w, '<', (int)i);
    if (!w->cpha)
        w->sck = w->cpol;
    spw_hold(w, gap);
}

static void spw_deselect(spi_wave *w, int left)
{
    spw_hold(w, 10);
    w->cs = 1;
    if (w->mm >= 0)
        spw_want(w, 'M', w->mm);
    spw_want(w, 'E', left);
    spw_hold(w, 200);
}

/* mon_spi.s (built for `mode`) watching the wave from cycle 60; the events
 * must be exactly the wave's */
static int spi_mon_check(const spi_wave *w, int mode)
{
    static const msf_image *const img[4] = {&img_mon_spi_m0, &img_mon_spi_m1, &img_mon_spi_m2,
                                            &img_mon_spi_m3};
    uint32_t k;
    uint8_t idle = spw_idle(w);
    int n, i;

    CHECK(!w->overflow, "the wave does not fit");
    if (fw_boot(img[mode], 0, 0, idle))
        return -1;
    for (k = 0; k < 60U + w->n + 4000U; ++k)
        cycle(k >= 60U && k - 60U < w->n ? spw_lv[k - 60U] : idle);
    CHECK(!tr.overflow, "trace overflow");
    n = mon_events(ev_c, ev_d, I2W_EVENTS);
    if (n != (int)w->nw) {
        print_events("got", ev_c, ev_d, n < 0 ? 0 : n);
        print_events("want", w->wc, w->wd, (int)w->nw);
    }
    CHECK(n == (int)w->nw, "mode %d: %d events, want %d", mode, n, (int)w->nw);
    for (i = 0; i < n; ++i)
        CHECK(ev_c[i] == w->wc[i] && ev_d[i] == w->wd[i],
              "mode %d: event %d ('%c' %02x), want ('%c' %02x)", mode, i, ev_c[i], ev_d[i],
              w->wc[i], w->wd[i]);
    return 0;
}

static spi_wave spw;

/* test_monitors.test_mon_spi_modes: every mode, random bytes on MOSI and
 * MISO with and without gaps between bytes: each byte decoded on both */
static int test_mon_spi_modes(void)
{
    static const uint32_t hg[2][2] = {{30U, 40U}, {40U, 0U}};
    int mode, k, b;
    msf_rng rng;

    msf_rng_seed(&rng, 5U);
    for (mode = 0; mode < 4; ++mode) {
        spw_init(&spw, mode);
        spw_hold(&spw, I2W_BOOT);
        for (k = 0; k < 2; ++k) {
            spw_select(&spw, 20U);
            for (b = 0; b < 4; ++b) {
                unsigned o = msf_rng_range(&rng, 0U, 256U), i = msf_rng_range(&rng, 0U, 256U);

                spw_byte(&spw, o, i, hg[k][0], hg[k][1]);
            }
            spw_deselect(&spw, 0);
        }
        if (spi_mon_check(&spw, mode))
            return -1;
    }
    return 0;
}

/* test_monitors.test_mon_spi_limits: SCK periods of 28 cycles inside a
 * byte, 36 between bytes, 16 from CS low to the first sampling edge */
static int test_mon_spi_limits(void)
{
    static const unsigned data[4][2] = {{0xA5U, 0x3CU}, {0x00U, 0xFFU}, {0xFFU, 0x00U},
                                        {0x5AU, 0xC3U}};
    int mode, b;

    for (mode = 0; mode < 4; ++mode) {
        spw_init(&spw, mode);
        spw_hold(&spw, I2W_BOOT);
        spw_select(&spw, spw.cpha ? 6U : 2U);
        for (b = 0; b < 4; ++b)
            spw_byte(&spw, data[b][0], data[b][1], 14U, 8U);
        spw_deselect(&spw, 0);
        if (spi_mon_check(&spw, mode))
            return -1;
    }
    return 0;
}

/* test_monitors.test_mon_spi_errors: a controller in another mode (SCK
 * idling high), CS rising in the middle of a byte, a single clock */
static int test_mon_spi_errors(void)
{
    int k;

    spw_init(&spw, 0);
    spw_hold(&spw, I2W_BOOT);
    spw.sck = 1;                                  /* a mode 2 / 3 controller */
    spw_hold(&spw, 20U);
    spw_select(&spw, 20U);                        /* 'M' 1 at the end */
    spw.mosi = 1;
    spw.miso = 0;
    for (k = 0; k < 8; ++k) {                     /* 8 clocks idling high */
        spw.sck = 0;
        spw_hold(&spw, 30U);
        spw.sck = 1;
        spw_hold(&spw, 30U);
    }
    spw_want(&spw, '>', 0xFF);
    spw_want(&spw, '<', 0x00);
    spw_deselect(&spw, 0);
    spw.sck = 0;
    spw_hold(&spw, 100U);
    spw_select(&spw, 20U);                        /* mode 0 again */
    spw_byte(&spw, 0x81U, 0x7EU, 30U, 0U);
    spw_bit(&spw, 1, 0, 30U);
    spw_bit(&spw, 0, 0, 30U);
    spw_bit(&spw, 1, 0, 30U);
    spw.sck = 0;
    spw_deselect(&spw, 3);                        /* CS high after 3 bits */
    spw_select(&spw, 20U);
    spw_bit(&spw, 1, 1, 30U);
    spw.sck = 0;
    spw_deselect(&spw, 1);                        /* a single clock */
    return spi_mon_check(&spw, 0);
}

/* ======================================================================== */
/* UART -> SPI bridge (test_bridge): thread 0 queues UART bytes, thread 1
 * runs the SPI transfers they announce and sends the bytes read back.     */

/* test_bridge.ShiftRegDevice: an 8-bit register shifted MSB first, MOSI
 * (uio3) in, MISO (uio4) out, SCK uio2, CS uio5 (high when undriven);
 * records the bytes of each transfer */
typedef struct {
    int cpol, cpha, sck, cs, miso, n, ntr;
    uint8_t reg;
    uint8_t got[8][8];
    int ngot[8];
} shreg_dev;

static void shreg_init(shreg_dev *d, int mode)
{
    memset(d, 0, sizeof *d);
    d->cpol = mode >> 1;
    d->cpha = mode & 1;
    d->reg = 0x5AU;
    d->miso = (d->reg >> 7) & 1;
    d->sck = d->cpol;
    d->cs = 1;
}

static int shreg_cycle(shreg_dev *d, msf_pins p)
{
    int cs = (p.oe >> 5) & 1U ? (p.out >> 5) & 1 : 1;
    int sck = (p.out >> 2) & 1, mosi = (p.out >> 3) & 1;

    if (!cs && d->cs) {                       /* selected: first bit on MISO */
        if (d->ntr < 8)
            d->ngot[d->ntr++] = 0;
        d->n = 0;
        d->miso = (d->reg >> 7) & 1;
    }
    if (!cs && sck != d->sck) {
        int leading = sck != d->cpol;

        if (leading == !d->cpha) {            /* the sampling edge */
            d->reg = (uint8_t)(d->reg << 1 | mosi);
            if (++d->n % 8 == 0 && d->ntr && d->ngot[d->ntr - 1] < 8)
                d->got[d->ntr - 1][d->ngot[d->ntr - 1]++] = d->reg;
        } else {                              /* the shifting edge */
            d->miso = (d->reg >> 7) & 1;
        }
    }
    d->sck = sck;
    d->cs = cs;
    return d->miso;
}

/* test_bridge.test_bridge_modes: every SPI mode, transfers of 3, 2 and 1
 * bytes and a 0 that is ignored: the device sees each transfer's bytes
 * framed by CS, and the UART returns what it shifted out */
static int test_bridge_modes(void)
{
    static const msf_image *const img[4] = {&img_bridge_m0, &img_bridge_m1, &img_bridge_m2,
                                            &img_bridge_m3};
    static const uint8_t frames[10] = {3, 0x11, 0x22, 0xC3, 0, 2, 0xA5, 0x00, 1, 0xFF};
    static const uint8_t back_want[6] = {0x5A, 0x11, 0x22, 0xC3, 0xA5, 0x00};
    static const uint8_t tr_want[3][3] = {{0x11, 0x22, 0xC3}, {0xA5, 0x00}, {0xFF}};
    static const int tr_len[3] = {3, 2, 1};
    mon_item items[10];
    shreg_dev d;
    int back[16], mode, miso, i, j;
    uint32_t k, total = 400U + 4U * 12U * MON_BIT, n;

    for (i = 0; i < 10; ++i) {
        items[i].v = frames[i];
        items[i].bit = MON_BIT;
        items[i].stop = 1;
        items[i].idle = 3U * MON_BIT;
        total += mon_item_len(&items[i]);
    }
    for (mode = 0; mode < 4; ++mode) {
        if (fw_boot(img[mode], 0, 0, 0x02U))
            return -1;
        shreg_init(&d, mode);
        miso = d.miso;
        for (k = 0; k < total; ++k) {
            int line = k >= 400U ? mon_line(items, 10, k - 400U) : 1;

            miso = shreg_cycle(&d, cycle((uint8_t)(line << 1 | miso << 4)));
        }
        CHECK(!tr.overflow, "trace overflow");
        CHECK(d.ntr == 3, "mode %d: %d transfers, want 3", mode, d.ntr);
        for (i = 0; i < 3; ++i) {
            CHECK(d.ngot[i] == tr_len[i], "mode %d: transfer %d has %d bytes, want %d", mode, i,
                  d.ngot[i], tr_len[i]);
            for (j = 0; j < tr_len[i]; ++j)
                CHECK(d.got[i][j] == tr_want[i][j], "mode %d: transfer %d byte %d = %02x, want %02x",
                      mode, i, j, d.got[i][j], tr_want[i][j]);
        }
        n = msf_uart_decode(&tr, MON_BIT, 0, back, 16);
        CHECK(n == 6U, "mode %d: %d bytes back, want 6", mode, (int)n);
        for (i = 0; i < 6; ++i)
            CHECK(back[i] == back_want[i], "mode %d: byte %d back = %02x, want %02x", mode, i,
                  back[i], back_want[i]);
    }
    return 0;
}

/* ======================================================================== */
/* Capture engine (test_capture): the chip as a logic analyser.            */

#define CS_RUN  0x8000U
#define CS_TRIG 0x4000U
#define CS_DONE 0x2000U
#define CS_OVF  0x1000U

/* test_capture.cap_ref: the words the engine records for a sample
 * sequence (isa.md 6.6: packing, change records, trigger, POST), and the
 * index of the trigger word; the word count, or -1 if it does not finish */
static int cap_ref(const uint8_t *s, int ns, int chg, unsigned mask, unsigned val, int edge,
                   int post, uint16_t *words, int maxw, int *tw)
{
    int n = 0, i, trig = 0, left = 0, prev_c = 1, first = 1, half = 0, thit = 0;
    unsigned last = 0, delta = 0;

    for (i = 0; i < ns; ++i) {
        int cond = (s[i] & mask) == val, hit = cond && (!edge || !prev_c), have = 0, ptrig = 0;
        unsigned word = 0;

        prev_c = cond;
        if (chg) {
            unsigned d = (delta + 1U) & 0xFFU;

            if (first || s[i] != last || d == 255U || (hit && !trig)) {
                word = (unsigned)s[i] << 8 | d;
                have = 1;
                ptrig = hit && !trig;
                delta = 0;
                last = s[i];
            } else {
                delta = d;
            }
        } else if (!half) {
            last = s[i];
            half = 1;
            thit = hit && !trig;
        } else {
            word = (unsigned)s[i] << 8 | last;
            have = 1;
            ptrig = !trig && (thit || hit);
            half = 0;
        }
        first = 0;
        if (!have)
            continue;
        if (n >= maxw)
            return -1;
        words[n++] = (uint16_t)word;
        if (ptrig) {
            trig = 1;
            *tw = n - 1;
            left = post;
            if (post == 0)
                return n;
        } else if (trig) {
            if (left == 1)
                return n;
            --left;
        }
    }
    return -1;
}

/* test_capture.test_fw_logic_analyzer: logic_analyzer.s arms (sample mode,
 * every 2nd cycle, edge trigger on (pins & 0xF0) == 0x30, POST 9, a
 * 32-word ring), waits, then dumps over the UART at 12 cycles a bit: the
 * host gets the samples back exactly. The pins are k >> 2 (k counting the
 * harness's cycles); the chip's CNT differs from k by a constant, found by
 * trying -8..8: only the right one can match all 32 words. */
static int test_fw_logic_analyzer(void)
{
    enum { DIV = 2, SIZE = 32, POST = 9, NS = 4096 };
    static uint8_t samples[NS];
    static uint16_t words[NS];
    int raw[128], i, n, nw, tw = 0, off, found = 0;
    uint16_t w[64];
    uint32_t k;

    if (fw_boot(&img_logic_analyzer, 0, 0, 0))
        return -1;
    for (k = 0; k < 60000U; ++k)
        if (cycle((uint8_t)(k >> 2)).uo == DONE)
            break;
    CHECK(k < 60000U, "no DONE on uo_out");
    CHECK(!tr.overflow, "trace overflow");
    n = (int)msf_uart_decode(&tr, 12U, 0, raw, 128);
    CHECK(n == 2 * (4 + SIZE), "%d bytes dumped, want %d", n, 2 * (4 + SIZE));
    for (i = 0; i < n; ++i)
        CHECK(raw[i] >= 0, "framing error in byte %d", i);
    for (i = 0; i < n / 2; ++i)
        w[i] = (uint16_t)(raw[2 * i] | raw[2 * i + 1] << 8);
    CHECK(w[0] == 0xA55AU, "header %04x, want a55a", w[0]);
    CHECK((w[3] & (CS_RUN | CS_TRIG | CS_DONE | CS_OVF)) == (CS_TRIG | CS_DONE),
          "status %04x", w[3]);
    for (off = -8; off <= 8 && !found; ++off) {
        for (i = 0; i < NS; ++i)
            samples[i] = (uint8_t)(((int)w[1] + i * DIV - off) >> 2);
        nw = cap_ref(samples, NS, 0, 0xF0U, 0x30U, 1, POST, words, NS, &tw);
        if (nw < 0)
            continue;
        /* oldest first; the ring was cleared before arming */
        found = 1;
        for (i = 0; i < SIZE; ++i)
            if (w[4 + i] != (nw - SIZE + i >= 0 ? words[nw - SIZE + i] : 0U))
                found = 0;
        if (found)
            CHECK(w[2] == (uint16_t)(((tw - (nw - SIZE)) % SIZE + SIZE) % SIZE),
                  "trigger index %d, want %d", w[2], ((tw - (nw - SIZE)) % SIZE + SIZE) % SIZE);
    }
    CHECK(found, "the 32 samples match the spec at no CNT offset");
    return 0;
}

/* ======================================================================== */
/* PS/2 (test_buses): the chip plays a keyboard against a host model that
 * reads device frames on CLK falling edges, sends commands the way a host
 * does, and checks the device's timing. CLK uio2, DATA uio3 (open drain,
 * pulled up), UART RX uio1 / TX uio0.                                     */

#define PS2_CLK 2
#define PS2_DAT 3
#define PS2_US  12U                 /* cycles per microsecond at 12 MHz */

typedef struct {
    uint32_t at;
    uint8_t b;
    int good;                       /* 0: send it with bad parity */
} ps2_send;

/* test_buses.Ps2Host */
typedef struct {
    const ps2_send *sends;
    int nsends, isend;
    int inh_frame, inh_bits, inh_armed;   /* inhibit after inh_bits of frame inh_frame */
    uint32_t inh_len;
    int pull_clk, pull_dat, clk, dat;
    int bits[11], nbits;
    uint8_t got[32];
    int ngot, errors, frames_started;
    uint32_t first_error;
    int acks[16], nacks;
    int tx_on, tx_bits[10], tx_n, tx_stage;   /* 0 inhibit, 1 request, 2 bits, 3 ACK seen */
    uint32_t tx_t;
    uint32_t last_edge;
    int last_fall_bits;
    uint32_t ph_min, ph_max;              /* clock phases in device frames, under 100 us */
    int nph;
    int inhibit_on;
    uint32_t inhibit_until;
} ps2_host;

static int odd_parity(unsigned b)
{
    int n = 0;

    for (; b; b >>= 1)
        n += (int)(b & 1U);
    return 1 - (n & 1);
}

static void ps2_init(ps2_host *h, const ps2_send *sends, int nsends)
{
    memset(h, 0, sizeof *h);
    h->sends = sends;
    h->nsends = nsends;
    h->clk = h->dat = 1;
    h->ph_min = 0xFFFFFFFFU;
}

static void ps2_error(ps2_host *h, uint32_t k)
{
    if (!h->errors++)
        h->first_error = k;
}

/* the levels the host leaves on CLK and DATA (1 = released) after cycle k */
static uint8_t ps2_step(ps2_host *h, uint32_t k, msf_pins p)
{
    int chip_clk = ((p.oe >> PS2_CLK) & 1U) && !((p.out >> PS2_CLK) & 1U);
    int chip_dat = ((p.oe >> PS2_DAT) & 1U) && !((p.out >> PS2_DAT) & 1U);
    int clk = !(chip_clk || h->pull_clk), dat = !(chip_dat || h->pull_dat);
    int fall = h->clk && !clk, rise = clk && !h->clk;
    int quiet = clk && !h->nbits && k - h->last_edge >= 50U * PS2_US;
    int i;

    /* the host starts a command when it is due and the bus is quiet */
    if (!h->tx_on && h->isend < h->nsends && k >= h->sends[h->isend].at && quiet) {
        const ps2_send *s = &h->sends[h->isend++];

        for (i = 0; i < 8; ++i)
            h->tx_bits[i] = (s->b >> i) & 1;
        h->tx_bits[8] = odd_parity(s->b) ^ (s->good ? 0 : 1);
        h->tx_bits[9] = 1;
        h->tx_n = 0;
        h->tx_t = k;
        h->tx_stage = 0;
        h->tx_on = 1;
        h->pull_clk = 1;
    }
    if (h->tx_on) {
        if (h->tx_stage == 0 && k - h->tx_t >= 110U * PS2_US) {
            h->pull_dat = 1;
            h->tx_stage = 1;
        } else if (h->tx_stage == 1 && k - h->tx_t >= 115U * PS2_US) {
            h->pull_clk = 0;
            h->tx_stage = 2;
        } else if (h->tx_stage == 2 && fall) {
            if (h->tx_n < 10) {
                h->pull_dat = 1 - h->tx_bits[h->tx_n++];
            } else {                        /* the 11th clock: the device's ACK */
                if (h->nacks < 16)
                    h->acks[h->nacks++] = dat == 0;
                h->tx_stage = 3;
            }
        } else if (h->tx_stage == 3 && rise) {
            h->tx_on = 0;
        }
    } else if (h->inhibit_on) {
        if (k >= h->inhibit_until) {
            h->pull_clk = 0;
            h->inhibit_on = 0;
        }
    } else {
        /* device to host: DATA must not change while CLK is low */
        if (dat != h->dat && !clk && !h->pull_clk)
            ps2_error(h, k);
        if (fall) {
            if (!h->nbits)
                h->frames_started++;
            h->bits[h->nbits++] = dat;
            if (h->inh_armed && h->frames_started == h->inh_frame && h->nbits == h->inh_bits) {
                h->pull_clk = 1;
                h->inhibit_on = 1;
                h->inhibit_until = k + h->inh_len;
                h->nbits = 0;
                h->inh_armed = 0;
            } else if (h->nbits == 11) {
                unsigned v = 0;

                for (i = 1; i < 9; ++i)
                    v |= (unsigned)h->bits[i] << (i - 1);
                if (h->bits[0] != 0 || h->bits[10] != 1 || h->bits[9] != odd_parity(v))
                    ps2_error(h, k);
                if (h->ngot < 32)
                    h->got[h->ngot++] = (uint8_t)v;
                h->nbits = 0;
            }
        }
    }
    /* clock phases inside a device frame: lows, and highs between bits */
    if ((fall || rise) && !h->pull_clk && !h->tx_on) {
        int in_frame = (rise && h->last_fall_bits >= 1) || (fall && h->nbits >= 2 && h->nbits <= 11);

        if (in_frame && h->last_edge) {
            uint32_t ph = k - h->last_edge;

            if (ph < 100U * PS2_US) {
                if (ph < h->ph_min)
                    h->ph_min = ph;
                if (ph > h->ph_max)
                    h->ph_max = ph;
                h->nph++;
            }
        }
        if (fall)
            h->last_fall_bits = h->nbits;
        h->last_edge = k;
    }
    h->clk = clk;
    h->dat = dat;
    return (uint8_t)((h->pull_clk ? 0U : 1U) << PS2_CLK | (h->pull_dat ? 0U : 1U) << PS2_DAT);
}

/* test_buses.ps2_run: ps2_device.s, `ub` sent on its UART from cycle 400;
 * returns the bytes it sent on its UART */
static int ps2_run(const msf_image *img, ps2_host *h, const uint8_t *ub, int nub,
                   uint32_t cycles, int *back, int maxback)
{
    mon_item items[8];
    uint8_t uio = 0x02U | 1U << PS2_CLK | 1U << PS2_DAT;
    uint32_t k;
    int i;

    for (i = 0; i < nub; ++i) {
        items[i].v = ub[i];
        items[i].bit = MON_BIT;
        items[i].stop = 1;
        items[i].idle = 20U * MON_BIT;
    }
    if (fw_boot(img, 0, 0, uio))
        return -1;
    for (k = 0; k < cycles; ++k) {
        msf_pins p = cycle(uio);
        int line = k >= 400U && nub ? mon_line(items, (uint32_t)nub, k - 400U) : 1;
        uint8_t ext = (uint8_t)(ps2_step(h, k, p) | line << 1);

        uio = (uint8_t)((p.out & p.oe) | (ext & ~p.oe));
    }
    CHECK(!tr.overflow, "trace overflow");
    CHECK(!h->errors, "%d protocol errors, the first in cycle %d", h->errors, (int)h->first_error);
    return (int)msf_uart_decode(&tr, MON_BIT, 0, back, (uint32_t)maxback);
}

/* test_buses.test_ps2_device_types_scancodes: scancodes from the UART reach
 * the host as valid frames, in order, every clock phase within 30-50 us */
static int test_ps2_device_types_scancodes(void)
{
    static const uint8_t codes[4] = {0x1C, 0xF0, 0x1C, 0x5A};
    static ps2_host h;
    int back[8], i;

    ps2_init(&h, 0, 0);
    if (ps2_run(&img_ps2_q240, &h, codes, 4, 400U + 4U * 30U * MON_BIT + 4U * 12U * 1000U, back,
                8) < 0)
        return -1;
    CHECK(h.ngot == 4, "the host got %d bytes, want 4", h.ngot);
    for (i = 0; i < 4; ++i)
        CHECK(h.got[i] == codes[i], "byte %d = %02x, want %02x", i, h.got[i], codes[i]);
    CHECK(h.nph > 0 && h.ph_min >= 30U * PS2_US && h.ph_max <= 50U * PS2_US,
          "%d clock phases, %d..%d cycles, want 360..600", h.nph, (int)h.ph_min, (int)h.ph_max);
    return 0;
}

/* test_buses.test_ps2_device_answers_commands: reset, set LEDs (and its
 * argument), echo and read ID get a keyboard's answers; every command byte
 * is ACKed in the 11th clock and reported on the UART; bad parity gets FE */
static int test_ps2_device_answers_commands(void)
{
    static const uint8_t cb[6] = {0xFF, 0xED, 0x02, 0xEE, 0xF2, 0x55};
    static const uint8_t want[9] = {0xFA, 0xAA, 0xFA, 0xFA, 0xEE, 0xFA, 0xAB, 0x83, 0xFE};
    static const uint8_t back_want[5] = {0xFF, 0xED, 0x02, 0xEE, 0xF2};
    static ps2_send sends[6];
    static ps2_host h;
    int back[16], n, i;

    for (i = 0; i < 6; ++i) {
        sends[i].at = 2000U + (uint32_t)i * 9000U;
        sends[i].b = cb[i];
        sends[i].good = i < 5;
    }
    ps2_init(&h, sends, 6);
    n = ps2_run(&img_ps2_q60, &h, 0, 0, 2000U + 6U * 9000U + 6000U, back, 16);
    if (n < 0)
        return -1;
    CHECK(h.nacks == 6, "%d ACKs, want 6", h.nacks);
    for (i = 0; i < 6; ++i)
        CHECK(h.acks[i], "command %d not ACKed", i);
    CHECK(h.ngot == 9, "the host got %d bytes, want 9", h.ngot);
    for (i = 0; i < 9; ++i)
        CHECK(h.got[i] == want[i], "byte %d = %02x, want %02x", i, h.got[i], want[i]);
    CHECK(n == 5, "%d bytes on the UART, want 5", n);
    for (i = 0; i < 5; ++i)
        CHECK(back[i] == back_want[i], "UART byte %d = %02x, want %02x", i, back[i],
              back_want[i]);
    return 0;
}

/* test_buses.test_ps2_device_host_inhibit: the host pulls CLK low after 4
 * bits of a frame, for 150 us; the device sends the whole byte again */
static int test_ps2_device_host_inhibit(void)
{
    static const uint8_t codes[2] = {0x1C, 0x32};
    static ps2_host h;
    int back[8];

    ps2_init(&h, 0, 0);
    h.inh_armed = 1;
    h.inh_frame = 1;
    h.inh_bits = 4;
    h.inh_len = 150U * PS2_US;
    if (ps2_run(&img_ps2_q60, &h, codes, 2, 400U + 2U * 30U * MON_BIT + 8000U, back, 8) < 0)
        return -1;
    CHECK(h.ngot == 2 && h.got[0] == 0x1C && h.got[1] == 0x32,
          "the host got %d bytes (%02x %02x), want 1c 32", h.ngot, h.got[0], h.got[1]);
    return 0;
}

/* ======================================================================== */
/* CAN 2.0A (test_buses): can.s against a node model written from the
 * specification. The chip's TXD is uio2, its RXD uio7; the bus is the
 * wired AND of the two nodes' TXD. UART RX uio1 / TX uio0.                */

#define CAN_TX  2
#define CAN_RX  7
#define CAN_BT  480U                /* can.s default: 50 kbit/s at 24 MHz */
#define CAN_SP  (CAN_BT / 2U)

typedef struct {
    uint16_t ident;
    uint8_t data[8];
    int len, bad_crc;
} can_frame;

static uint16_t can_crc15(const uint8_t *bits, int n)
{
    uint16_t crc = 0;
    int i;

    for (i = 0; i < n; ++i) {
        int nxt = bits[i] ^ ((crc >> 14) & 1);

        crc = (uint16_t)((crc << 1) & 0x7FFF);
        if (nxt)
            crc ^= 0x4599;
    }
    return crc;
}

static int can_msb(uint8_t *b, int n, unsigned v, int w)
{
    int i;

    for (i = 0; i < w; ++i)
        b[n++] = (uint8_t)((v >> (w - 1 - i)) & 1U);
    return n;
}

/* test_buses.can_bits then stuff: a data frame, SOF to the end of the
 * CRC, with stuff bits */
static int can_stuffed(const can_frame *f, uint8_t *out)
{
    uint8_t bits[128];
    int n = 0, i, m = 0, run = 0, last = -1;

    bits[n++] = 0;
    n = can_msb(bits, n, f->ident, 11);
    n = can_msb(bits, n, 0, 3);
    n = can_msb(bits, n, (unsigned)f->len, 4);
    for (i = 0; i < f->len; ++i)
        n = can_msb(bits, n, f->data[i], 8);
    n = can_msb(bits, n, can_crc15(bits, n) ^ (f->bad_crc ? 1U : 0U), 15);
    for (i = 0; i < n; ++i) {
        if (run == 5) {
            out[m++] = (uint8_t)(1 - last);
            run = 1;
            last = 1 - last;
        }
        out[m++] = bits[i];
        run = bits[i] == last ? run + 1 : 1;
        last = bits[i];
    }
    if (run == 5)
        out[m++] = (uint8_t)(1 - last);
    return m;
}

enum { CAN_FRAME, CAN_DELIM, CAN_ACK, CAN_EOF };

/* test_buses.CanNode: takes part after 10 recessive bits, hard-syncs on
 * SOF, drives at bit starts and samples at CAN_SP; receives every frame
 * and ACKs a good one if `ack`; sends each queued frame once, after 11 idle
 * bits and not before `start`, or joins the chip's SOF if `join` */
typedef struct {
    const can_frame *q;
    int nq, iq, ack, join;
    uint32_t start, gap;
    int txd, bus_prev, active, tx;
    uint32_t idle_since, t0, k;
    uint8_t bits[160], txbits[200];
    int nbits, runl, last, stuff_next, total, crc_done, crc_ok, phase, n, ntx, txi;
    int sent[8], nsent, errors;
    can_frame got[8];
    int ngot;
} can_node;

static void can_init(can_node *c, const can_frame *q, int nq, int ack, uint32_t start, int join,
                     uint32_t gap)
{
    memset(c, 0, sizeof *c);
    c->q = q;
    c->nq = nq;
    c->ack = ack;
    c->start = start;
    c->join = join;
    c->gap = gap;
    c->txd = c->bus_prev = 1;
}

static void can_begin(can_node *c, uint32_t k, int tx, int own_sof)
{
    c->active = 1;
    c->t0 = k;
    c->tx = tx;
    c->nbits = c->runl = 0;
    c->last = 1;
    c->stuff_next = 0;
    c->total = -1;
    c->crc_done = 0;
    c->phase = CAN_FRAME;
    c->n = 0;
    if (tx) {
        c->ntx = can_stuffed(&c->q[c->iq], c->txbits);
        c->txi = 1;
        if (own_sof)
            c->txd = 0;                    /* SOF now */
    }
}

static unsigned can_field(const uint8_t *b, int at, int w)
{
    unsigned v = 0;
    int j;

    for (j = 0; j < w; ++j)
        v = v << 1 | b[at + j];
    return v;
}

static void can_sample(can_node *c, int b, int i)
{
    if (c->phase == CAN_FRAME) {
        if (c->tx && i < c->ntx && c->txbits[i] == 1 && b == 0) {
            c->tx = 0;                     /* lost arbitration */
            c->txd = 1;
        }
        if (c->stuff_next) {
            if (b == c->last)
                c->errors++;               /* stuff error */
            c->runl = 1;
            c->last = b;
            c->stuff_next = 0;
        } else {
            if (c->nbits < 160)
                c->bits[c->nbits++] = (uint8_t)b;
            c->runl = b == c->last ? c->runl + 1 : 1;
            c->last = b;
            c->stuff_next = c->runl == 5;
            if (c->nbits == 19) {
                unsigned dlc = can_field(c->bits, 15, 4);

                c->total = 19 + 8 * (int)(dlc < 8U ? dlc : 8U) + 15;
            }
            if (c->nbits == c->total) {
                c->crc_ok = can_crc15(c->bits, c->nbits) == 0;
                c->crc_done = 1;
            }
        }
        if (c->crc_done && !c->stuff_next)
            c->phase = CAN_DELIM;          /* the next bit */
    } else if (c->phase == CAN_DELIM) {
        c->phase = CAN_ACK;
    } else if (c->phase == CAN_ACK) {
        if (c->tx) {
            if (c->nsent < 8)
                c->sent[c->nsent++] = b == 0;   /* ACKed? */
            c->iq++;
            c->start = c->k + c->gap;
        } else if (c->crc_ok && c->ngot < 8) {
            can_frame *g = &c->got[c->ngot++];
            int n;

            g->ident = (uint16_t)can_field(c->bits, 1, 11);
            g->len = (int)can_field(c->bits, 15, 4);
            for (n = 0; n < (g->len < 8 ? g->len : 8); ++n)
                g->data[n] = (uint8_t)can_field(c->bits, 19 + 8 * n, 8);
        }
        c->phase = CAN_EOF;
        c->n = 0;
    } else if (++c->n >= 8) {              /* ACK delimiter + 7 EOF bits */
        c->active = 0;
        c->txd = 1;
    }
}

/* the level the node puts on the chip's RXD after cycle k */
static int can_step(can_node *c, uint32_t k, msf_pins p)
{
    int chip = (p.out >> CAN_TX) & 1, bus = chip & c->txd;

    if (!c->active) {
        int idle = k - c->idle_since >= 10U * CAN_BT;    /* bus integration */

        if (bus == 0 && c->bus_prev == 1 && idle)        /* someone's SOF */
            can_begin(c, k, c->join && c->iq < c->nq, 0);
        else if (bus == 1 && c->iq < c->nq && k >= c->start && k - c->idle_since >= 11U * CAN_BT)
            can_begin(c, k, 1, 1);
    }
    if (bus == 0)
        c->idle_since = k;
    if (c->active) {
        uint32_t t = (k - c->t0) % CAN_BT, i = (k - c->t0) / CAN_BT;

        c->k = k;
        if (t == 0U && i > 0U) {                         /* bit start: drive */
            if (c->phase == CAN_FRAME && c->tx && c->txi < c->ntx)
                c->txd = c->txbits[c->txi++];
            else
                c->txd = c->phase == CAN_ACK && !c->tx && c->ack && c->crc_ok ? 0 : 1;
        }
        if (t == CAN_SP)
            can_sample(c, bus, (int)i);
    }
    c->bus_prev = bus;
    return chip & c->txd;
}

/* test_buses.can_run: can.s with `cmd` sent on its UART from cycle 400
 * (the last byte followed by 40 idle bits); the UART bytes it returns */
static int can_run(can_node *c, const uint8_t *cmd, int ncmd, uint32_t cycles, int *back, int max)
{
    mon_item items[16];
    uint8_t uio = 0x02U | 1U << CAN_RX;
    uint32_t k;
    int i;

    for (i = 0; i < ncmd; ++i) {
        items[i].v = cmd[i];
        items[i].bit = MON_BIT;
        items[i].stop = 1;
        items[i].idle = (uint16_t)(i == ncmd - 1 ? 40U * MON_BIT : MON_BIT);
    }
    if (fw_boot(&img_can, 0, 0, uio))
        return -1;
    for (k = 0; k < cycles; ++k) {
        msf_pins p = cycle(uio);
        int line = k >= 400U && ncmd ? mon_line(items, (uint32_t)ncmd, k - 400U) : 1;
        uint8_t ext = (uint8_t)(line << 1 | can_step(c, k, p) << CAN_RX);

        uio = (uint8_t)((p.out & p.oe) | (ext & ~p.oe));
    }
    CHECK(!tr.overflow, "trace overflow");
    CHECK(!c->errors, "the node saw %d stuff errors", c->errors);
    return (int)msf_uart_decode(&tr, MON_BIT, 0, back, (uint32_t)max);
}

static int can_check_back(const int *back, int n, const uint8_t *want, int nw)
{
    int i;

    CHECK(n == nw, "%d UART bytes back, want %d", n, nw);
    for (i = 0; i < n; ++i)
        CHECK(back[i] == want[i], "UART byte %d = %02x, want %02x", i, back[i], want[i]);
    return 0;
}

/* test_buses.test_can_receive_and_ack: a node sends two frames; the chip
 * ACKs and reports each; one with a bad CRC is reported 'E' 'C', not ACKed */
static int test_can_receive_and_ack(void)
{
    static const can_frame q[3] = {{0x123, {0x11, 0x22, 0x33}, 3, 0},
                                   {0x7FF, {0}, 0, 0},
                                   {0x055, {0xA5}, 1, 1}};
    static const uint8_t want[] = {'R', 0x01, 0x23, 3, 0x11, 0x22, 0x33, 'R', 0x07, 0xFF, 0,
                                   'E', 'C'};
    static can_node c;
    int back[32], n;

    can_init(&c, q, 3, 1, 2000U, 0, 40U * CAN_BT);
    n = can_run(&c, 0, 0, 2000U + 3U * 200U * CAN_BT, back, 32);
    if (n < 0)
        return -1;
    CHECK(c.nsent == 3 && c.sent[0] && c.sent[1] && !c.sent[2],
          "%d frames sent, ACKs %d %d %d, want 1 1 0", c.nsent, c.sent[0], c.sent[1], c.sent[2]);
    return can_check_back(back, n, want, (int)sizeof want);
}

/* test_buses.test_can_transmit: 'T' sends a data frame the node receives
 * with a good CRC and ACKs; the chip replies 'K' */
static int test_can_transmit(void)
{
    static const uint8_t cmd[12] = {'T', 0x02, 0xA5, 8, 0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0xFF,
                                    0x5A, 0x81};
    static const uint8_t want[1] = {'K'};
    static can_node c;
    int back[8], n, i;

    can_init(&c, 0, 0, 1, 0, 0, 0);
    n = can_run(&c, cmd, 12, 400U + 40U * MON_BIT * 13U + 200U * CAN_BT, back, 8);
    if (n < 0)
        return -1;
    CHECK(c.ngot == 1 && c.got[0].ident == 0x2A5 && c.got[0].len == 8,
          "the node got %d frames (first %03x, %d bytes), want one 2a5 of 8", c.ngot,
          c.got[0].ident, c.got[0].len);
    for (i = 0; i < 8; ++i)
        CHECK(c.got[0].data[i] == cmd[4 + i], "data byte %d = %02x, want %02x", i,
              c.got[0].data[i], cmd[4 + i]);
    return can_check_back(back, n, want, 1);
}

/* test_buses.test_can_arbitration_and_no_ack: the node joins the chip's
 * SOF with a lower identifier: the chip loses arbitration, reports the
 * node's frame, then sends its own again ('K'). With nobody to ACK: 8
 * tries, then 'N' */
static int test_can_arbitration_and_no_ack(void)
{
    static const can_frame q[1] = {{0x0F0, {0x42}, 1, 0}};
    static const uint8_t cmd1[5] = {'T', 0x01, 0x00, 1, 0x99};
    static const uint8_t want1[] = {'R', 0x00, 0xF0, 1, 0x42, 'K'};
    static const uint8_t cmd2[4] = {'T', 0x00, 0x01, 0};
    static const uint8_t want2[1] = {'N'};
    static can_node c;
    int back[16], n;

    can_init(&c, q, 1, 1, 1000000000U, 1, 0);
    n = can_run(&c, cmd1, 5, 400U + 40U * MON_BIT * 6U + 400U * CAN_BT, back, 16);
    if (n < 0 || can_check_back(back, n, want1, (int)sizeof want1))
        return -1;
    CHECK(c.nsent == 1 && c.sent[0], "the node sent %d frames (ACKed %d), want 1 ACKed",
          c.nsent, c.sent[0]);
    CHECK(c.ngot == 1 && c.got[0].ident == 0x100 && c.got[0].len == 1 && c.got[0].data[0] == 0x99,
          "the node got %d frames, want one 100 of [99]", c.ngot);
    can_init(&c, 0, 0, 0, 0, 0, 0);                    /* only closes the TXD -> RXD loop */
    n = can_run(&c, cmd2, 4, 400U + 40U * MON_BIT * 5U + 8U * 80U * CAN_BT + 4000U, back, 16);
    if (n < 0)
        return -1;
    return can_check_back(back, n, want2, 1);
}

/* ======================================================================== */
/* Timing-fault injection (test_faults): the controllers' timing knobs
 * (firmware defines) move edges by exact amounts, measured at the pins.   */

static const msf_image *const fault_uart_knob_img[4] = {
    &img_uart_b104_s104,
    &img_uart_b99_s104,
    &img_uart_b110_s37,
    &img_uart_b104_s300,
};
static const msf_image *const fault_uart_sweep_img[21] = {
    &img_uart_b94_s94,
    &img_uart_b95_s95,
    &img_uart_b96_s96,
    &img_uart_b97_s97,
    &img_uart_b98_s98,
    &img_uart_b99_s99,
    &img_uart_b100_s100,
    &img_uart_b101_s101,
    &img_uart_b102_s102,
    &img_uart_b103_s103,
    &img_uart_b104_s104,
    &img_uart_b105_s105,
    &img_uart_b106_s106,
    &img_uart_b107_s107,
    &img_uart_b108_s108,
    &img_uart_b109_s109,
    &img_uart_b110_s110,
    &img_uart_b111_s111,
    &img_uart_b112_s112,
    &img_uart_b113_s113,
    &img_uart_b114_s114,
};
static const msf_image *const fault_i2c_img[5] = {
    &img_i2c_ql9_qh9,
    &img_i2c_ql9_qh3,
    &img_i2c_ql2_qh9,
    &img_i2c_ql5_qh14,
    &img_i2c_ql1_qh1,
};
static const msf_image *const fault_spi_img[4] = {
    &img_bridge_sh1_sl1,
    &img_bridge_sh3_sl1,
    &img_bridge_sh1_sl5,
    &img_bridge_sh10_sl2,
};
static uint8_t lvbuf[TRACE_CAP];

/* test_faults.runs: (level, length) runs of lv[] from its first edge at or
 * after `start`; the last run ends with the trace */
static int lv_runs(uint32_t n, uint32_t start, uint8_t *rl, uint32_t *rn, int max)
{
    uint32_t k, a = 0;
    int m = 0, have = 0;

    for (k = start < 1U ? 1U : start; k < n; ++k) {
        if (lvbuf[k] != lvbuf[k - 1U]) {
            if (have && m < max) {
                rl[m] = lvbuf[a];
                rn[m++] = k - a;
            }
            a = k;
            have = 1;
        }
    }
    if (have && m < max) {
        rl[m] = lvbuf[a];
        rn[m++] = n - a;
    }
    return m;
}

/* test_faults.uart_frames: uart_tx.s (BIT, STOP set by the image) sends
 * `msg`; lvbuf = uio_out[0] per cycle until DONE. Returns the length. */
static int uart_frames(const msf_image *img, const char *msg, uint32_t bit, uint32_t stop,
                       uint32_t *n)
{
    uint16_t data[16];
    uint32_t i, len = (uint32_t)strlen(msg), limit = 20U * (bit + stop) * (len + 2U), c;

    for (i = 0; i <= len; ++i)
        data[i] = (uint8_t)msg[i];
    if (fw_boot(img, data, (uint16_t)(len + 1U), 0))
        return -1;
    for (c = 0; c < limit; ++c)
        if (cycle(0).uo == DONE)
            break;
    CHECK(c < limit, "uo_out never showed DONE");
    CHECK(!tr.overflow, "trace overflow");
    for (i = 0; i < tr.n; ++i)
        lvbuf[i] = (uint8_t)(tr.p[i].out & 1U);
    *n = tr.n;
    return 0;
}

/* test_faults.test_fault_uart_knobs: every bit of 0x55 0x55 lasts BIT,
 * the stop bit STOP */
static int test_fault_uart_knobs(void)
{
    static const uint32_t ks[4][2] = {{104, 104}, {99, 104}, {110, 37}, {104, 300}};
    uint8_t rl[32];
    uint32_t rn[32], n, first, k;
    int t, i, m;

    for (t = 0; t < 4; ++t) {
        if (uart_frames(fault_uart_knob_img[t], "UU", ks[t][0], ks[t][1], &n))
            return -1;
        for (first = 1; first < n && !(lvbuf[first - 1U] == 1 && lvbuf[first] == 0); ++first)
            ;
        CHECK(first < n, "BIT %d STOP %d: no start bit", (int)ks[t][0], (int)ks[t][1]);
        m = lv_runs(n, first, rl, rn, 32);
        CHECK(m >= 19, "BIT %d STOP %d: %d runs", (int)ks[t][0], (int)ks[t][1], m);
        /* each frame: start and b0..b7 alternate (9 runs of one bit), then stop */
        for (i = 0; i < 19; ++i) {
            if (i == 9)
                continue;
            k = rn[i];
            CHECK(k == ks[t][0], "BIT %d STOP %d: run %d is %d cycles", (int)ks[t][0],
                  (int)ks[t][1], i, (int)k);
        }
        CHECK(rl[9] == 1 && rn[9] == ks[t][1], "BIT %d STOP %d: the stop bit is %d for %d",
              (int)ks[t][0], (int)ks[t][1], rl[9], (int)rn[9]);
    }
    return 0;
}

/* test_faults.mid_bit_receiver: find each start edge, sample every bit at
 * its nominal centre (no re-sync in a frame); bytes, -1 for a stop bit
 * read low */
static int mid_bit_receiver(uint32_t n, uint32_t nominal, int *out, int max)
{
    uint32_t i = 1, k;
    int m = 0;

    while (i < n && m < max) {
        if (lvbuf[i - 1U] == 1 && lvbuf[i] == 0) {
            int s[10], v = 0;

            for (k = 0; k < 10U; ++k) {
                uint32_t at = i + nominal / 2U + k * nominal;

                s[k] = lvbuf[at < n ? at : n - 1U];
            }
            if (s[0] != 0) {
                ++i;
                continue;
            }
            for (k = 1; k < 9U; ++k)
                v |= s[k] << (k - 1U);
            out[m++] = s[9] == 1 ? v : -1;
            i += 9U * nominal + nominal / 2U;
        } else {
            ++i;
        }
    }
    return m;
}

/* test_faults.test_fault_uart_tolerance_sweep: bit times 94..114 against a
 * receiver sampling at 104's centres: 0x55 decodes exactly for 99..109 */
static int test_fault_uart_tolerance_sweep(void)
{
    int got[8], m, ok, b;
    uint32_t n;

    for (b = 94; b <= 114; ++b) {
        if (uart_frames(fault_uart_sweep_img[b - 94], "UUU", (uint32_t)b, (uint32_t)b, &n))
            return -1;
        m = mid_bit_receiver(n, 104U, got, 8);
        ok = m == 3 && got[0] == 0x55 && got[1] == 0x55 && got[2] == 0x55;
        CHECK(ok == (b >= 99 && b <= 109), "BIT %d: the receiver %s, want it to %s", b,
              ok ? "decodes 55 55 55" : "fails", b >= 99 && b <= 109 ? "decode" : "fail");
    }
    return 0;
}

/* test_faults.i2c_scl: i2c_master.s writing to an absent device (address
 * NACKed, then STOP); SCL (uio6, open drain, pulled up) runs from its
 * first edge */
static int i2c_scl(const msf_image *img, uint8_t *rl, uint32_t *rn, int max)
{
    static const uint16_t data[4] = {0x50, 1, 0xA5, 0};
    uint8_t uio = 0x60U;
    uint32_t c, first;

    if (fw_boot(img, data, 4, uio))
        return -1;
    for (c = 0; c < 20000U; ++c) {
        msf_pins p = cycle(uio);

        uio = (uint8_t)((p.out & p.oe) | (0x60U & ~p.oe));
        if (p.uo & 0x80U)
            break;
    }
    CHECK(c < 20000U, "i2c_master never finished");
    for (c = 0; c < tr.n; ++c)
        lvbuf[c] = (uint8_t)((tr.p[c].oe >> 6) & 1U ? (tr.p[c].out >> 6) & 1U : 1U);
    for (first = 1; first < tr.n && lvbuf[first] == lvbuf[first - 1U]; ++first)
        ;
    return lv_runs(tr.n, first, rl, rn, max);
}

/* test_faults.test_fault_i2c_knobs: SCL high and low phases move by
 * exactly 3 cycles per QH / QL count (runs 2..15: the address byte) */
static int test_fault_i2c_knobs(void)
{
    static const int q[5][2] = {{9, 9}, {9, 3}, {2, 9}, {5, 14}, {1, 1}};
    uint8_t rl0[32], rl[32];
    uint32_t rn0[32], rn[32];
    int t, i;

    if (i2c_scl(fault_i2c_img[0], rl0, rn0, 32) < 16)
        return -1;
    for (t = 1; t < 5; ++t) {
        if (i2c_scl(fault_i2c_img[t], rl, rn, 32) < 16)
            return -1;
        for (i = 2; i < 16; ++i) {
            int d = rl0[i] ? 3 * (q[t][1] - 9) : 3 * (q[t][0] - 9);

            CHECK(rl[i] == rl0[i] && (int)rn[i] == (int)rn0[i] + d,
                  "QL %d QH %d: run %d is %d for %d, want %d for %d", q[t][0], q[t][1], i, rl[i],
                  (int)rn[i], rl0[i], (int)rn0[i] + d);
        }
    }
    return 0;
}

/* test_faults.test_fault_spi_knobs: the bridge's SCK (mode 0) is high
 * 4 + 4 SH cycles and low 10 + 4 SL */
static int test_fault_spi_knobs(void)
{
    static const int ks[4][2] = {{1, 1}, {3, 1}, {1, 5}, {10, 2}};
    static const mon_item items[2] = {{1, MON_BIT, 1, 200}, {0xA5, MON_BIT, 1, 200}};
    uint8_t rl[16];
    uint32_t rn[16], k, first, total = 400U + 2U * (10U * MON_BIT + 200U) + 2000U;
    int t, i, m;

    for (t = 0; t < 4; ++t) {
        if (fw_boot(fault_spi_img[t], 0, 0, 0x02U))
            return -1;
        for (k = 0; k < total; ++k)
            cycle(k >= 400U && !mon_line(items, 2, k - 400U) ? 0x00U : 0x02U);
        CHECK(!tr.overflow, "trace overflow");
        for (k = 0; k < tr.n; ++k)
            lvbuf[k] = (uint8_t)((tr.p[k].out >> 2) & 1U);
        for (first = 1; first < tr.n && lvbuf[first] == lvbuf[first - 1U]; ++first)
            ;
        m = lv_runs(tr.n, first, rl, rn, 16);
        CHECK(m == 16, "SH %d SL %d: %d SCK runs, want 16", ks[t][0], ks[t][1], m);
        for (i = 0; i < 16; ++i) {
            if (rl[i])
                CHECK((int)rn[i] == 4 + 4 * ks[t][0], "SH %d SL %d: a high of %d cycles",
                      ks[t][0], ks[t][1], (int)rn[i]);
            else if (i < 15)                  /* the last low: idle */
                CHECK((int)rn[i] == 10 + 4 * ks[t][1], "SH %d SL %d: a low of %d cycles",
                      ks[t][0], ks[t][1], (int)rn[i]);
        }
    }
    return 0;
}

/* ======================================================================== */
/* WS2812 (test_firmware): ws2812.s drives an LED chain on uio0 from the TX
 * engine, 15 cycles a bit (800 kHz at 12 MHz).                            */

/* test_firmware.test_fw_ws2812: every bit 15 cycles, a 1 high for 10, a 0
 * for 5 (the LEDs allow +-2), no gap between bits, the colours in order,
 * then the line low for over 50 us to latch them */
static int test_fw_ws2812(void)
{
    static const uint16_t data[10] = {3, 0x10, 0xFF, 0x00, 0xA5, 0x5A, 0x81, 0x00, 0x00, 0xFF};
    static uint32_t rises[72];
    uint32_t nr = 0, c, k, high = 0;
    int b, j, v;

    if (fw_boot(&img_ws2812, data, 10, 0))
        return -1;
    for (c = 0; c < 20000U; ++c)
        if (cycle(0).uo == DONE)
            break;
    CHECK(c < 20000U, "uo_out never showed DONE");
    CHECK(!tr.overflow, "trace overflow");
    for (k = 1; k < tr.n; ++k) {
        if ((tr.p[k].out & 1U) && !(tr.p[k - 1U].out & 1U)) {
            if (nr < 72U)
                rises[nr] = k;
            ++nr;
        }
    }
    CHECK(nr == 72U, "%d rising edges, want 72 (3 LEDs)", (int)nr);
    for (k = 1; k < 72U; ++k)
        CHECK(rises[k] - rises[k - 1U] == 15U, "bit %d starts %d cycles after the one before, want 15",
              (int)k, (int)(rises[k] - rises[k - 1U]));
    for (b = 0; b < 9; ++b) {
        v = 0;
        for (j = 0; j < 8; ++j) {
            k = rises[8 * b + j];
            for (high = 0; k + high < tr.n && (tr.p[k + high].out & 1U); ++high)
                ;
            CHECK(high == 5U || high == 10U, "bit %d is high for %d cycles", 8 * b + j, (int)high);
            v = (v << 1) | (high == 10U);
        }
        CHECK(v == data[1 + b], "byte %d = %02x, want %02x", b, v, data[1 + b]);
    }
    CHECK(tr.n - rises[71] - high >= 600U, "the line is low %d cycles at the end, want 600 (latch)",
          (int)(tr.n - rises[71] - high));
    return 0;
}

/* ======================================================================== */
/* Input filter and Manchester RX (test_peripherals). The Python tests read
 * registers or the SRAM; these store the results in RAM and read it back
 * with dump_ram.                                                          */

/* test_peripherals.wait_uo: step with uio_in = uio until uo_out = v */
static int wait_uo(uint8_t v, uint8_t uio, uint32_t limit)
{
    uint32_t c;

    for (c = 0; c < limit; ++c)
        if (cycle(uio).uo == v)
            break;
    CHECK(c < limit, "uo_out never showed %02x", v);
    return 0;
}

/* filter_count.s: pulses 1, 2, 3, 4, 5 and 8 cycles wide on the pins of
 * `mask`, 40 cycles apart; the two threads' rise counts */
static int filter_pulses(const msf_image *img, uint8_t mask, uint16_t *got)
{
    static const uint32_t w[6] = {1, 2, 3, 4, 5, 8};
    uint32_t i, c;

    if (fw_boot(img, 0, 0, 0) || wait_uo(0x01U, 0, 1000U))
        return -1;
    for (i = 0; i < 6U; ++i) {
        for (c = 0; c < w[i]; ++c)
            cycle(mask);
        for (c = 0; c < 40U; ++c)
            cycle(0);
    }
    if (wait_uo(DONE, 0, 2000U))
        return -1;
    for (c = 0; c < 50U; ++c)           /* T1 stores its count too */
        cycle(0);
    return dump_ram(0, 2, got);
}

/* test_peripherals.test_filter_removes_short_pulses: FILTER on uio2 with
 * len 4, uio3 left alone: uio3 shows all six rises, uio2 only the three of
 * 4 cycles or more */
static int test_filter_removes_short_pulses(void)
{
    uint16_t got[2];

    if (filter_pulses(&img_filter_pulses, 0x0CU, got))
        return -1;
    CHECK(got[0] == 3U && got[1] == 6U, "uio2 (filtered) %d rises, uio3 %d: want 3 and 6",
          got[0], got[1]);
    return 0;
}

/* test_peripherals.test_filter_every_pin: FILTER on all eight uio pins (len
 * 4): on each pin only the three pulses of 4 cycles or more get through */
static int test_filter_every_pin(void)
{
    static const msf_image *const img[8] = {
        &img_filter_pin0, &img_filter_pin1, &img_filter_pin2, &img_filter_pin3,
        &img_filter_pin4, &img_filter_pin5, &img_filter_pin6, &img_filter_pin7,
    };
    uint16_t got[2];
    int pin;

    for (pin = 0; pin < 8; ++pin) {
        if (filter_pulses(img[pin], 0xFFU, got))
            return -1;
        CHECK(got[0] == 3U && got[1] == 3U, "uio%d: %d and %d rises got through, want 3", pin,
              got[0], got[1]);
    }
    return 0;
}

/* test_peripherals.test_filter_delay: a filtered pin's change arrives
 * exactly len cycles after the same change on an unfiltered pin */
static int test_filter_delay(void)
{
    static const msf_image *const img[2] = {&img_filter_delay4, &img_filter_delay9};
    static const uint16_t len[2] = {4, 9};
    uint16_t got[1];
    uint32_t c;
    int t;

    for (t = 0; t < 2; ++t) {
        if (fw_boot(img[t], 0, 0, 0) || wait_uo(0x01U, 0, 1000U))
            return -1;
        for (c = 0; c < 20U; ++c)
            cycle(0);
        if (wait_uo(DONE, 0x0CU, 200U) || dump_ram(0, 1, got))
            return -1;
        CHECK(got[0] == len[t], "len %d: uio2 rose %d cycles after uio3", len[t], got[0]);
    }
    return 0;
}

/* test_peripherals.spiky_uart: one bit of 104 cycles with an inverted
 * 3-cycle spike at its centre, where the RX engine samples */
static uint32_t spiky_bit(uint8_t *lv, uint32_t m, int v)
{
    uint32_t i;

    for (i = 0; i < 104U; ++i)
        lv[m + i] = (uint8_t)(i >= 51U && i < 54U ? !v : v);
    return m + 104U;
}

/* test_peripherals.test_filter_uart_spikes: UART RX at 104 cycles a bit
 * with a spike at every bit centre and in the idle line: without the filter
 * the bytes come out wrong, with FILTER on the RX pin (len 6) they are
 * right */
static int test_filter_uart_spikes(void)
{
    static const uint8_t data[4] = {0x55, 0xA3, 0x00, 0xFF};
    static const msf_image *const img[2] = {&img_rx_uart_nofilt, &img_rx_uart_filt};
    static uint8_t lv[48U * 104U];
    uint16_t got[4];
    uint32_t m = 0, k;
    int t, i, j, ok;

    m = spiky_bit(lv, m, 1);
    m = spiky_bit(lv, m, 1);
    for (i = 0; i < 4; ++i) {
        m = spiky_bit(lv, m, 0);
        for (j = 0; j < 8; ++j)
            m = spiky_bit(lv, m, (data[i] >> j) & 1);
        m = spiky_bit(lv, m, 1);
        m = spiky_bit(lv, m, 1);
    }
    for (k = 0; k < 104U; ++k)
        lv[m++] = 1;
    for (t = 0; t < 2; ++t) {
        if (fw_boot(img[t], 0, 0, 0x02U) || wait_uo(0x01U, 0x02U, 1000U))
            return -1;
        for (k = 0; k < m; ++k)
            cycle((uint8_t)(lv[k] << 1));
        for (k = 0; k < 200U; ++k)
            cycle(0x02U);
        if (dump_ram(0, 4, got))
            return -1;
        ok = got[0] == data[0] && got[1] == data[1] && got[2] == data[2] && got[3] == data[3];
        CHECK(ok == t, "filter %s: got %02x %02x %02x %02x, want them %s", t ? "on" : "off",
              got[0], got[1], got[2], got[3], t ? "right" : "wrong");
    }
    return 0;
}

/* test_peripherals.drive_levels: uio_in[pin] = levels[k] like a real
 * transmitter: symbol k starts at round(k * period * (1 + ppm / 1e6)) + j_k,
 * j_k in [-jitter, jitter] per edge (it does not accumulate). The period is
 * in hundredths of a cycle, so the rounding is exact. */
static void drive_levels(int pin, const uint8_t *levels, uint32_t n, uint32_t period100,
                         int jitter, uint32_t seed)
{
    msf_rng rng;
    uint32_t k, c;
    int32_t start = 0, next;

    msf_rng_seed(&rng, seed);
    for (k = 0; k < n; ++k) {
        next = (int32_t)(((k + 1U) * period100 + 50U) / 100U) +
               (int32_t)msf_rng_range(&rng, 0, (uint32_t)(2 * jitter + 1)) - jitter;
        for (c = 0; c < (uint32_t)(next - start > 1 ? next - start : 1); ++c)
            cycle((uint8_t)((levels[k] & 1U) << pin));
        start = next;
    }
}

/* test_peripherals.test_rx_manchester_frames_jitter: Manchester RX (half-bit
 * = RX_DIV = 8): two frames separated by idle line, at -2 %, 0 and +2 %
 * rate with +-1 cycle of jitter on every edge. Both frames decode, the
 * second after the receiver re-locks. */
static int test_rx_manchester_frames_jitter(void)
{
    static const uint8_t f0[5] = {0x55, 0xA7, 0x3C, 0x00, 0xFF}, f1[2] = {0x81, 0x6E};
    static const uint32_t p100[3] = {800, 816, 784};
    uint8_t lv[2 * 8 * 5 + 6];
    uint16_t got[7];
    uint32_t c, n;
    int t, f, i, j, ok;

    for (t = 0; t < 3; ++t) {
        if (fw_boot(&img_rx_manch, 0, 0, 0) || wait_uo(0x01U, 0, 1000U))
            return -1;
        for (c = 0; c < 30U; ++c)
            cycle(0);
        for (f = 0; f < 2; ++f) {
            const uint8_t *w = f ? f1 : f0;
            int nw = f ? 2 : 5;

            n = 0;
            for (i = 0; i < nw; ++i)
                for (j = 0; j < 8; ++j) {       /* a bit b is 1-b then b */
                    lv[n++] = (uint8_t)!((w[i] >> j) & 1);
                    lv[n++] = (uint8_t)((w[i] >> j) & 1);
                }
            for (i = 0; i < 6; ++i)
                lv[n++] = 0;
            drive_levels(5, lv, n, p100[t], 1, (uint32_t)t + 1U);
        }
        if (wait_uo(DONE, 0, 5000U) || dump_ram(0, 7, got))
            return -1;
        ok = 1;
        for (i = 0; i < 7; ++i)
            ok &= got[i] == (i < 5 ? f0[i] : f1[i - 5]);
        CHECK(ok, "rate %d/800: got %02x %02x %02x %02x %02x %02x %02x", (int)p100[t], got[0],
              got[1], got[2], got[3], got[4], got[5], got[6]);
    }
    return 0;
}

/* test_peripherals.test_rx_manchester_loopback: TX Manchester (uio0) wired
 * to RX Manchester (uio3) by the bench, same chip, 6 cycles a half-bit (1
 * Mbit/s at 12 MHz), back to back */
static int test_rx_manchester_loopback(void)
{
    uint16_t words[13], got[13];
    msf_rng rng;
    uint32_t c;
    uint8_t uio = 0;
    int i;

    msf_rng_seed(&rng, 9U);
    words[0] = 0x55;
    for (i = 1; i < 13; ++i)
        words[i] = (uint16_t)msf_rng_range(&rng, 0, 256);
    if (fw_boot(&img_manch_loopback, words, 13, 0))
        return -1;
    for (c = 0; c < 10000U; ++c) {
        msf_pins p = cycle(uio);

        if (p.uo == DONE)
            break;
        uio = (uint8_t)((p.out & 1U) << 3);
    }
    CHECK(c < 10000U, "uo_out never showed DONE");
    if (dump_ram(64, 13, got))
        return -1;
    for (i = 0; i < 13; ++i)
        CHECK(got[i] == words[i], "word %d = %02x, want %02x", i, got[i], words[i]);
    return 0;
}

/* ======================================================================== */
/* Debug view (test_debug, isa.md §8): ui_in[2] high during reset makes
 * uo_out show the byte of internal state chosen by ui_in[7:2], page 1 with
 * ui_in[1]. Read in step mode, one core cycle a view (dbg_view).          */

#define DBG_STRAP 0x04U
#define DBG_JMP   0x4800U           /* JMP: opcode 0x09 << 11 */

/* test_debug: boot_asm with ui_in = ui at reset, then `cycles` cycles */
static int dbg_boot(const msf_image *img, uint8_t ui, uint32_t cycles)
{
    static const uint16_t zeros[128];
    uint32_t c;

    TRY(msf_boot_enter(0));
    TRY(msf_boot_write(0, zeros, 128));
    TRY(msf_boot_write(img->addr, img->words, img->count));
    TRY(msf_reset(ui, 0, 1));
    TRY(msf_run());
    for (c = 0; c < cycles; ++c)
        msf_step(0);
    return 0;
}

/* test_debug.view / view1: select a view and read it. On silicon no clock
 * edge is needed, but the board wrapper captures the pins for the PS only
 * after a step, so each read here is one core cycle: the tests read views
 * of parked state, or count that cycle. */
static uint8_t dbg_view(unsigned sel, int page)
{
    msf_set_ui((uint8_t)(sel << 2 | (page ? 0x02U : 0U) | MSF_BOOT_DIRECT));
    return msf_step(0).uo;
}

/* a 16-bit view: low byte at sel, high byte at sel | 1 */
static unsigned dbg_view16(unsigned sel, int page)
{
    unsigned hi = dbg_view(sel | 1U, page);

    return hi << 8 | dbg_view(sel, page);
}

/* test_debug.self_jump: address of the first `x: JMP x` in an image */
static unsigned dbg_self_jump(const msf_image *img)
{
    unsigned i;

    for (i = 0; i < img->count; ++i)
        if (img->words[i] == (DBG_JMP | (img->addr + i)))
            return img->addr + i;
    return 0;
}

/* test_debug.test_debug_view_reads_state: both threads' registers, the last
 * issued PC and word, flags, SR, the pins, the FIFO; the cycle counter's
 * low byte counts core cycles; uio is left alone */
static int test_debug_view_reads_state(void)
{
    static const unsigned regs1[8] = {0, 0xCAFE, 0, 0, 0, 0x0FF0, 0, 0};
    unsigned h0 = dbg_self_jump(&img_debug_prog), regs0[8] = {0, 0x1234, 0xBEEF, 0xFFFF,
                                                              0x8001, 0, 0, 0};
    int want[32], t, r, s, lo;

    CHECK(h0 != 0U, "no self-jump in debug_prog.s");
    regs0[6] = h0 + 1U;             /* t1 */
    if (dbg_boot(&img_debug_prog, DBG_STRAP | MSF_BOOT_DIRECT, 80))
        return -1;
    for (t = 0; t < 2; ++t)
        for (r = 0; r < 8; ++r) {
            unsigned got = dbg_view16((unsigned)(t << 4 | r << 1), 0),
                     v = t ? regs1[r] : regs0[r];

            CHECK(got == v, "T%d R%d: %04x, want %04x", t, r, got, v);
        }
    for (s = 0; s < 32; ++s)
        want[s] = 0;                /* 0x2C-0x3F: FIFO empty, no drops, reserved */
    want[0x00] = (int)(h0 & 0xFFU);
    want[0x01] = (int)(h0 >> 8);    /* PC[9:8]; T0 issued last, single mode */
    want[0x02] = (int)((DBG_JMP | h0) & 0xFFU);
    want[0x03] = (int)((DBG_JMP | h0) >> 8);
    want[0x04] = 0x6;               /* N1 Z1 N0 Z0 */
    want[0x05] = want[0x06] = -1;   /* the cycle counter: below */
    want[0x07] = 0x01;              /* SR */
    want[0x08] = 0x80;
    want[0x09] = 0x3C;
    want[0x0A] = 0xC3;
    want[0x0B] = 0x5A;
    for (s = 0; s < 32; ++s) {
        int got = dbg_view(0x20U + (unsigned)s, 0);

        CHECK(want[s] < 0 || got == want[s], "view %02x: %02x, want %02x", 0x20 + s, got,
              want[s]);
    }
    lo = dbg_view(0x25, 0);         /* two reads, one cycle apart */
    CHECK(dbg_view(0x25, 0) == ((lo + 1) & 0xFF), "the cycle counter's low byte did not count");
    CHECK(msf_sample().out == 0x3CU, "the debug view changed uio_out");
    return 0;
}

/* test_debug.test_debug_view_pc_in_bank1: the last issued PC's bits 9:8
 * (view 0x21) for code in SRAM bank 1 */
static int test_debug_view_pc_in_bank1(void)
{
    CHECK(img_debug_bank1.count == 950U - 128U + 1U, "debug_bank1.s is %d words",
          (int)img_debug_bank1.count);
    if (dbg_boot(&img_debug_bank1, DBG_STRAP | MSF_BOOT_DIRECT, 40))
        return -1;
    CHECK((dbg_view(0x21, 0) & 3U) == 950U >> 8, "PC[9:8] = %d", dbg_view(0x21, 0) & 3);
    CHECK(dbg_view(0x20, 0) == (950U & 0xFFU), "PC[7:0] = %02x", dbg_view(0x20, 0));
    return 0;
}

/* test_debug.test_debug_mode_off_without_strap: without the strap uo_out is
 * the firmware's, whatever ui_in[7:2] is */
static int test_debug_mode_off_without_strap(void)
{
    static const unsigned sel[4] = {0x00, 0x13, 0x20, 0x3F};
    int i;

    if (dbg_boot(&img_debug_prog, MSF_BOOT_DIRECT, 80))
        return -1;
    for (i = 0; i < 4; ++i)
        CHECK(dbg_view(sel[i], 0) == 0x5AU, "view %02x showed %02x, want the firmware's 5a",
              sel[i], dbg_view(sel[i], 0));
    return 0;
}

/* test_debug.test_debug_single_step: one core cycle at a time, the last
 * issued PC after each follows the program's order and R1 counts the loop's
 * INCRs. A read is a step (dbg_view), so the trace reads PC[7:0] only: the
 * loop is at 128-131 (test_debug_view_pc_in_bank1 reads PC[9:8]). */
static int test_debug_single_step(void)
{
    unsigned pcs[60], trace[60], r1;
    int n = 0, i, k, incrs = 0;

    if (dbg_boot(&img_debug_loop, DBG_STRAP | MSF_BOOT_DIRECT, 0))
        return -1;
    for (i = 0; i < 60; ++i) {
        pcs[i] = dbg_view(0x20, 0);
        if (i == 0 || pcs[i] != pcs[i - 1])
            trace[n++] = pcs[i];
    }
    for (k = 0; k < n && trace[k] != 128U; ++k)
        ;
    CHECK(n - k >= 13, "PC trace from 128: %d entries", n - k);
    for (i = 0; i < 13; ++i)
        CHECK(trace[k + i] == (i ? 129U + (unsigned)(i - 1) % 3U : 128U),
              "PC trace entry %d is %d, want %d", i, (int)trace[k + i],
              i ? 129 + (i - 1) % 3 : 128);
    for (i = k; i < n; ++i)
        incrs += trace[i] == 129U;
    /* R1's low byte, read in cycle 61, holds every INCR the trace shows (up
     * to cycle 60): the view shows the instruction issuing in the current
     * cycle, whose result lands at the next edge. Its high byte is 0. */
    r1 = dbg_view(0x02, 0);
    CHECK(dbg_view(0x03, 0) == 0U, "R1[15:8] is not 0");
    CHECK(r1 == (unsigned)incrs, "R1 = %d, INCR issued %d times", (int)r1, incrs);
    return 0;
}

/* test_debug.test_debug_view_page1: T2 and T3 registers, the running and
 * stalled threads, flags and T0's fetch pointer (four threads, all parked) */
static int test_debug_view_page1(void)
{
    static const unsigned tr_[5][3] = {
        {2, 1, 0x2211}, {2, 7, 0x2277}, {2, 3, 0xFFFF}, {3, 1, 0x3311}, {3, 3, 0},
    };
    unsigned h0 = dbg_self_jump(&img_debug_page1), flags;
    int i;

    CHECK(h0 != 0U, "no self-jump in debug_page1.s");
    if (dbg_boot(&img_debug_page1, DBG_STRAP | MSF_BOOT_DIRECT, 120))
        return -1;
    for (i = 0; i < 5; ++i) {
        unsigned got = dbg_view16((tr_[i][0] - 2U) << 4 | tr_[i][1] << 1, 1);

        CHECK(got == tr_[i][2], "T%d R%d: %04x, want %04x", (int)tr_[i][0], (int)tr_[i][1], got,
              tr_[i][2]);
    }
    CHECK(dbg_view(0x20, 1) == 0xF8U, "threads %02x, want f8 (all run, T3 stalled)",
          dbg_view(0x20, 1));
    flags = dbg_view(0x21, 1);
    CHECK(((flags >> 4) & 3U) == 2U && ((flags >> 6) & 3U) == 1U, "flags %02x", flags);
    CHECK(dbg_view(0x22, 1) == (h0 & 0xFFU), "T0's fetch pointer %02x, want %02x",
          dbg_view(0x22, 1), h0 & 0xFFU);
    return 0;
}

/* test_threads.test_tmo_per_thread: each thread has its own TMO; T1 starts
 * with its starter's (isa.md §7). T1's WAIT times out with the 10 it
 * inherited although T0 then set 0: the release is in T1's first own slot
 * at or after expiry, 12 stalled cycles, so CNT_R to CNT_R is 2 + 12 + 2.
 * T1's own TMO = 3 expires after 4, already in its own slot: 2 + 4 + 2.
 * T0 (TMO 0) is still waiting: the debug view's thread byte shows it. */
static int test_tmo_per_thread(void)
{
    uint16_t got[3];
    unsigned thr;

    if (dbg_boot(&img_tmo_threads, DBG_STRAP | MSF_BOOT_DIRECT, 200))
        return -1;
    thr = dbg_view(0x20, 1);        /* {running, stalled} */
    CHECK(thr == 0x31U, "threads %02x, want 31 (T0 and T1 run, T0 stalled)", thr);
    if (dump_ram(0, 3, got))
        return -1;
    CHECK(got[0] == 1U, "T1's first WAIT did not time out");
    CHECK(got[1] == 16U, "T1's first WAIT took %d cycles CNT_R to CNT_R, want 16", got[1]);
    CHECK(got[2] == 8U, "T1's second WAIT (its own TMO 3) took %d, want 8", got[2]);
    return 0;
}

/* ======================================================================== */
/* Capture engine (test_capture, isa.md §6.6). The bench drives the pins as
 * a function of CNT, so the value every sample must hold follows from the
 * spec's timing alone, and cap_ref restates the recording rules. CNT is
 * read once on the debug view (the strap at reset), then counted, one a
 * step. The firmware (cap_*.s) leaves its registers in RAM[0..4].         */

/* the bench's pin functions of CNT (test_capture's lambdas) */
enum { CF_T, CF_T_HI, CF_HALF, CF_DIP, CF_STEPS, CF_T_ODD };

static uint8_t cap_pins(int f, uint32_t t)
{
    static const uint32_t at[6] = {0, 300, 310, 360, 900, 905};
    static const uint8_t lv[6] = {0x00, 0x01, 0x03, 0x07, 0x0F, 0x00};
    uint32_t v = 0;
    int i;

    switch (f) {
    case CF_T:
        v = t;
        break;
    case CF_T_HI:                   /* the counter's high bits too */
        v = t + (t >> 8);
        break;
    case CF_HALF:
        v = t >> 1;
        break;
    case CF_DIP:                    /* pin 0 low only in cycles 400-439 */
        v = t >= 400U && t < 440U ? 0U : 0xFFU;
        break;
    case CF_STEPS:
        for (i = 0; i < 6; ++i)
            if (t >= at[i])
                v = lv[i];
        break;
    default:                        /* ui_in, bit 0 kept high */
        v = t | 1U;
        break;
    }
    return (uint8_t)v;
}

typedef struct {
    const msf_image *img;           /* cap_arm.s or cap_work.s, its defines matching: */
    int f, ui;                      /* pins = cap_pins(f, CNT), on ui_in if ui */
    uint32_t cycles;
    unsigned base, n, div, mask, val;
    int edge, chg, post;
} cap_case;

static uint16_t cap_words[4096];    /* cap_run's reference words */

/* test_capture.capture: run one capture; check the status, the trigger
 * position and the buffer against cap_ref. Gives the word count, the
 * trigger word's index and RAM[0..4]. */
static int cap_run(const cap_case *cc, int *nw, int *tw, uint16_t *res)
{
    static uint8_t samples[4096];
    static uint16_t mem[128];
    unsigned size = 2U << cc->n, block = cc->base & ~(size - 1U), nxt, tpos;
    uint32_t t, k;
    int i;

    if (dbg_boot(cc->img, DBG_STRAP | MSF_BOOT_DIRECT, 0))
        return -1;
    t = dbg_view(0x25, 0);          /* CNT in the cycle after this one (< 256) */
    for (k = 0; k < cc->cycles; ++k, ++t) {
        uint8_t v = cap_pins(cc->f, t);

        if (cc->ui) {
            msf_set_ui(v);
            msf_step(0);
        } else {
            msf_step(v);
        }
    }
    if (dump_ram(0, 5, res))
        return -1;
    for (k = 0; k < 4096U; ++k)
        samples[k] = cap_pins(cc->f, res[2] + k * cc->div);
    *tw = 0;
    *nw = cap_ref(samples, 4096, cc->chg, cc->mask, cc->val, cc->edge, cc->post, cap_words,
                  4096, tw);
    CHECK(*nw >= 0, "the reference capture does not finish in 4096 samples");
    nxt = block | ((unsigned)*nw & (size - 1U));
    tpos = block | ((unsigned)*tw & (size - 1U));
    CHECK((res[0] & (CS_RUN | CS_TRIG | CS_DONE | CS_OVF)) == (CS_TRIG | CS_DONE), "status %04x",
          res[0]);
    CHECK((res[0] & 0x3FFU) == nxt, "next address %d, want %d", res[0] & 0x3FF, (int)nxt);
    CHECK(res[1] == tpos, "trigger at %d, want %d", res[1], (int)tpos);
    if (dump_ram((uint16_t)block, (uint16_t)size, mem))
        return -1;
    for (i = *nw > (int)size ? *nw - (int)size : 0; i < *nw; ++i)
        CHECK(mem[(unsigned)i & (size - 1U)] == cap_words[i],
              "buffer word %d (at %d) = %04x, want %04x", i,
              (int)(block | ((unsigned)i & (size - 1U))), mem[(unsigned)i & (size - 1U)],
              cap_words[i]);
    return 0;
}

/* test_capture.test_cap_sample_mode: two samples a word, every cycle and
 * every 3rd; MASK 0 triggers on the first sample, then POST words follow */
static int test_cap_sample_mode(void)
{
    static const cap_case cc[2] = {
        {&img_cap_s1, CF_T, 0, 3000, 512, 4, 1, 0, 0, 0, 0, 15},
        {&img_cap_s3, CF_T, 0, 3000, 512, 4, 3, 0, 0, 0, 0, 15},
    };
    uint16_t res[5];
    int i, nw, tw;

    for (i = 0; i < 2; ++i) {
        if (cap_run(&cc[i], &nw, &tw, res))
            return -1;
        CHECK(nw == 16 && tw == 0, "DIV %d: %d words, trigger word %d; want 16 and 0",
              (int)cc[i].div, nw, tw);
    }
    return 0;
}

/* test_capture.test_cap_divisor_sizes: sample mode at divisors 9 to 4099,
 * where the sample tick counts through values with high bits set; the pin
 * pattern uses the counter's high bits too */
static int test_cap_divisor_sizes(void)
{
    static const msf_image *const img[6] = {&img_cap_d9, &img_cap_d10, &img_cap_d16,
                                            &img_cap_d33, &img_cap_d100, &img_cap_d4099};
    static const unsigned div[6] = {9, 10, 16, 33, 100, 4099};
    cap_case cc = {0, CF_T_HI, 0, 0, 512, 4, 0, 0, 0, 0, 0, 3};
    uint16_t res[5];
    int i, nw, tw;

    for (i = 0; i < 6; ++i) {
        cc.img = img[i];
        cc.div = div[i];
        cc.cycles = 8U * div[i] + 400U;
        if (cap_run(&cc, &nw, &tw, res))
            return -1;
        CHECK(nw == 4 && tw == 0, "DIV %d: %d words, trigger word %d; want 4 and 0",
              (int)div[i], nw, tw);
    }
    return 0;
}

/* test_capture.test_cap_pretrigger_ring: a level trigger on a pin value
 * long after arming; the ring keeps the words before it (16-word buffer,
 * 160 words recorded, 6 after) */
static int test_cap_pretrigger_ring(void)
{
    static const cap_case cc = {&img_cap_ring, CF_HALF, 0, 3000, 640, 3, 1, 0xFF, 0xC0, 0, 0, 6};
    uint16_t res[5];
    int nw, tw;

    if (cap_run(&cc, &nw, &tw, res))
        return -1;
    CHECK(tw >= 16 && nw == tw + 7, "%d words, trigger word %d", nw, tw);
    return 0;
}

/* test_capture.test_cap_edge_vs_level: pin 0 high from before arming, low
 * at cycle 400, high again at 440: a level trigger fires at once, an edge
 * trigger only at 440 */
static int test_cap_edge_vs_level(void)
{
    static const cap_case level = {&img_cap_level, CF_DIP, 0, 3000, 512, 6, 1, 1, 1, 0, 0, 4};
    static const cap_case edge = {&img_cap_edge, CF_DIP, 0, 3000, 512, 6, 1, 1, 1, 1, 0, 4};
    uint16_t res[5];
    int nw, tw;

    if (cap_run(&level, &nw, &tw, res))
        return -1;
    CHECK(tw == 0, "level trigger at word %d, want 0", tw);
    if (cap_run(&edge, &nw, &tw, res))
        return -1;
    CHECK(tw == (440 - (int)res[2]) / 2, "edge trigger at word %d, want %d", tw,
          (440 - (int)res[2]) / 2);
    return 0;
}

/* test_capture.test_cap_change_mode: a word {pins, ticks since the last}
 * at every change, and one after 255 ticks without one */
static int test_cap_change_mode(void)
{
    static const cap_case cc[2] = {
        {&img_cap_chg1, CF_STEPS, 0, 2600, 768, 4, 1, 0, 0, 0, 1, 8},
        {&img_cap_chg2, CF_STEPS, 0, 2600, 768, 4, 2, 0, 0, 0, 1, 8},
    };
    uint16_t res[5];
    int i, k, nw, tw, seen;

    for (i = 0; i < 2; ++i) {
        if (cap_run(&cc[i], &nw, &tw, res))
            return -1;
        for (seen = 0, k = 0; k < nw; ++k)
            seen |= (cap_words[k] & 0xFFU) == 255U;
        CHECK(seen, "DIV %d: no record after 255 ticks", (int)cc[i].div);
    }
    return 0;
}

/* test_capture.test_cap_ui_pins: CAP_UI records ui_in */
static int test_cap_ui_pins(void)
{
    static const cap_case cc = {&img_cap_ui, CF_T_ODD, 1, 3000, 512, 3, 2, 0, 0, 0, 0, 7};
    uint16_t res[5];
    int nw, tw;

    return cap_run(&cc, &nw, &tw, res);
}

/* test_capture.test_cap_never_changes_core_timing: the same data-RAM-heavy
 * loop takes exactly as long with the engine recording every cycle as
 * without it, and the capture is intact: the engine only uses free port-B
 * cycles */
static int test_cap_never_changes_core_timing(void)
{
    static const cap_case cc = {&img_cap_work_on, CF_T, 0, 3000, 512, 6, 1, 0, 0, 0, 0, 110};
    uint16_t res[5], idle[5];
    int nw, tw;

    if (cap_run(&cc, &nw, &tw, res))
        return -1;
    if (dbg_boot(&img_cap_work_off, MSF_BOOT_DIRECT, 1500) || dump_ram(0, 5, idle))
        return -1;
    CHECK(res[3] != 0U && idle[3] == res[3], "%d cycles without capture, %d with", idle[3],
          res[3]);
    CHECK(!(idle[4] & CS_RUN), "the engine runs with the arm bit clear (status %04x)", idle[4]);
    return 0;
}

/* test_capture.test_cap_overflow_flag: a burst of 8 MEM_W keeps port B busy
 * 8 cycles while the engine makes a word every 2: its 2-word queue
 * overflows, and the status says so */
static int test_cap_overflow_flag(void)
{
    uint16_t res[5];

    if (dbg_boot(&img_cap_ovf, MSF_BOOT_DIRECT, 600) || dump_ram(0, 5, res))
        return -1;
    CHECK(res[4] & CS_OVF, "status %04x after the burst", res[4]);
    return 0;
}

/* ======================================================================== */

typedef struct {
    const char *name;
    int (*run)(void);
} msf_test;

static const msf_test tests[] = {
    {"crc_check_values", test_crc_check_values},
    {"boot_parallel_go", test_boot_parallel_go},
    {"boot_bad_crc_then_retry", test_boot_bad_crc_then_retry},
    {"restart_direct_boot", test_restart_direct_boot},
    {"uart_tx_drift_free", test_uart_tx_drift_free},
    {"spi_all_modes_full_duplex", test_spi_all_modes_full_duplex},
    {"i2c_write_ack", test_i2c_write_ack},
    {"i2c_address_nack", test_i2c_address_nack},
    {"i2c_write_then_read_repeated_start", test_i2c_write_then_read_repeated_start},
    {"i2c_constrained_random_with_stretching", test_i2c_constrained_random_with_stretching},
    {"usb_ls_tx_packet", test_usb_ls_tx_packet},
    {"usb_ls_rx_packet", test_usb_ls_rx_packet},
    {"eth_10baset_frame_fcs_tpidl_nlp", test_eth_10baset_frame_fcs_tpidl_nlp},
    {"threads_blink_and_echo", test_threads_blink_and_echo},
    {"jtag_probe", test_jtag_probe},
    {"onewire_probe", test_onewire_probe},
    {"spi_target_regs", test_spi_target_regs},
    {"spi_flash", test_spi_flash},
    {"i2c_target_eeprom", test_i2c_target_eeprom},
    {"swd_probe", test_swd_probe},
    {"four_threads_fifo", test_four_threads_fifo},
    {"mon_uart_decodes_and_times", test_mon_uart_decodes_and_times},
    {"mon_uart_break", test_mon_uart_break},
    {"mon_uart_back_to_back", test_mon_uart_back_to_back},
    {"mon_i2c_transfers", test_mon_i2c_transfers},
    {"mon_i2c_random_legal", test_mon_i2c_random_legal},
    {"mon_i2c_protocol_errors", test_mon_i2c_protocol_errors},
    {"mon_i2c_single_faults", test_mon_i2c_single_faults},
    {"mon_i2c_fast_master", test_mon_i2c_fast_master},
    {"mon_i2c_long_low", test_mon_i2c_long_low},
    {"mon_spi_modes", test_mon_spi_modes},
    {"mon_spi_limits", test_mon_spi_limits},
    {"mon_spi_errors", test_mon_spi_errors},
    {"bridge_modes", test_bridge_modes},
    {"fw_logic_analyzer", test_fw_logic_analyzer},
    {"ps2_device_types_scancodes", test_ps2_device_types_scancodes},
    {"ps2_device_answers_commands", test_ps2_device_answers_commands},
    {"ps2_device_host_inhibit", test_ps2_device_host_inhibit},
    {"can_receive_and_ack", test_can_receive_and_ack},
    {"can_transmit", test_can_transmit},
    {"can_arbitration_and_no_ack", test_can_arbitration_and_no_ack},
    {"fault_uart_knobs", test_fault_uart_knobs},
    {"fault_uart_tolerance_sweep", test_fault_uart_tolerance_sweep},
    {"fault_i2c_knobs", test_fault_i2c_knobs},
    {"fault_spi_knobs", test_fault_spi_knobs},
    {"fw_ws2812", test_fw_ws2812},
    {"filter_removes_short_pulses", test_filter_removes_short_pulses},
    {"filter_every_pin", test_filter_every_pin},
    {"filter_delay", test_filter_delay},
    {"filter_uart_spikes", test_filter_uart_spikes},
    {"rx_manchester_frames_jitter", test_rx_manchester_frames_jitter},
    {"rx_manchester_loopback", test_rx_manchester_loopback},
    {"debug_view_reads_state", test_debug_view_reads_state},
    {"debug_view_pc_in_bank1", test_debug_view_pc_in_bank1},
    {"debug_mode_off_without_strap", test_debug_mode_off_without_strap},
    {"debug_single_step", test_debug_single_step},
    {"debug_view_page1", test_debug_view_page1},
    {"tmo_per_thread", test_tmo_per_thread},
    {"cap_sample_mode", test_cap_sample_mode},
    {"cap_divisor_sizes", test_cap_divisor_sizes},
    {"cap_pretrigger_ring", test_cap_pretrigger_ring},
    {"cap_edge_vs_level", test_cap_edge_vs_level},
    {"cap_change_mode", test_cap_change_mode},
    {"cap_ui_pins", test_cap_ui_pins},
    {"cap_never_changes_core_timing", test_cap_never_changes_core_timing},
    {"cap_overflow_flag", test_cap_overflow_flag},
};

#define NTESTS ((int)(sizeof tests / sizeof tests[0]))

int msf_tests_count(void)
{
    return NTESTS;
}

const char *msf_tests_name(int i)
{
    return (i >= 0 && i < NTESTS) ? tests[i].name : 0;
}

int msf_tests_run(const char *only)
{
    int i, ran = 0, failed = 0;
    uint32_t digest, steps;

    if (msf_link_init() != 0) {
        msf_printf("msf-hil: PS EMIO GPIO initialisation failed\r\n");
        return -1;
    }
    for (i = 0; i < NTESTS; ++i) {
        int rc;

        if (only && strcmp(only, tests[i].name) != 0)
            continue;
        msf_printf("msf-hil: %s\r\n", tests[i].name);
        msf_digest_reset();
        rc = tests[i].run();
        if (msf_link_fault()) {
            msf_printf("    FAIL: the PL never acknowledged a step request\r\n");
            rc = -1;
        }
        digest = msf_digest(&steps);
        msf_printf("msf-hil: digest %s %04x%04x over %d steps\r\n", tests[i].name,
                   (unsigned)(digest >> 16), (unsigned)(digest & 0xFFFFU), (int)steps);
        msf_printf("msf-hil: %s %s\r\n", rc ? "FAIL" : "PASS", tests[i].name);
        ++ran;
        if (rc)
            ++failed;
        if (msf_link_fault())
            break;
    }
    msf_reset(0, 0, 0);
    msf_release_pins();
    msf_printf("msf-hil: %d of %d tests passed\r\n", ran - failed, ran);
    return ran ? failed : -1;
}
