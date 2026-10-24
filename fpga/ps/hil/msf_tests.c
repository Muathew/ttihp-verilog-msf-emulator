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
#define TRACE_CAP  262144U

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
