#include "ti_msp_dl_config.h"

#include "protocol_test.h"
#include "uart_test.h"
#include "spi_test.h"
#include "i2c_test.h"

/* Long enough for the FPGA's monitor to print one pass before the next. */
#define PASS_PERIOD_CYCLES CPUCLK_FREQ

int main(void)
{
    SYSCFG_DL_init();

    ProtocolTest_init();

    UART_Test_init();
    SPI_Test_init();
    I2C_Test_init();

    /*
     * Results go to the serial log (see protocol_test.h) and stay in
     * protocolResults[] for the debugger.
     */
    while (1) {
        ProtocolTest_runAll();
        delay_cycles(PASS_PERIOD_CYCLES);
    }
}
