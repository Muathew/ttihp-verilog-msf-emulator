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
#define TRACE_CAP  65536U

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

    CHECK(img_dump_ram.count <= 64U && addr <= 255U && count >= 1U && count <= 128U,
          "dump_ram: bad arguments");
    memcpy(code, img_dump_ram.words, img_dump_ram.count * sizeof code[0]);
    CHECK((code[0] & 0xff00U) == 0x0300U && (code[1] & 0xff00U) == 0x0400U,
          "dump_ram.s must start with LDR R3 / LDR R4");
    code[0] = (uint16_t)(0x0300U | addr);
    code[1] = (uint16_t)(0x0400U | count);
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

    if (msf_link_init() != 0) {
        msf_printf("msf-hil: PS EMIO GPIO initialisation failed\r\n");
        return -1;
    }
    for (i = 0; i < NTESTS; ++i) {
        int rc;

        if (only && strcmp(only, tests[i].name) != 0)
            continue;
        msf_printf("msf-hil: %s\r\n", tests[i].name);
        rc = tests[i].run();
        if (msf_link_fault()) {
            msf_printf("    FAIL: the PL never acknowledged a step request\r\n");
            rc = -1;
        }
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
