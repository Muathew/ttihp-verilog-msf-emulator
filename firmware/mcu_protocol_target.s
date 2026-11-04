; MSPM0G3517 protocol-test peer for PMOD JA.
;   UIO0 UART RX, UIO1 UART TX
;   UIO2 I2C SCL, UIO3 I2C SDA (open drain, external pull-ups)
;   UIO4 SPI SCK, UIO5 SPI CS, UIO6 SPI MOSI, UIO7 SPI MISO
;   UART: 115200 8N1 byte echo
;   I2C : 7-bit address 0x50, register 0 returns 0xA5
;   SPI : mode 0, one-byte CS frames; full-duplex test stream
;         55 AA 12 34 DE AD BE EF, matching and checking the MSPM0 payload
.include "msf.inc"

.equ UART_RX, 0
.equ UART_TX, 1
.equ I2C_SCLP, 2
.equ I2C_SDAP, 3
.equ SPI_SCKP, 4
.equ SPI_CSP, 5
.equ SPI_MOSIP, 6
.equ SPI_MISOP, 7
.equ SPI_ERROR, 0x80
.equ UART_BIT, 104
.equ I2C_ADDR, 0x50
.equ I2C_SCL, 1 << I2C_SCLP
.equ I2C_SDA, 1 << I2C_SDAP
.equ I2C_LINES, I2C_SCL | I2C_SDA
.equ I2C_HOLD, I2C_SCL
.equ SPI_MISO, 1 << SPI_MISOP
.equ SPI_CS, 1 << SPI_CSP

; T0 starts the protocol handlers and initializes their shared pin/SRAM state.
start:
        PIN_WI   0x00
        OE_WI    0x00
        LDR      R1, #(SPI_MISOP | SPI_MOSIP << 3)
        SER_CFG  R1
        UO_WI    0x00
        LDR      R1, #0xA5
        LDR      R2, #0
        ST       R2, R1
        LDI      R1, uart_target
        TSTART   #1, R1
        LDI      R1, i2c_target
        TSTART   #2, R1
        LDI      R1, spi_target
        TSTART   #3, R1
main_idle:
        JMP      main_idle

; T1: autonomous UART receiver plus transmitter, echoing each 8-bit byte.
uart_target:
        PIN_SET  (1 << UART_TX)
        OE_SET   (1 << UART_TX)
        LDR      R1, #UART_BIT
        RX_DIV   R1
        TX_DIV   R1
        LDI      R1, (RX_EN | RX_CHAR | RX_LSB | (8 - 1) << 8 | UART_RX)
        RX_CFG   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | UART_TX)
        TX_CFG   R1
        LDI      R2, 0x200
uart_echo:
        RX_POP   R1
        OUT_W    R1
        ADDS     R1, R1, R1
        OR       R1, R1, R2
        TX_PUSH  R1
        JMP      uart_echo

; T2: I2C target.  Accumulate incoming bytes in R1 so the SPI thread can own
; the shared serial shift register.  Clock stretching is used for reliable
; response timing while all four hardware threads are active.
i2c_target:
        LDR      R0, #I2C_SCL
        LDR      R5, #I2C_SDA
        LDR      R7, #I2C_LINES
        LDR      R3, #0
        LDR      R1, #0
        TMO      R1
i2c_idle:
        OE_CLR   I2C_LINES
i2c_wait_start:
        WAIT_EDGE I2C_SDAP, fall
        PIN_R    R1
        AND      R1, R1, R0
        ADDS     R1, R1, R1
        JZ       i2c_wait_start
i2c_start:
        WAIT_EDGE I2C_SCLP, low

; Address byte, sampled MSB first while SCL is high.
        LDR      R1, #0
        LDR      R4, #8
i2c_addr_bit:
        WAIT_EDGE I2C_SCLP, high
        PIN_R    R6
        AND      R6, R6, R7
        SHL      R1, R1, #1
        AND      R2, R6, R5
        SHR      R2, R2, #I2C_SDAP
        OR       R1, R1, R2
i2c_addr_fall:
        PIN_R    R2
        AND      R2, R2, R7
        SUBS     R2, R2, R6
        JZ       i2c_addr_fall
        SUBS     R2, R2, R0
        JP       i2c_sda_change
        DECR     R4
        JNZ      i2c_addr_bit
        OE_SET   I2C_HOLD
        MOV      R2, R1
        LDR      R1, #(I2C_ADDR << 1)
        SUBS     R1, R2, R1
        JZ       i2c_write
        DECR     R1
        JZ       i2c_read
        JMP      i2c_idle

; Pointer and data write transactions.
i2c_write:
        OE_SET   I2C_SDA
        OE_CLR   I2C_HOLD
        WAIT_EDGE I2C_SCLP, high
        WAIT_EDGE I2C_SCLP, low
        OE_CLR   I2C_SDA
        LDR      R1, #0
        LDR      R4, #8
i2c_ptr_bit:
        WAIT_EDGE I2C_SCLP, high
        PIN_R    R6
        AND      R6, R6, R7
        SHL      R1, R1, #1
        AND      R2, R6, R5
        SHR      R2, R2, #I2C_SDAP
        OR       R1, R1, R2
i2c_ptr_fall:
        PIN_R    R2
        AND      R2, R2, R7
        SUBS     R2, R2, R6
        JZ       i2c_ptr_fall
        SUBS     R2, R2, R0
        JP       i2c_sda_change
        DECR     R4
        JNZ      i2c_ptr_bit
        OE_SET   I2C_LINES
        MOV      R3, R1
        LDR      R1, #0x7F
        AND      R3, R3, R1
        OE_CLR   I2C_HOLD
i2c_write_ack:
        WAIT_EDGE I2C_SCLP, high
        WAIT_EDGE I2C_SCLP, low
        OE_CLR   I2C_SDA
        LDR      R1, #0
        LDR      R4, #8
i2c_data_bit:
        WAIT_EDGE I2C_SCLP, high
        PIN_R    R6
        AND      R6, R6, R7
        SHL      R1, R1, #1
        AND      R2, R6, R5
        SHR      R2, R2, #I2C_SDAP
        OR       R1, R1, R2
i2c_data_fall:
        PIN_R    R2
        AND      R2, R2, R7
        SUBS     R2, R2, R6
        JZ       i2c_data_fall
        SUBS     R2, R2, R0
        JP       i2c_sda_change
        DECR     R4
        JNZ      i2c_data_bit
        OE_SET   I2C_LINES
        MOV      R2, R1
        ST       R3, R2
        INCR     R3
        LDR      R1, #0x7F
        AND      R3, R3, R1
        OE_CLR   I2C_HOLD
        JMP      i2c_write_ack

; Register read.  A repeated START after the pointer byte returns data RAM[0].
i2c_read:
        OE_SET   I2C_SDA
        LD       R2, R3
        SWAP     R2, R2
        OE_CLR   I2C_HOLD
        WAIT_EDGE I2C_SCLP, high
        WAIT_EDGE I2C_SCLP, low
i2c_tx_byte:
        LDR      R4, #8
i2c_tx_bit:
        MOV      R1, R2
        INCR     R1
        JN       i2c_tx_one
        OE_SET   I2C_SDA
        JMP      i2c_tx_clock
i2c_tx_one:
        OE_CLR   I2C_SDA
        NOP
i2c_tx_clock:
        SHL      R2, R2, #1
        WAIT_EDGE I2C_SCLP, high
        WAIT_EDGE I2C_SCLP, low
        DECR     R4
        JNZ      i2c_tx_bit
        OE_CLR   I2C_SDA
        INCR     R3
        LDR      R1, #0x7F
        AND      R3, R3, R1
        LD       R2, R3
        SWAP     R2, R2
        WAIT_EDGE I2C_SCLP, high
        PIN_R    R6
        WAIT_EDGE I2C_SCLP, low
        AND      R6, R6, R5
        ADDS     R6, R6, R6
        JZ       i2c_tx_byte
        JMP      i2c_idle

i2c_sda_change:
        AND      R1, R6, R5
        ADDS     R1, R1, R1
        JNZ      i2c_start
        JMP      i2c_idle

; T3: each one-byte CS frame returns the corresponding test-vector byte and
; compares the simultaneously received MOSI byte. MISO stays driven between
; frames so its first bit is ready even when CS setup is shorter than a core
; input-synchronizer interval; this PMOD fixture is a single-device SPI bus.
spi_target:
        LDI      R6, spi_bytes
        LDR      R5, #0
        MOV      R7, R6
        LD       R1, R7
        MOV      R2, R1
        SWAP     R1, R1
        SR_LD    R1
        LDR      R0, #0
        LDR      R4, #8
        OE_SET   SPI_MISO
        SHIFT_OUT R0
spi_wait_select:
        WAIT_EDGE SPI_CSP, low
spi_bit:
        WAIT_EDGE SPI_SCKP, high
        XFER
        DECR     R4
        JNZ      spi_bit
        WAIT_EDGE SPI_CSP, high
        SR_RD    R1
        LDR      R3, #0xFF
        AND      R1, R1, R3
        SUBS     R1, R1, R2
        JZ       spi_match
        UO_SET   SPI_ERROR
spi_match:
        INCR     R5
        LDR      R1, #7
        AND      R5, R5, R1
        ADDS     R7, R6, R5
        LD       R1, R7
        MOV      R2, R1
        SWAP     R1, R1
        SR_LD    R1
        SHIFT_OUT R0
        LDR      R4, #8
        JMP      spi_wait_select

spi_bytes:
        .word 0x0055, 0x00AA, 0x0012, 0x0034
        .word 0x00DE, 0x00AD, 0x00BE, 0x00EF
