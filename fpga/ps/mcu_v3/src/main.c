// SPDX-License-Identifier: Apache-2.0
// Vitis standalone app: load the v3 board peer (firmware/mcu_v3_peer.s) into
// msfcore, give PMOD JA to the core, and print what the peer tells the MCU.
//
// The MCU (mcu_ext/protocol_tests, v3_test.c) is the judge: it knows what it sent
// and checks every verdict. This side shows the same bytes as the FPGA sent them,
// a pass at a time, so a disagreement between the two logs points at the wire.
// The peer copies every UART byte to uo_out as an event in mcu_protocol_target.s's
// format (code 1); a pass ends with 'P', and its summary is printed then. While a
// line prints (milliseconds) events can be missed; the next pass is not affected,
// and the count of framing errors says if it happened.
#include <stdint.h>

#include "firmware_image.h"
#include "msf_link.h"
#include "msf_log.h"

#define UO_STROBE    0x80U
#define UO_FIRST     0x40U
#define UO_PAYLOAD   0x3FU
#define EV_BYTE      1
#define SILENT_POLLS 20000000U    /* some seconds with nothing on uo_out */
#define MAX_ETH      8
#define MAX_USB      8

static uint8_t raw, stable;
static int have_first, seen_event;
static uint8_t first_group;
static uint32_t framing_errors, bad_codes, passes, stray;

/* The pass so far: each prompt is followed by a fixed number of data bytes
 * (firmware/mcu_v3_peer.s), so a data byte is never taken for a prompt. */
static int cur, need, got;
static uint8_t data_bytes[4];
static int cal_seen, n_eth, n_usb, guard_a;
static uint32_t cal_t;
static uint8_t eth[MAX_ETH][3], usb[MAX_USB][4];
static int late_seen[3];
static uint16_t late[3];

static void clear_pass(void)
{
    cal_seen = n_eth = n_usb = guard_a = 0;
    late_seen[0] = late_seen[1] = late_seen[2] = 0;
}

static void print_pass(void)
{
    int i;

    msf_printf("FPGA ---- v3 pass %d: what the FPGA sent the MCU ----\r\n", (int)++passes);
    if (cal_seen && cal_t != 0U)
        msf_printf("FPGA cal  T=%d cycles: MCU clock %d kHz\r\n", (int)cal_t,
                   (int)((24000U * 16384U + cal_t / 2U) / cal_t));
    else if (cal_seen)
        msf_printf("FPGA cal  T=0: the calibration pattern did not all arrive\r\n");
    for (i = 0; i < n_eth; ++i)
        msf_printf("FPGA eth  %d: status %02x, %d bytes after the SFD\r\n", i, eth[i][0],
                   eth[i][1] | eth[i][2] << 8);
    for (i = 0; i < n_usb; ++i)
        msf_printf("FPGA usb  %d: status %02x, %d bytes, SYNC %02x PID %02x\r\n", i,
                   usb[i][0], usb[i][1], usb[i][2], usb[i][3]);
    if (guard_a)
        msf_printf("FPGA guard A: UIO7 and UIO2 driven low, the guard names T0 and UIO7\r\n");
    for (i = 0; i < 3; ++i) {
        static const char *const what[3] = {
            "T0 late: UIO7 off the bus", "flag cleared: UIO7 back",
            "the guard names T1, T0 late: UIO7 stays"
        };
        if (late_seen[i])
            msf_printf("FPGA guard %c: %s, LATE_RD %04x\r\n", 'B' + i, what[i], late[i]);
    }
    msf_printf("FPGA totals: passes %d | framing %d | other events %d | stray bytes %d\r\n",
               (int)passes, (int)framing_errors, (int)bad_codes, (int)stray);
    clear_pass();
}

/* A prompt's data bytes are all in: keep them. */
static void complete(void)
{
    switch (cur) {
    case 's':
        cal_seen = 1;
        cal_t = (uint32_t)data_bytes[0] | (uint32_t)data_bytes[1] << 8;
        break;
    case 'm':
        if (n_eth < MAX_ETH) {
            eth[n_eth][0] = data_bytes[0];
            eth[n_eth][1] = data_bytes[1];
            eth[n_eth][2] = data_bytes[2];
            ++n_eth;
        }
        break;
    case 'u':
        if (n_usb < MAX_USB) {
            usb[n_usb][0] = data_bytes[0];
            usb[n_usb][1] = data_bytes[1];
            usb[n_usb][2] = data_bytes[2];
            usb[n_usb][3] = data_bytes[3];
            ++n_usb;
        }
        break;
    default:                        /* 'B' 'C' 'D': LATE_RD, low byte first */
        late_seen[cur - 'B'] = 1;
        late[cur - 'B'] = (uint16_t)(data_bytes[0] | data_bytes[1] << 8);
        break;
    }
}

static void on_event(uint16_t word)
{
    uint8_t b = (uint8_t)word;

    seen_event = 1;
    if ((word >> 8) != EV_BYTE) {
        ++bad_codes;
        return;
    }
    if (need > 0) {
        data_bytes[got++] = b;
        if (--need == 0)
            complete();
        return;
    }
    cur = b;
    got = 0;
    switch (b) {
    case 'S':                       /* calibration asked for (again, if no MCU yet) */
        clear_pass();
        break;
    case 's': need = 2; break;
    case 'm': need = 3; break;
    case 'u': need = 4; break;
    case 'B': case 'C': case 'D': need = 2; break;
    case 'A': guard_a = 1; break;
    case 'M': case 'U': break;
    case 'P': print_pass(); break;
    default: ++stray; break;
    }
}

/* As fpga/ps/mcu_test: only a value read twice running counts. */
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
        return;
    if (uo & UO_FIRST) {
        if (have_first)
            ++framing_errors;
        first_group = uo & UO_PAYLOAD;
        have_first = 1;
    } else if (have_first) {
        have_first = 0;
        on_event((uint16_t)((uint16_t)first_group << 6 | (uo & UO_PAYLOAD)));
    } else if (seen_event) {
        ++framing_errors;
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
    msf_printf("Loading the v3 board peer (%d words), app built %s %s.\r\n",
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
    msf_printf("PMOD JA now owns UIO[0:7]; start the MCU. A summary follows each pass.\r\n");
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
