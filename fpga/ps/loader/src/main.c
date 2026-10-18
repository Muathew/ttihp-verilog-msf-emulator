// SPDX-License-Identifier: Apache-2.0
#include "firmware_image.h"
#include "xgpio.h"
#include "xparameters.h"
#include "xstatus.h"
#include "xil_printf.h"

#include <stdint.h>

#define GPIO_DEVICE_ID XPAR_AXI_GPIO_0_DEVICE_ID
#define GPIO_CONTROL_CHANNEL 1U
#define GPIO_STATUS_CHANNEL 2U

#define UI_MASK 0x000000ffU
#define PS_OWNS_PINS (1U << 8)
#define CORE_RESET_RELEASE (1U << 9)
#define PARALLEL_STROBE (1U << 10)

#define STATUS_FRAME_OK (1U << 1)
#define STATUS_FRAME_ERROR (1U << 2)
#define STATUS_STARTING_FIRMWARE (1U << 3)
#define STATUS_PARALLEL_ACK (1U << 7)

#define BOOT_PARALLEL_MODE 0x02U
#define BOOT_POLL_LIMIT 100000000U
#define MAX_FIRMWARE_WORDS 384U

static XGpio gpio;
static uint32_t gpio_control;
static uint8_t next_strobe = 1U;

static uint8_t crc8_update(uint8_t crc, uint8_t byte)
{
    unsigned bit;

    crc ^= byte;
    for (bit = 0; bit < 8U; ++bit) {
        crc = (crc & 0x80U) ? (uint8_t)((crc << 1) ^ 0x07U)
                            : (uint8_t)(crc << 1);
    }
    return crc;
}

static uint32_t gpio_status(void)
{
    return XGpio_DiscreteRead(&gpio, GPIO_STATUS_CHANNEL);
}

static int wait_status(uint32_t mask, uint32_t expected)
{
    uint32_t poll;

    for (poll = 0; poll < BOOT_POLL_LIMIT; ++poll) {
        if ((gpio_status() & mask) == expected)
            return 0;
    }
    return -1;
}

static int wait_frame_result(void)
{
    uint32_t poll;

    for (poll = 0; poll < BOOT_POLL_LIMIT; ++poll) {
        uint32_t status = gpio_status();
        if ((status & STATUS_FRAME_ERROR) != 0U)
            return -2;
        if ((status & STATUS_FRAME_OK) != 0U)
            return 0;
    }
    return -1;
}

static int send_parallel_byte(uint8_t byte)
{
    uint32_t poll;

    gpio_control = (gpio_control & ~UI_MASK) | byte;
    XGpio_DiscreteWrite(&gpio, GPIO_CONTROL_CHANNEL, gpio_control);

    /* The synchronized input bus must see the data before its strobe edge. */
    for (volatile uint32_t settle = 0; settle < 4096U; ++settle) {
    }

    gpio_control &= ~PARALLEL_STROBE;
    if (next_strobe)
        gpio_control |= PARALLEL_STROBE;
    XGpio_DiscreteWrite(&gpio, GPIO_CONTROL_CHANNEL, gpio_control);

    for (poll = 0; poll < BOOT_POLL_LIMIT; ++poll) {
        uint32_t status = gpio_status();
        if (((status & STATUS_PARALLEL_ACK) != 0U) == (next_strobe != 0U)) {
            next_strobe ^= 1U;
            return 0;
        }
    }
    return -1;
}

static int send_frame(uint8_t command, uint16_t address,
                      const uint16_t *words, uint16_t count)
{
    uint8_t frame[7U + 2U * MAX_FIRMWARE_WORDS];
    uint32_t length = 6U + 2U * count;
    uint32_t i;
    uint8_t crc = 0U;

    if (count > MAX_FIRMWARE_WORDS || length + 1U > sizeof(frame))
        return -1;

    frame[0] = 0xa5U;
    frame[1] = command;
    frame[2] = (uint8_t)address;
    frame[3] = (uint8_t)(address >> 8);
    frame[4] = (uint8_t)count;
    frame[5] = (uint8_t)(count >> 8);
    for (i = 0; i < count; ++i) {
        frame[6U + 2U * i] = (uint8_t)words[i];
        frame[7U + 2U * i] = (uint8_t)(words[i] >> 8);
    }
    for (i = 1U; i < length; ++i)
        crc = crc8_update(crc, frame[i]);
    frame[length] = crc;

    for (i = 0; i <= length; ++i) {
        if (send_parallel_byte(frame[i]) != 0)
            return -1;
    }
    return wait_frame_result();
}

int main(void)
{
    int status;

    if ((FIRMWARE_LOAD_ADDRESS + FIRMWARE_WORD_COUNT) > 512U ||
        FIRMWARE_WORD_COUNT > MAX_FIRMWARE_WORDS) {
        xil_printf("Firmware image exceeds the supported loader range.\r\n");
        return XST_FAILURE;
    }

    status = XGpio_Initialize(&gpio, GPIO_DEVICE_ID);
    if (status != XST_SUCCESS) {
        xil_printf("AXI GPIO initialization failed: %d\r\n", status);
        return XST_FAILURE;
    }

    XGpio_SetDataDirection(&gpio, GPIO_CONTROL_CHANNEL, 0x7ffU);
    XGpio_SetDataDirection(&gpio, GPIO_STATUS_CHANNEL, 0xffU);

    /* Select parallel boot and claim the PMOD before releasing core reset. */
    gpio_control = BOOT_PARALLEL_MODE | PS_OWNS_PINS;
    XGpio_DiscreteWrite(&gpio, GPIO_CONTROL_CHANNEL, gpio_control);
    XGpio_SetDataDirection(&gpio, GPIO_CONTROL_CHANNEL, 0U);
    gpio_control |= CORE_RESET_RELEASE;
    XGpio_DiscreteWrite(&gpio, GPIO_CONTROL_CHANNEL, gpio_control);

    if (wait_status(STATUS_PARALLEL_ACK, 0U) != 0) {
        xil_printf("Core reset/status did not initialize.\r\n");
        return XST_FAILURE;
    }

    xil_printf("Sending %u words at address %u over parallel boot.\r\n",
               (unsigned)FIRMWARE_WORD_COUNT,
               (unsigned)FIRMWARE_LOAD_ADDRESS);

    status = send_frame('W', FIRMWARE_LOAD_ADDRESS, firmware_words,
                        (uint16_t)FIRMWARE_WORD_COUNT);
    if (status != 0) {
        xil_printf(status == -2 ? "Firmware write frame was rejected.\r\n"
                                : "Firmware write frame timed out.\r\n");
        return XST_FAILURE;
    }
    status = send_frame('G', FIRMWARE_LOAD_ADDRESS, firmware_words, 0U);
    if (status != 0) {
        xil_printf(status == -2 ? "Firmware start frame was rejected.\r\n"
                                : "Firmware start frame timed out.\r\n");
        return XST_FAILURE;
    }
    if (wait_status(STATUS_STARTING_FIRMWARE,
                    STATUS_STARTING_FIRMWARE) != 0) {
        xil_printf("Firmware start frame failed.\r\n");
        return XST_FAILURE;
    }

    /* The core has left its loader; reconnect all bidirectional pins to JA. */
    gpio_control &= ~PS_OWNS_PINS;
    XGpio_DiscreteWrite(&gpio, GPIO_CONTROL_CHANNEL, gpio_control);
    xil_printf("Firmware is running; PMOD JA is connected to the emulator.\r\n");
    return XST_SUCCESS;
}
