; Margin finder, UART: how far off its baud rate does a device still read us?
; Sends a byte to the device at p cycles a bit, for each p in the sweep, and checks
; what the device echoes at its own rate. See margin.inc for the report.
;   pins  : TXP to the device, RXP from it, REPORT_PIN to the host (defines)
;   NOM   : the device's nominal bit time, cycles (define; 104 = 115200 at 12 MHz)
;   BYTE  : the byte sent (0x55: every bit an edge, and the last data bit far from
;           the start, so it finds the receiver's limit)
; One cycle of p moves the end of a frame by 10 cycles, so the steps are fine.
; A device that echoes nothing at any p reports lo = 0.
.equ TXP,  1
.equ RXP,  2
.equ NOM,  104
.equ BYTE, 0x55
.equ SWEEP_LO, NOM - 10
.equ SWEEP_HI, NOM + 10
.include "margin.inc"

setup:  PIN_SET  (1 << TXP)
        OE_SET   (1 << TXP)
        RET

trial:  LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | TXP)
        TX_CFG   R1
        TX_DIV   R3                ; the swept bit time
        LDI      R1, NOM
        RX_DIV   R1
        LDI      R1, (RX_EN | RX_CHAR | RX_LSB | (9 - 1) << 8 | RXP)
        RX_CFG   R1                ; 8 data bits + the stop bit; drops any old word
        CNT_R    R5
        LDI      R6, 2 * NOM
        ADDS     R5, R5, R6
        WAIT_CMP R5                ; two bits of idle line before the frame
        LDI      R1, 0x200 | BYTE << 1
        TX_PUSH  R1
        TX_WAIT
        LDI      R1, 14 * NOM
        TMO      R1
        RX_POP   R1
        LDR      R2, #0
        TMO      R2                ; (TMO leaves the flags alone)
        JZ       t_no
        LDI      R2, 0x100 | BYTE
        SUBS     R1, R1, R2
        JNZ      t_no
        LDR      R1, #1
        JMP      tdone
t_no:   LDR      R1, #0
        JMP      tdone
