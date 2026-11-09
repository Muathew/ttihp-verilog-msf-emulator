/*
 * The v3 board test: the MCU is the line peer and the judge for what v3 added,
 * against firmware/mcu_v3_peer.s on the FPGA's core (12 MHz). A pass:
 *   1. calibration: on 'S' the MCU sends the calibration pattern on MOSI and the
 *      FPGA answers with T, the core cycles it took; from T the MCU places every
 *      line symbol on the FPGA's clock (v3_lines.h);
 *   2. five Ethernet frames through RX mode 3 at 5 core cycles a bit (10BASE-T's
 *      ratio at 50 MHz): 64 bytes at 0, +100 and -100 ppm, 1518 bytes at -100 ppm,
 *      and 64 bytes with one bit flipped, which must be refused;
 *   3. four USB packets through the NRZI receiver at 4 cycles a bit (full speed's
 *      ratio at 48 MHz): at 0, +2500 and -2500 ppm (USB allows 0.25 %), and one
 *      with a bit flipped;
 *   4. the late guard: POCI (UIO7) read with the MCU's pull-up and then pull-down
 *      in each of the peer's four states. Driven low reads 0 both ways; off the bus
 *      follows the pull. SCL (UIO2) is driven by the same thread but not named (not
 *      SDA: PA0 also drives the red LED through J18, which could hold a released
 *      line low and pass for driven).
 * Each line symbol's edges land on the nearest SPI bit (62.5 ns at 16 Mbit/s): at
 * random up to 31 ns from where they belong, which at 12 MHz is what 7.5 ns is at
 * 50 MHz (10BASE-T) or 7.8 ns at 48 MHz (USB).
 *
 * The SPI is the signal generator: MOSI only, 16-bit words fed by the CPU with
 * interrupts off. SCLK and CS are taken off their pins, so no 16 MHz clock runs
 * beside MOSI. Every stream is timed: one that took longer than 32 CPU cycles a
 * word had gaps (the CPU fell behind, or the SPI pauses between words), and its
 * result is reported as not valid instead of as a failure of the FPGA.
 */
#include "ti_msp_dl_config.h"

#include "protocol_test.h"
#include "v3_lines.h"
#include "v3_test.h"

/* Pins, by the names SysConfig generates from p_test.syscfg for SPI_0 and I2C_0
 * (checked against the SDK's templates, driverlib/.meta/spi/SPI.Board.h.xdt and
 * i2c/I2C.Board.h.xdt, 9 Nov 2026). */
#define V3_POCI_PORT   GPIO_SPI_0_POCI_PORT
#define V3_POCI_PIN    GPIO_SPI_0_POCI_PIN
#define V3_POCI_IOMUX  GPIO_SPI_0_IOMUX_POCI
#define V3_POCI_FUNC   GPIO_SPI_0_IOMUX_POCI_FUNC
#define V3_SCLK_IOMUX  GPIO_SPI_0_IOMUX_SCLK
#define V3_CS_IOMUX    GPIO_SPI_0_IOMUX_CS0
#define V3_SCL_PORT    GPIO_I2C_0_SCL_PORT
#define V3_SCL_PIN     GPIO_I2C_0_SCL_PIN
#define V3_SCL_IOMUX   GPIO_I2C_0_IOMUX_SCL

#define MS             (CPUCLK_FREQ / 1000U)
#define CYCLES_A_WORD  32U       /* 16 SPI bits at CPUCLK / 2 */
#define ETH_FRAMES     5U
#define USB_PACKETS    4U

static const struct { uint16_t len; int16_t ppm; uint8_t corrupt; } ethCase[ETH_FRAMES] = {
    {64, 0, 0}, {64, 100, 0}, {64, -100, 0}, {1518, -100, 0}, {64, 0, 1}
};
static const struct { int16_t ppm; uint8_t corrupt; } usbCase[USB_PACKETS] = {
    {0, 0}, {2500, 0}, {-2500, 0}, {0, 1}
};

typedef enum { R_PASS, R_FAIL, R_TIMEOUT, R_INVALID } Result;

/* UART1 bytes from the FPGA, filled by the interrupt */
static volatile uint8_t rxq[256];
static volatile uint8_t rxHead;
static uint8_t rxTail;
static volatile bool sawPrompt;

static uint16_t calWords[V3_CAL_WORDS];
static uint32_t calCount;
static uint16_t words[V3_MAX_WORDS];
static uint8_t frame[V3_ETH_MAX];
static uint8_t packet[V3_USB_BYTES];
static bool configured;
static uint32_t rng = 0x4D534633U;

/* What the pass saw, for the log */
static uint32_t passNumber;
static Result calRes;
static uint32_t calT, calStream, calWordsSent;
static Result ethRes[ETH_FRAMES], usbRes[USB_PACKETS], guardRes[4];
static uint8_t ethGot[ETH_FRAMES][3];         /* status, length */
static uint32_t ethStream[ETH_FRAMES], ethWords[ETH_FRAMES];
static uint8_t usbGot[USB_PACKETS][4];        /* status, count, SYNC, PID */
static uint32_t usbStream[USB_PACKETS], usbWords[USB_PACKETS];
static uint8_t guardUp[4], guardDown[4], guardScl[4];
static uint16_t guardLate[4];

static struct { uint32_t pass, fail, timeout, invalid; } totals[4];   /* cal, eth, usb, guard */

/* ---- time: SysTick counts CPU cycles down from 2^24 - 1; now() keeps a 32-bit
 * count, so it must run at least every 0.5 s while a wait depends on it ---- */
static uint32_t tickLast, tickNow;

static uint32_t now(void)
{
    uint32_t v = SysTick->VAL;

    tickNow += (tickLast - v) & 0x00FFFFFFU;
    tickLast = v;
    return tickNow;
}

static void waitCycles(uint32_t cycles)
{
    uint32_t t0 = now();

    while (now() - t0 < cycles) {
    }
}

/* ---- the prompt queue ---- */
void V3_Test_uartByte(uint8_t byte)
{
    static uint8_t prev;

    rxq[rxHead] = byte;
    rxHead++;
    /* Two 'S' running: the v3 peer asking again for calibration. The v2 peer only
     * echoes what it is sent, one different byte a pass, so it never does this. */
    if (byte == 'S' && prev == 'S') {
        sawPrompt = true;
    }
    prev = byte;
}

static void flush(void)
{
    rxTail = rxHead;
}

static int getByte(uint32_t timeoutCycles)
{
    uint32_t t0 = now();
    uint8_t b;

    while (rxTail == rxHead) {
        if (now() - t0 > timeoutCycles) {
            return -1;
        }
    }
    b = rxq[rxTail];
    rxTail++;
    return b;
}

/* Skip to the next byte equal to want: false if none came in time. */
static bool waitFor(uint8_t want, uint32_t timeoutCycles)
{
    uint32_t t0 = now();

    for (;;) {
        while (rxTail == rxHead) {
            if (now() - t0 > timeoutCycles) {
                return false;
            }
        }
        if (rxq[rxTail++] == want) {
            return true;
        }
    }
}

static bool getBytes(uint8_t *out, uint32_t n)
{
    uint32_t i;

    for (i = 0; i < n; i++) {
        int b = getByte(20U * MS);

        if (b < 0) {
            return false;
        }
        out[i] = (uint8_t) b;
    }
    return true;
}

/* ---- the SPI as a signal generator ---- */
static void configure(void)
{
    /* SCLK and CS off their pins: inputs, so no clock runs down the PMOD cable */
    DL_GPIO_initDigitalInput(V3_SCLK_IOMUX);
    DL_GPIO_initDigitalInput(V3_CS_IOMUX);
    /* SCL is only read from now on (the LaunchPad pulls it up) */
    DL_GPIO_initDigitalInput(V3_SCL_IOMUX);

    DL_SPI_disable(SPI_0_INST);
    DL_SPI_setFrameFormat(SPI_0_INST, DL_SPI_FRAME_FORMAT_MOTO4_POL0_PHA1);
    DL_SPI_setDataSize(SPI_0_INST, DL_SPI_DATA_SIZE_16);
    DL_SPI_setBitRateSerialClockDivider(SPI_0_INST, 0U);     /* CPUCLK / 2 = 16 MHz */
    DL_SPI_enable(SPI_0_INST);

    /* MOSI to its idle level, low, before the FPGA looks */
    {
        static const uint16_t idle[2] = {0, 0};
        uint32_t i;

        for (i = 0; i < 2U; i++) {
            while (DL_SPI_isTXFIFOFull(SPI_0_INST)) {
            }
            DL_SPI_transmitData16(SPI_0_INST, idle[i]);
        }
        while (DL_SPI_isBusy(SPI_0_INST)) {
        }
        while (!DL_SPI_isRXFIFOEmpty(SPI_0_INST)) {
            (void) DL_SPI_receiveData16(SPI_0_INST);
        }
    }
    calCount = v3_cal_words(calWords);
    configured = true;
}

/* Send n words with interrupts off; returns the CPU cycles it took. */
static uint32_t stream(const uint16_t *w, uint32_t n)
{
    uint32_t i, t0, t1;

    __disable_irq();
    t0 = SysTick->VAL;
    for (i = 0; i < n; i++) {
        while (DL_SPI_isTXFIFOFull(SPI_0_INST)) {
        }
        DL_SPI_transmitData16(SPI_0_INST, w[i]);
        if (!DL_SPI_isRXFIFOEmpty(SPI_0_INST)) {
            (void) DL_SPI_receiveData16(SPI_0_INST);
        }
    }
    while (DL_SPI_isBusy(SPI_0_INST)) {
    }
    t1 = SysTick->VAL;
    __enable_irq();
    while (!DL_SPI_isRXFIFOEmpty(SPI_0_INST)) {
        (void) DL_SPI_receiveData16(SPI_0_INST);
    }
    return (t0 - t1) & 0x00FFFFFFU;
}

/* A stream with gaps lasts longer than its words: 1 % and a few words of slack. */
static bool gapless(uint32_t cycles, uint32_t n)
{
    uint32_t want = n * CYCLES_A_WORD;

    return cycles <= want + want / 100U + 4U * CYCLES_A_WORD;
}

/* ---- the guard: read POCI with each pull, and SCL ---- */
static void readGuard(uint32_t k)
{
    DL_GPIO_initDigitalInputFeatures(V3_POCI_IOMUX, DL_GPIO_INVERSION_DISABLE,
        DL_GPIO_RESISTOR_PULL_UP, DL_GPIO_HYSTERESIS_DISABLE, DL_GPIO_WAKEUP_DISABLE);
    waitCycles(MS / 10U);
    guardUp[k] = DL_GPIO_readPins(V3_POCI_PORT, V3_POCI_PIN) ? 1U : 0U;
    DL_GPIO_initDigitalInputFeatures(V3_POCI_IOMUX, DL_GPIO_INVERSION_DISABLE,
        DL_GPIO_RESISTOR_PULL_DOWN, DL_GPIO_HYSTERESIS_DISABLE, DL_GPIO_WAKEUP_DISABLE);
    waitCycles(MS / 10U);
    guardDown[k] = DL_GPIO_readPins(V3_POCI_PORT, V3_POCI_PIN) ? 1U : 0U;
    guardScl[k] = DL_GPIO_readPins(V3_SCL_PORT, V3_SCL_PIN) ? 1U : 0U;
    DL_GPIO_initPeripheralInputFunction(V3_POCI_IOMUX, V3_POCI_FUNC);
}

/* ---- the pass ---- */
static Result runCal(void)
{
    uint8_t r[3];

    calT = 0;
    calWordsSent = calCount;
    calStream = stream(calWords, calCount);
    if (!waitFor('s', 50U * MS) || !getBytes(r, 2)) {
        return R_TIMEOUT;
    }
    calT = (uint32_t) r[0] | ((uint32_t) r[1] << 8);
    if (!gapless(calStream, calCount)) {
        return R_INVALID;
    }
    /*
     * One SPI bit is 2 CPU cycles, and T / 16384 core cycles at 12 MHz: so the CPU
     * clock is 24 MHz x 16384 / T. SYSOSC is 32 MHz within a few per cent; anything
     * else means the FPGA missed transitions or the SPI did not run as asked.
     */
    if (calT < 11800U || calT > 12800U) {
        return R_FAIL;
    }
    return R_PASS;
}

static Result runEth(uint32_t i)
{
    uint32_t n = ethCase[i].len;
    uint32_t sym = v3_symbol_fp(calT, V3_ETH_HALF_X2, ethCase[i].ppm);
    uint32_t len;

    v3_eth_frame(frame, n, &rng, ethCase[i].corrupt);
    ethWords[i] = v3_eth_words(frame, n, sym, v3_rand(&rng) & 0xFFFFU, words, V3_MAX_WORDS);
    ethGot[i][0] = ethGot[i][1] = ethGot[i][2] = 0;
    if (!waitFor('M', 400U * MS)) {
        return R_TIMEOUT;
    }
    if (ethWords[i] == 0U) {
        return R_INVALID;
    }
    ethStream[i] = stream(words, ethWords[i]);
    if (!waitFor('m', 50U * MS) || !getBytes(ethGot[i], 3)) {
        return R_TIMEOUT;
    }
    if (!gapless(ethStream[i], ethWords[i])) {
        return R_INVALID;
    }
    len = (uint32_t) ethGot[i][1] | ((uint32_t) ethGot[i][2] << 8);
    if (ethGot[i][0] != (ethCase[i].corrupt ? 0x82U : 0x81U) || len != n) {
        return R_FAIL;
    }
    return R_PASS;
}

static Result runUsb(uint32_t i)
{
    uint32_t sym = v3_symbol_fp(calT, V3_USB_BIT_X2, usbCase[i].ppm);

    v3_usb_packet(packet, &rng, usbCase[i].corrupt);
    usbWords[i] = v3_usb_words(packet, sym, v3_rand(&rng) & 0xFFFFU, words, V3_MAX_WORDS);
    usbGot[i][0] = usbGot[i][1] = usbGot[i][2] = usbGot[i][3] = 0;
    if (!waitFor('U', 400U * MS)) {
        return R_TIMEOUT;
    }
    if (usbWords[i] == 0U) {
        return R_INVALID;
    }
    usbStream[i] = stream(words, usbWords[i]);
    if (!waitFor('u', 50U * MS) || !getBytes(usbGot[i], 4)) {
        return R_TIMEOUT;
    }
    if (!gapless(usbStream[i], usbWords[i])) {
        return R_INVALID;
    }
    if (usbGot[i][0] != (usbCase[i].corrupt ? 0x82U : 0x81U) || usbGot[i][1] != V3_USB_BYTES ||
        usbGot[i][2] != 0x80U || usbGot[i][3] != V3_USB_PID) {
        return R_FAIL;
    }
    return R_PASS;
}

static Result runGuard(uint32_t k)
{
    static const uint8_t prompt[4] = {'A', 'B', 'C', 'D'};
    static const uint16_t late[4] = {0, 0x8011, 0x8010, 0x8021};
    uint8_t r[2];

    guardLate[k] = 0;
    guardUp[k] = guardDown[k] = guardScl[k] = 0xFF;
    if (!waitFor(prompt[k], 400U * MS)) {
        return R_TIMEOUT;
    }
    if (k > 0U) {
        if (!getBytes(r, 2)) {
            return R_TIMEOUT;
        }
        guardLate[k] = (uint16_t) (r[0] | (r[1] << 8));
    }
    readGuard(k);
    if (guardScl[k] != 0U || guardDown[k] != 0U || guardLate[k] != late[k]) {
        return R_FAIL;
    }
    /* off the bus in state B only: there the pull-up wins */
    return (guardUp[k] == (k == 1U ? 1U : 0U)) ? R_PASS : R_FAIL;
}

static void count(uint32_t which, Result r)
{
    if (r == R_PASS) {
        totals[which].pass++;
    } else if (r == R_FAIL) {
        totals[which].fail++;
    } else if (r == R_TIMEOUT) {
        totals[which].timeout++;
    } else {
        totals[which].invalid++;
    }
}

static void logResult(Result r)
{
    Log_str(r == R_PASS ? " -> PASS\r\n" : r == R_FAIL ? " -> FAIL\r\n" :
            r == R_TIMEOUT ? " -> TIMEOUT\r\n" : " -> NOT VALID (the stream had gaps)\r\n");
}

static void logPpm(int32_t ppm)
{
    Log_str(ppm < 0 ? " -" : " +");
    Log_dec((uint32_t) (ppm < 0 ? -ppm : ppm));
    Log_str(" ppm");
}

static void logStream(uint32_t cycles, uint32_t n)
{
    Log_str(" stream ");
    Log_dec(cycles);
    Log_str("/");
    Log_dec(n * CYCLES_A_WORD);
    Log_str(" cycles");
}

static void logTotals(const char *name, uint32_t which)
{
    Log_str(name);
    Log_str(" pass=");
    Log_dec(totals[which].pass);
    Log_str(" fail=");
    Log_dec(totals[which].fail);
    Log_str(" timeout=");
    Log_dec(totals[which].timeout);
    Log_str(" invalid=");
    Log_dec(totals[which].invalid);
}

static void logPass(void)
{
    uint32_t i;

    Log_str("MCU ---- v3 pass ");
    Log_dec(passNumber);
    Log_str(" ----\r\nMCU cal  T=");
    Log_dec(calT);
    if (calT != 0U) {
        /* the CPU clock, from T: 24 MHz x 16384 / T, in kHz */
        Log_str(" (MCU clock ");
        Log_dec((uint32_t) ((24000ULL * 16384U + calT / 2U) / calT));
        Log_str(" kHz against the FPGA's 12 MHz)");
    }
    logStream(calStream, calWordsSent);
    logResult(calRes);
    for (i = 0; i < ETH_FRAMES; i++) {
        Log_str("MCU eth  ");
        Log_dec(i);
        Log_str(": ");
        Log_dec(ethCase[i].len);
        Log_str(" bytes");
        logPpm(ethCase[i].ppm);
        Log_str(ethCase[i].corrupt ? ", a bit flipped: want 82" : ": want 81");
        Log_str(", got ");
        Log_hex8(ethGot[i][0]);
        Log_str(" length ");
        Log_dec((uint32_t) ethGot[i][1] | ((uint32_t) ethGot[i][2] << 8));
        logStream(ethStream[i], ethWords[i]);
        logResult(ethRes[i]);
    }
    for (i = 0; i < USB_PACKETS; i++) {
        Log_str("MCU usb  ");
        Log_dec(i);
        Log_str(":");
        logPpm(usbCase[i].ppm);
        Log_str(usbCase[i].corrupt ? ", a bit flipped: want 82" : ": want 81");
        Log_str(", got ");
        Log_hex8(usbGot[i][0]);
        Log_str(" bytes ");
        Log_dec(usbGot[i][1]);
        Log_str(" SYNC ");
        Log_hex8(usbGot[i][2]);
        Log_str(" PID ");
        Log_hex8(usbGot[i][3]);
        logStream(usbStream[i], usbWords[i]);
        logResult(usbRes[i]);
    }
    for (i = 0; i < 4U; i++) {
        static const char *const what[4] = {
            "A driven:              want up=0 down=0 SCL=0          ",
            "B T0 late, guarded:    want up=1 down=0 SCL=0 LATE=8011",
            "C flag cleared:        want up=0 down=0 SCL=0 LATE=8010",
            "D late, T1 guarded:    want up=0 down=0 SCL=0 LATE=8021"
        };
        Log_str("MCU guard ");
        Log_str(what[i]);
        Log_str(", got up=");
        Log_dec(guardUp[i]);
        Log_str(" down=");
        Log_dec(guardDown[i]);
        Log_str(" SCL=");
        Log_dec(guardScl[i]);
        if (i > 0U) {
            Log_str(" LATE=");
            Log_hex8((uint8_t) (guardLate[i] >> 8));
            Log_hex8((uint8_t) guardLate[i]);
        }
        logResult(guardRes[i]);
    }
    Log_str("MCU v3 totals:");
    logTotals(" cal", 0);
    logTotals(" | eth", 1);
    logTotals(" | usb", 2);
    logTotals(" | guard", 3);
    Log_str("\r\n");
}

void V3_Test_init(void)
{
    SysTick->LOAD = 0x00FFFFFFU;
    SysTick->VAL = 0U;
    SysTick->CTRL = SysTick_CTRL_CLKSOURCE_Msk | SysTick_CTRL_ENABLE_Msk;
    tickLast = SysTick->VAL;
    rxHead = 0;
    rxTail = 0;
    passNumber = 0;
    /* The v3 peer prompts every 0.2 s while it waits: two prompts within 0.6 s. */
    waitCycles(600U * MS);
}

bool V3_Test_peerIsV3(void)
{
    return sawPrompt;
}

void V3_Test_runPass(void)
{
    uint32_t i;

    if (!configured) {
        configure();
        Log_str("\r\nMCU ==== v3 board test (firmware/mcu_v3_peer.s), built " __DATE__ " " __TIME__
                " ====\r\n");
    }
    passNumber++;
    flush();
    if (!waitFor('S', 1000U * MS)) {
        Log_str("MCU v3: no 'S' from the FPGA for 1 s\r\n");
        return;
    }
    calRes = runCal();
    count(0, calRes);
    for (i = 0; i < ETH_FRAMES; i++) {
        ethRes[i] = runEth(i);
        count(1, ethRes[i]);
    }
    for (i = 0; i < USB_PACKETS; i++) {
        usbRes[i] = runUsb(i);
        count(2, usbRes[i]);
    }
    for (i = 0; i < 4U; i++) {
        guardRes[i] = runGuard(i);
        count(3, guardRes[i]);
    }
    (void) waitFor('P', 200U * MS);
    logPass();
}
