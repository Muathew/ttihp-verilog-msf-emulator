#include "ti_msp_dl_config.h"

#include "protocol_test.h"
#include "uart_test.h"
#include "spi_test.h"
#include "i2c_test.h"
#include "v3_test.h"

/* Long enough for the FPGA's monitor to print one pass before the next. */
#define PASS_PERIOD_CYCLES CPUCLK_FREQ

int main(void)
{
    SYSCFG_DL_init();

    ProtocolTest_init();

    UART_Test_init();
    SPI_Test_init();
    I2C_Test_init();
    V3_Test_init();

    /*
     * Results go to the serial log (see protocol_test.h) and stay in
     * protocolResults[] for the debugger. The FPGA program decides which test runs:
     * the v2 protocol peer (firmware/mcu_protocol_target.s) only echoes, the v3
     * board peer (firmware/mcu_v3_peer.s) prompts with 'S', and once it has, every
     * pass is a v3 pass (v3_test.h). Nothing to set on the MCU.
     */
    while (1) {
        if (V3_Test_peerIsV3()) {
            V3_Test_runPass();
            delay_cycles(PASS_PERIOD_CYCLES);
        } else {
            ProtocolTest_runAll();
            delay_cycles(PASS_PERIOD_CYCLES);
        }
    }
}
