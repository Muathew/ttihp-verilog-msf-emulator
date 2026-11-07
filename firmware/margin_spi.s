; Margin finder, SPI: how fast a clock does a device still follow? Runs two
; transfers at SCK half period p, for each p in the sweep, and passes if the second
; reads back what this device is meant to answer. See margin.inc for the report.
;   pins  : SCK uio[2], MOSI uio[3], MISO uio[4], CS uio[5] (mode 0, MSB first)
;   SEND, WANT: the byte sent, and what MISO must carry in the next byte (defines;
;           the default is a device that answers the complement of the last byte)
; Every edge is on the cycle counter, so a half period is p cycles exactly.
.equ SCK,  0x04
.equ CS,   0x20
.equ SEND, 0xA5
.equ WANT, 0x5A
.equ SWEEP_LO, 20
.equ SWEEP_HI, 48
.equ NOM,  SWEEP_HI
.include "margin.inc"

setup:  PIN_SET  CS
        PIN_CLR  (SCK | 0x08)
        OE_SET   (SCK | 0x08 | CS)
        LDR      R1, #(3 | 4 << 3)
        SER_CFG  R1                ; SR out = MOSI, in = MISO, MSB first
        RET

trial:  CNT_R    R5
        LDI      R1, 40
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  CS
        ADDS     R5, R5, R3        ; @range R3 SWEEP_LO SWEEP_HI
        WAIT_CMP R5
        CALL     xbyte             ; the first byte: the device learns SEND
        CALL     xbyte             ; the second: R6 = what it answered
        ADDS     R5, R5, R3        ; @range R3 SWEEP_LO SWEEP_HI
        WAIT_CMP R5
        PIN_SET  CS
        LDI      R1, WANT
        SUBS     R1, R6, R1
        JZ       t_ok
        LDR      R1, #0
        JMP      tdone
t_ok:   LDR      R1, #1
        JMP      tdone

; one byte: SEND out, R6 = the byte in; R3 = half period, R5 = the last edge
xbyte:  LDI      R1, SEND << 8
        SR_LD    R1
        LDR      R4, #8
xb:     XFER                       ; MOSI changes and MISO is read, half a period
        ADDS     R5, R5, R3        ; @range R3 SWEEP_LO SWEEP_HI
        WAIT_CMP R5
        PIN_SET  SCK
        ADDS     R5, R5, R3        ; @range R3 SWEEP_LO SWEEP_HI
        WAIT_CMP R5
        PIN_CLR  SCK
        DECR     R4
        JNZ      xb
        SR_RD    R6
        LDR      R1, #0xFF
        AND      R6, R6, R1
        RET
