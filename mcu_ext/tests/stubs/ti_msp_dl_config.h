/*
 * Stand-in for the SysConfig-generated header, for compiling the MCU test's own
 * code on the PC (mcu_ext/tests/run_compile_test.sh). Every DriverLib name here has
 * the signature of the MSPM0 SDK's driverlib (dl_spi.h, dl_gpio.h, dl_uart_main.h,
 * checked 9 Nov 2026); the pin names follow its SysConfig templates. It only
 * compiles: nothing here touches hardware or behaves like it.
 */
#ifndef TI_MSP_DL_CONFIG_STUB_H
#define TI_MSP_DL_CONFIG_STUB_H
#include <stdbool.h>
#include <stdint.h>

#define CPUCLK_FREQ 32000000U

typedef struct { volatile uint32_t CTL0, CTL1, CLKCTL, STAT; } SPI_Regs;
typedef struct { volatile uint32_t DIN31_0; } GPIO_Regs;
typedef struct { volatile uint32_t STAT; } UART_Regs;
typedef struct { volatile uint32_t CTRL, LOAD, VAL, CALIB; } SysTick_Type;
extern SysTick_Type *const SysTick;
#define SysTick_CTRL_CLKSOURCE_Msk (1UL << 2)
#define SysTick_CTRL_ENABLE_Msk    (1UL << 0)
void __disable_irq(void);
void __enable_irq(void);

extern SPI_Regs *const SPI_0_INST;
extern UART_Regs *const UART_0_INST;
extern UART_Regs *const UART_1_INST;
extern GPIO_Regs *const GPIOA;
extern GPIO_Regs *const GPIOB;
#define GPIO_SPI_0_POCI_PORT       GPIOB
#define GPIO_SPI_0_POCI_PIN        (1UL << 19)
#define GPIO_SPI_0_IOMUX_POCI      (45U)
#define GPIO_SPI_0_IOMUX_POCI_FUNC (3U)
#define GPIO_SPI_0_IOMUX_SCLK      (44U)
#define GPIO_SPI_0_IOMUX_CS0       (60U)
#define GPIO_I2C_0_SCL_PORT        GPIOA
#define GPIO_I2C_0_SCL_PIN         (1UL << 1)
#define GPIO_I2C_0_IOMUX_SCL       (2U)

typedef enum { DL_SPI_FRAME_FORMAT_MOTO4_POL0_PHA1 = 0x31 } DL_SPI_FRAME_FORMAT;
typedef enum { DL_SPI_DATA_SIZE_16 = 15 } DL_SPI_DATA_SIZE;
void DL_SPI_enable(SPI_Regs *spi);
void DL_SPI_disable(SPI_Regs *spi);
bool DL_SPI_isBusy(const SPI_Regs *spi);
void DL_SPI_setFrameFormat(SPI_Regs *spi, DL_SPI_FRAME_FORMAT frameFormat);
void DL_SPI_setDataSize(SPI_Regs *spi, DL_SPI_DATA_SIZE dataSize);
void DL_SPI_setBitRateSerialClockDivider(SPI_Regs *spi, uint32_t SCR);
bool DL_SPI_isTXFIFOFull(const SPI_Regs *spi);
bool DL_SPI_isRXFIFOEmpty(const SPI_Regs *spi);
void DL_SPI_transmitData16(SPI_Regs *spi, uint16_t data);
uint16_t DL_SPI_receiveData16(const SPI_Regs *spi);

typedef enum { DL_GPIO_INVERSION_DISABLE = 0 } DL_GPIO_INVERSION;
typedef enum { DL_GPIO_RESISTOR_NONE = 0, DL_GPIO_RESISTOR_PULL_UP = 1,
               DL_GPIO_RESISTOR_PULL_DOWN = 2 } DL_GPIO_RESISTOR;
typedef enum { DL_GPIO_HYSTERESIS_DISABLE = 0 } DL_GPIO_HYSTERESIS;
typedef enum { DL_GPIO_WAKEUP_DISABLE = 0 } DL_GPIO_WAKEUP;
void DL_GPIO_initDigitalInput(uint32_t pincmIndex);
void DL_GPIO_initDigitalInputFeatures(uint32_t pincmIndex, DL_GPIO_INVERSION inversion,
    DL_GPIO_RESISTOR internalResistor, DL_GPIO_HYSTERESIS hysteresis, DL_GPIO_WAKEUP wakeup);
void DL_GPIO_initPeripheralInputFunction(uint32_t pincmIndex, uint32_t function);
uint32_t DL_GPIO_readPins(GPIO_Regs *gpio, uint32_t pins);

void SYSCFG_DL_init(void);
void delay_cycles(uint32_t cycles);
#endif
