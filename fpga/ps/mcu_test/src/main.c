// SPDX-License-Identifier: Apache-2.0
// Vitis standalone app: load the MCU protocol-test peer into msfcore, connect
// all eight UIO pins to PMOD JA, and print everything the peer reports.
//
// The peer (firmware/mcu_protocol_target.s, which documents the format) sends
// events on uo_out and cannot wait for us, while a line on the console blocks
// for milliseconds. So events are only collected while traffic is arriving, and
// printed at the first firmware heartbeat (about 0.2 s apart) with no traffic
// since the one before. The heartbeat also says how many events the peer sent,
// so anything missed while printing is reported, not silently lost.
#include <stdint.h>

#include "firmware_image.h"
#include "msf_link.h"
#include "msf_log.h"

/* uo_out while the peer runs */
#define UO_STROBE   0x80U
#define UO_FIRST    0x40U
#define UO_PAYLOAD  0x3FU

/* Event codes and EV_BUS values: firmware/mcu_protocol_target.s */
enum {
    EV_SENT, EV_UART, EV_SPI_OK, EV_SPI_BAD, EV_SPI_EXP, EV_SPI_RST,
    EV_I2C_ADR, EV_I2C_ADX, EV_I2C_PTR, EV_I2C_WR, EV_I2C_RD, EV_I2C_RDA,
    EV_BUS, EV_LA, EV_PINS, EV_ACT
};
#define BUS_STOP      0
#define BUS_RSTART    1
#define BUS_WATCHDOG  2
#define BUS_BOOT      0x80

#define PIN_RX    0x01U
#define PIN_SCL   0x04U
#define PIN_SDA   0x08U
#define PIN_CS    0x20U

#define QUEUE_WORDS    256U
#define LA_BYTES       1024U        /* 509 words of 2 bytes in the firmware */
#define LA_DIV_CYCLES  4U           /* LA_DIV in the firmware */
#define CORE_MHZ       12U
#define BEATS_ALIVE    50U          /* pins printed at least this often (10 s) */
#define SILENT_POLLS   20000000U    /* some seconds with nothing on uo_out */
#define WD_PRINT_FIRST 3            /* watchdog events printed before thinning out */

static const char *const pin_names[8] = {
    "RX", "TX", "SCL", "SDA", "SCK", "CS", "MOSI", "MISO"
};

/* Decoder state */
static uint8_t raw, stable;         /* last read; last value read twice running */
static int have_first, seen_event;
static uint8_t first_group;
static uint32_t framing_errors;

/* Collected, not yet printed */
static uint16_t queue[QUEUE_WORDS];
static uint32_t queue_n, queue_overflow;
static uint8_t la[LA_BYTES];
static uint32_t la_n;
static int traffic_since_beat, traffic_queued;

/* Heartbeat */
static uint8_t beat_pins, beat_moved, moved_since_print, printed_pins;
static uint8_t events_received;     /* compared with EV_SENT, modulo 256 */
static uint32_t beats_since_print;
static int printed_any;

/* Totals since the app started */
static int burst, spi_index, spi_bad_byte;
static uint32_t n_uart, n_spi_ok, n_spi_bad, n_spi_rst, n_i2c_acked, n_i2c_other,
                n_i2c_ptr, n_i2c_wr, n_i2c_rd, n_i2c_rda, n_watchdog, n_lost;

static void spi_line(int data, const char *verdict)
{
    msf_printf("FPGA SPI  byte %d: MOSI 0x%02x %s\r\n", spi_index, data, verdict);
}

static void print_event(uint16_t word)
{
    int code = word >> 8;
    int data = word & 0xFF;

    switch (code) {
    case EV_UART:
        ++n_uart;
        msf_printf("FPGA UART got 0x%02x, echoed it\r\n", data);
        break;
    case EV_SPI_OK:
        ++n_spi_ok;
        spi_line(data, "ok");
        spi_index = (spi_index + 1) & 7;
        break;
    case EV_SPI_BAD:
        spi_bad_byte = data;        /* EV_SPI_EXP says what was expected */
        break;
    case EV_SPI_EXP:
        ++n_spi_bad;
        msf_printf("FPGA SPI  byte %d: MOSI 0x%02x MISMATCH, expected 0x%02x\r\n",
                   spi_index, spi_bad_byte, data);
        spi_index = (spi_index + 1) & 7;
        break;
    case EV_SPI_RST:
        ++n_spi_rst;
        msf_printf("FPGA SPI  went quiet at byte %d with %d bits to go: next frame is byte 0\r\n",
                   data & 0x0F, data >> 4);
        spi_index = 0;
        break;
    case EV_I2C_ADR:
        ++n_i2c_acked;
        msf_printf("FPGA I2C  address byte 0x%02x (%s 0x%02x): ACK\r\n", data,
                   (data & 1) ? "read" : "write", data >> 1);
        break;
    case EV_I2C_ADX:
        ++n_i2c_other;
        msf_printf("FPGA I2C  address byte 0x%02x (%s 0x%02x): not ours, no ACK\r\n",
                   data, (data & 1) ? "read" : "write", data >> 1);
        break;
    case EV_I2C_PTR:
        ++n_i2c_ptr;
        msf_printf("FPGA I2C  pointer 0x%02x: ACK\r\n", data);
        break;
    case EV_I2C_WR:
        ++n_i2c_wr;
        msf_printf("FPGA I2C  wrote 0x%02x: ACK\r\n", data);
        break;
    case EV_I2C_RD:
        ++n_i2c_rd;
        msf_printf("FPGA I2C  sent 0x%02x, controller NACK (the normal last byte)\r\n", data);
        break;
    case EV_I2C_RDA:
        ++n_i2c_rda;
        msf_printf("FPGA I2C  sent 0x%02x, controller ACK (it wants another byte)\r\n", data);
        break;
    case EV_BUS:
        if (data == BUS_STOP) {
            msf_printf("FPGA I2C  STOP\r\n");
        } else if (data == BUS_RSTART) {
            msf_printf("FPGA I2C  repeated START\r\n");
        } else if (data == BUS_WATCHDOG) {
            if (++n_watchdog <= WD_PRINT_FIRST)
                msf_printf("FPGA I2C  WATCHDOG: a line stayed low with no clock; released the bus\r\n");
        } else if (data & BUS_BOOT) {
            msf_printf("FPGA peer firmware v%d is running\r\n", data & 0x7F);
        }
        break;
    default:
        break;
    }
}

static void print_capture(void)
{
    uint32_t words = la_n / 2U, i, ticks = 0U;
    int last = -1;

    msf_printf("FPGA logic analyser: the first MCU pass at the FPGA's pins, 1 = high, a line per change\r\n");
    msf_printf("LA     t_us RX TX SCL SDA SCK CS MOSI MISO\r\n");
    for (i = 0U; i < words; ++i) {
        int pins = la[2U * i];

        if (i != 0U)
            ticks += la[2U * i + 1U];
        if (pins != last) {
            uint32_t tenths = ticks * LA_DIV_CYCLES * 10U / CORE_MHZ;

            last = pins;
            msf_printf("LA %6d.%d  %d  %d   %d   %d   %d  %d    %d    %d\r\n",
                       (int)(tenths / 10U), (int)(tenths % 10U),
                       pins & 1, (pins >> 1) & 1, (pins >> 2) & 1, (pins >> 3) & 1,
                       (pins >> 4) & 1, (pins >> 5) & 1, (pins >> 6) & 1, (pins >> 7) & 1);
        }
    }
    msf_printf("FPGA logic analyser: end, %d words\r\n", (int)words);
}

static void print_queue(void)
{
    uint32_t i;

    if (traffic_queued)
        msf_printf("FPGA ---- burst %d: %d events ----\r\n", ++burst, (int)queue_n);
    for (i = 0U; i < queue_n; ++i)
        print_event(queue[i]);
    if (queue_overflow != 0U)
        msf_printf("FPGA WARNING: %d events did not fit the print queue\r\n",
                   (int)queue_overflow);
    if (traffic_queued)
        msf_printf("FPGA totals: UART %d | SPI ok %d bad %d quiet-resets %d | I2C acked %d other %d ptr %d wr %d read-nack %d read-ack %d | watchdog %d lost %d framing %d\r\n",
                   (int)n_uart, (int)n_spi_ok, (int)n_spi_bad, (int)n_spi_rst,
                   (int)n_i2c_acked, (int)n_i2c_other, (int)n_i2c_ptr, (int)n_i2c_wr,
                   (int)n_i2c_rd, (int)n_i2c_rda, (int)n_watchdog, (int)n_lost,
                   (int)framing_errors);
    if (la_n != 0U)
        print_capture();
    queue_n = 0U;
    queue_overflow = 0U;
    la_n = 0U;
    traffic_queued = 0;
}

static void print_pins(void)
{
    int i;

    msf_printf("FPGA pins RX=%d TX=%d SCL=%d SDA=%d SCK=%d CS=%d MOSI=%d MISO=%d moved:",
               beat_pins & 1, (beat_pins >> 1) & 1, (beat_pins >> 2) & 1,
               (beat_pins >> 3) & 1, (beat_pins >> 4) & 1, (beat_pins >> 5) & 1,
               (beat_pins >> 6) & 1, (beat_pins >> 7) & 1);
    if (moved_since_print == 0U)
        msf_printf(" none");
    for (i = 0; i < 8; ++i)
        if (moved_since_print & (1U << i))
            msf_printf(" %s", pin_names[i]);
    msf_printf("\r\n");
    if (moved_since_print == 0U) {  /* what idle should look like */
        if ((beat_pins & (PIN_SCL | PIN_SDA)) != (PIN_SCL | PIN_SDA))
            msf_printf("FPGA hint: I2C line low at idle: pull-ups, wiring, or the MCU stopped mid-transfer\r\n");
        if ((beat_pins & PIN_RX) == 0U)
            msf_printf("FPGA hint: UART RX low at idle: MCU TX should idle high (wiring, ground, MCU running?)\r\n");
        if ((beat_pins & PIN_CS) == 0U)
            msf_printf("FPGA hint: SPI CS low at idle: MCU CS should idle high (wiring, ground, MCU running?)\r\n");
    }
}

static void on_heartbeat(uint8_t sent)
{
    uint8_t lost = (uint8_t)(sent - events_received);

    moved_since_print |= beat_moved;
    if ((!traffic_since_beat && (queue_n != 0U || la_n != 0U)) ||
        queue_n >= QUEUE_WORDS / 2U)
        print_queue();
    traffic_since_beat = 0;
    if (lost != 0U) {
        n_lost += lost;
        events_received = sent;
        msf_printf("FPGA WARNING: missed %d events while printing\r\n", (int)lost);
    }
    if (queue_n != 0U || la_n != 0U)
        return;                     /* the pins line comes after what it describes */
    if (!printed_any || moved_since_print != 0U || beat_pins != printed_pins ||
        ++beats_since_print >= BEATS_ALIVE) {
        print_pins();
        printed_any = 1;
        printed_pins = beat_pins;
        moved_since_print = 0U;
        beats_since_print = 0U;
    }
}

static void on_event(uint16_t word)
{
    int code = word >> 8;
    uint8_t data = (uint8_t)word;

    seen_event = 1;
    switch (code) {
    case EV_PINS:
        beat_pins = data;
        return;
    case EV_ACT:
        beat_moved = data;
        return;
    case EV_SENT:
        on_heartbeat(data);
        return;
    case EV_LA:
        if (la_n < LA_BYTES)
            la[la_n++] = data;
        traffic_since_beat = 1;
        return;
    case EV_BUS:
        /* sent directly by T0, so not in EV_SENT's count */
        if (data != BUS_WATCHDOG && (data & BUS_BOOT) == 0U) {
            ++events_received;
            traffic_since_beat = 1;
            traffic_queued = 1;
        }
        break;
    default:
        ++events_received;
        traffic_since_beat = 1;
        traffic_queued = 1;
        break;
    }
    if (queue_n < QUEUE_WORDS)
        queue[queue_n++] = word;
    else
        ++queue_overflow;
}

/* One read of uo_out. The bits of one change can cross into the PS at slightly
 * different times, so only a value read twice running counts. */
static void on_sample(uint8_t uo)
{
    uint8_t changed;

    if (uo != raw) {
        raw = uo;
        return;
    }
    changed = (uint8_t)(uo ^ stable);
    stable = uo;
    if ((changed & UO_STROBE) == 0U)
        return;                     /* no new group */
    if (uo & UO_FIRST) {
        if (have_first)
            ++framing_errors;       /* its second group never came */
        first_group = uo & UO_PAYLOAD;
        have_first = 1;
    } else if (have_first) {
        have_first = 0;
        on_event((uint16_t)((uint16_t)first_group << 6 | (uo & UO_PAYLOAD)));
    } else if (seen_event) {
        ++framing_errors;           /* its first group was missed */
    }
}

static int load_peer(void)
{
    int rc;

    if (FIRMWARE_LOAD_ADDRESS + FIRMWARE_WORD_COUNT > 1024U) {
        msf_printf("Firmware image exceeds the 1024-word address space.\r\n");
        return 1;
    }
    if (msf_link_init() != 0) {
        msf_printf("PS EMIO GPIO initialization failed.\r\n");
        return 1;
    }
    rc = msf_boot_enter(0);
    if (rc != MSF_OK) {
        msf_printf("Core reset/status did not initialize (%d).\r\n", rc);
        return 1;
    }
    msf_printf("Loading MCU UART/I2C/SPI peer (%d words), app built %s %s.\r\n",
               (int)FIRMWARE_WORD_COUNT, __DATE__, __TIME__);
    rc = msf_boot_write(FIRMWARE_LOAD_ADDRESS, firmware_words, FIRMWARE_WORD_COUNT);
    if (rc != MSF_OK) {
        msf_printf(rc == MSF_ERR_NAK ? "Firmware write frame was rejected.\r\n"
                                     : "Firmware write frame timed out.\r\n");
        return 1;
    }
    rc = msf_boot_go(FIRMWARE_LOAD_ADDRESS);
    if (rc != MSF_OK) {
        msf_printf(rc == MSF_ERR_NAK ? "Firmware start frame was rejected.\r\n"
                                     : "Firmware start frame failed.\r\n");
        return 1;
    }
    msf_release_pins();
    return 0;
}

int main(void)
{
    uint32_t silent = 0U;
    msf_pins pins;

    if (load_peer() != 0)
        return 1;
    pins = msf_sample();
    raw = stable = pins.uo;
    msf_printf("PMOD JA now owns UIO[0:7]; run the MCU tests. Events follow, in bursts.\r\n");
    for (;;) {
        pins = msf_sample();
        if (pins.uo != stable)
            silent = 0U;
        else if (++silent == SILENT_POLLS)
            msf_printf("FPGA WARNING: nothing from the peer: uo=0x%02x uio_out=0x%02x uio_oe=0x%02x\r\n",
                       (int)pins.uo, (int)pins.out, (int)pins.oe);
        on_sample(pins.uo);
    }
}
