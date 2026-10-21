; Two deterministic hardware threads sharing the chip:
;   T0: square wave on uio[7] with an exact half period (absolute WAIT_CMP
;       schedule; HALF must be even so every target lands in a T0 slot)
;   T1: UART echo, uio[1] (RX engine) -> uio[0] (TX engine), 115200 8N1 at 12 MHz
; Neither thread's timing depends on what the other one is doing.
.include "msf.inc"
.equ HALF, 500

start:
        OE_SET   0x81
        LDI      R1, echo_thread
        TSTART   R1
        LDI      R6, HALF
        CNT_R    R5
blink:  ADDS     R5, R5, R6
        WAIT_CMP R5
        PIN_TGL  0x80
        JMP      blink

echo_thread:
        LDR      R1, #0
        TMO      R1                 ; T1's blocking ops wait forever (T0 keeps its own TMO)
        LDR      R1, #104
        TX_DIV   R1
        RX_DIV   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | 0)
        TX_CFG   R1                 ; 10-bit frames: start, 8 data, stop
        LDI      R1, (RX_EN | RX_CHAR | RX_LSB | (8 - 1) << 8 | 1)
        RX_CFG   R1
        LDI      R2, 0x200          ; stop bit
echo:   RX_POP   R1
        ADDS     R1, R1, R1         ; start bit 0
        OR       R1, R1, R2
        TX_PUSH  R1
        JMP      echo
