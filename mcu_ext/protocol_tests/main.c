#include "ti_msp_dl_config.h"

#include "protocol_test.h"
#include "uart_test.h"
#include "spi_test.h"
#include "i2c_test.h"
#include "can_test.h"

int main(void)
{
    SYSCFG_DL_init();

    ProtocolTest_init();

    UART_Test_init();
    SPI_Test_init();
    I2C_Test_init();
    CAN_Test_init();

    /*
     * Run all protocol tests once.
     *
     * Repeat this call when you want to execute another test
     * cycle. Avoid an unrestricted continuous loop because
     * the I2C and CAN tests involve timeout waits.
     */
    while (1) {
        ProtocolTest_runAll();

        /*
         * Results are available in protocolResults[].
         * Set a breakpoint here to inspect them in CCS.
         */
        __WFI();
    }
}