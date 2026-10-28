; HIL RX probe (test_peripherals.rx_fw): FILTER = FILT (0: off), then the RX
; engine with RX_DIV = DIV and RX_CFG = CFG stores N words at 0.. and shows
; DONE. Shows 0x01 once armed.
.include "msf.inc"
.equ FILT, 0
.equ DIV,  8
.equ CFG,  0
.equ N,    1

        LDI      R1, FILT
        FILTER   R1
        LDR      R1, #0
        TMO      R1                 ; RX_POP waits forever
        LDI      R1, DIV
        RX_DIV   R1
        LDI      R1, CFG
        RX_CFG   R1
        UO_WI    0x01               ; armed
        LDR      R2, #0
        LDR      R6, #N
lp:     RX_POP   R3
        ST       R2, R3
        INCR     R2
        DECR     R6
        JNZ      lp
        STAT     R4
        UO_WI    DONE
halt:   JMP      halt
