; AM2302 (DHT22) humidity and temperature sensor, read by the chip as the host: the
; start signal, then the sensor's 40 bits timed edge to edge on the one wire.
;   pin   : uio[0], open drain: OUT stays 0, OE = 1 pulls the line low, OE = 0 lets
;           the pull-up (and the sensor) have it
;   output: data RAM [64] relative humidity x 10, [65] temperature x 10 (bit 15 the
;           sign), [66] the checksum byte; uo_out = DONE if the checksum agrees and no
;           edge was missing, else BAD
; Sources: Aosong AM2302 data sheet (single-bus timing table): the host holds the bus
;           low for Tbe 0.8-20 ms (1.2 ms here) and releases it; the sensor answers
;           low 80 us (Trel) and high 80 us (Treh), then 40 bits, each a low of 50 us
;           (TLOW) and a high of 26 us (a 0, TH0) or 70 us (a 1, TH1), MSB first:
;           humidity (16 bits), temperature (16 bits, bit 15 the sign), and a checksum,
;           the low 8 bits of the sum of the four bytes before it.
.include "msf.inc"
.equ BUS,    0x01
.equ T_BE,   14400                  ; 1.2 ms
.equ EDGE_T, 2400                   ; 200 us: an edge that does not come is an error
.equ TH_1,   576                    ; 48 us: a high longer than this is a 1
.equ BAD,    0xEE
.equ RISE,   0x10                   ; WAIT_EDGE conditions on uio[0]
.equ FALL,   0x20

start:  PIN_CLR  BUS
        OE_SET   BUS                ; the start signal
        CNT_R    R5
        LDI      R1, T_BE
        ADDS     R5, R5, R1
        WAIT_CMP R5
        OE_CLR   BUS                ; released
        LDI      R1, EDGE_T
        TMO      R1
        WAIT_EDGE #FALL             ; the sensor: low 80 us
        JZ       bad
        WAIT_EDGE #RISE             ; high 80 us
        JZ       bad
        WAIT_EDGE #FALL             ; the first bit's low
        JZ       bad
        LDR      R0, #64            ; R0: where the next 16 bits go
        LDR      R6, #3             ; three fields: 16, 16 and 8 bits
        LDR      R4, #16
fld:    LDR      R3, #0
bit:    WAIT_EDGE #RISE             ; the low ends
        JZ       bad
        CNT_R    R1
        WAIT_EDGE #FALL             ; the high ends: its length is the bit
        JZ       bad
        CNT_R    R2
        SUBS     R2, R2, R1
        ADDS     R3, R3, R3         ; (before the test: ADDS sets the flags)
        LDI      R1, TH_1
        SUBS     R2, R1, R2         ; N: longer than TH_1, a 1
        JP       b0
        INCR     R3
b0:     DECR     R4
        JNZ      bit
        ST       R0, R3
        INCR     R0
        LDR      R4, #16
        DECR     R6
        LDR      R1, #1
        SUBS     R1, R6, R1
        JNZ      f_n
        LDR      R4, #8             ; the last field: the checksum, 8 bits
f_n:    ADDS     R6, R6, R6
        SHR      R6, R6, #1
        JNZ      fld
        ; the checksum: the four bytes' sum, low 8 bits
        LDR      R1, #64
        LD       R2, R1
        SHR      R3, R2, #4
        SHR      R3, R3, #4         ; humidity high byte
        LDR      R4, #0xFF
        AND      R2, R2, R4
        ADDS     R3, R3, R2
        INCR     R1
        LD       R2, R1
        SHR      R5, R2, #4
        SHR      R5, R5, #4
        ADDS     R3, R3, R5
        AND      R2, R2, R4
        ADDS     R3, R3, R2
        AND      R3, R3, R4
        INCR     R1
        LD       R2, R1
        SUBS     R2, R2, R3
        JNZ      bad
        UO_WI    DONE
        JMP      halt
bad:    UO_WI    BAD
halt:   JMP      halt
