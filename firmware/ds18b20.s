; DS18B20 1-Wire thermometer (alone on the bus): convert, wait for the conversion by
; polling read slots, read the scratchpad, check its CRC, keep the temperature.
;   pin   : DQ uio[3], open drain (OUT stays 0, OE = 1 pulls low), pulled up
;   output: data RAM [64 .. 72] the scratchpad, [73] the temperature in 1/16 degree
;           (signed); uo_out = DONE, or BAD (no presence pulse, no end of conversion,
;           or a CRC that does not check)
; Sources: Maxim DS18B20 data sheet (19-7487): SKIP ROM 0xCC, CONVERT T 0x44 (read slots
; return 0 while converting, 1 when done; up to 750 ms at 12 bits), READ SCRATCHPAD
; 0xBE (9 bytes, LSB first, temperature LSB and MSB first, CRC last), CRC-8 x^8 + x^5 +
; x^4 + 1; Maxim application note 126 (standard-speed slots): reset low 480 us, the
; presence sampled 70 us after release; write 1: low 6 us, slot 70 us; write 0: low
; 60 us, then 10 us; read: low 6 us, sample 9 us later, 55 us more.
.include "msf.inc"
.equ DQ,    0x08
.equ US,    12
.equ BAD,   0xEE
.equ POLLS, 60000                   ; read slots before giving up (about 4.2 s)

start:  PIN_CLR  DQ
        OE_CLR   DQ
        CALL     reset
        JNZ      bad                ; no presence pulse
        LDR      R1, #0xCC
        CALL     wbyte
        LDR      R1, #0x44
        CALL     wbyte
        LDI      R4, POLLS          ; poll: a read slot gives 1 when the conversion ends
cv:     CALL     rbit
        ADDS     R1, R1, R1
        JNZ      cv_end
        DECR     R4
        JNZ      cv
        JMP      bad
cv_end: CALL     reset
        JNZ      bad
        LDR      R1, #0xCC
        CALL     wbyte
        LDR      R1, #0xBE
        CALL     wbyte
        LDR      R6, #64            ; 9 bytes
        LDR      R4, #9
rd:     CALL     rbyte
        ST       R6, R1
        INCR     R6
        DECR     R4
        JNZ      rd
        ; CRC-8 (reflected, 0x8C) over all 9: 0 if they check
        LDR      R6, #64
        LDR      R4, #9
        LDR      R2, #0             ; R2: the CRC
cb:     LD       R1, R6
        INCR     R6
        LDR      R3, #8
cbit:   XOR      R0, R2, R1
        SHR      R2, R2, #1
        LDR      R5, #1
        AND      R0, R0, R5
        ADDS     R0, R0, R0
        JZ       c_n
        LDR      R5, #0x8C
        XOR      R2, R2, R5
c_n:    SHR      R1, R1, #1
        DECR     R3
        JNZ      cbit
        DECR     R4
        JNZ      cb
        ADDS     R2, R2, R2
        JNZ      bad
        LDR      R1, #65            ; the temperature: MSB << 8 | LSB
        LD       R2, R1
        SWAP     R2, R2
        DECR     R1
        LD       R3, R1
        OR       R2, R2, R3
        LDR      R1, #73
        ST       R1, R2
        UO_WI    DONE
        JMP      halt
bad:    UO_WI    BAD
halt:   JMP      halt

; reset and presence: Z = 1 if a device answered. Uses R5, R1.
reset:  CNT_R    R5
        OE_SET   DQ                 ; low 480 us
        LDI      R1, 480 * US
        ADDS     R5, R5, R1
        WAIT_CMP R5
        OE_CLR   DQ
        LDI      R1, 70 * US
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_R    R1                 ; the presence pulse holds it low
        LDI      R0, 410 * US
        ADDS     R5, R5, R0
        WAIT_CMP R5
        LDR      R0, #DQ
        AND      R1, R1, R0
        ADDS     R1, R1, R1         ; Z = 1: low, a device
        RET
; write byte R1, LSB first. Uses R0, R1, R2, R3, R5.
wbyte:  LDR      R3, #8
wb:     CNT_R    R5
        OE_SET   DQ
        LDR      R0, #1
        AND      R0, R1, R0
        ADDS     R0, R0, R0
        LDI      R2, 60 * US        ; a 0: low 60 us, then 10
        LDR      R0, #10 * US
        JZ       wb_lo
        LDR      R2, #6 * US        ; a 1: low 6 us, then 64
        LDI      R0, 64 * US
wb_lo:  ADDS     R5, R5, R2
        WAIT_CMP R5
        OE_CLR   DQ
        ADDS     R5, R5, R0
        WAIT_CMP R5
        SHR      R1, R1, #1
        DECR     R3
        JNZ      wb
        RET
; read a bit into R1 (0 or DQ). Uses R0, R1, R5.
rbit:   CNT_R    R5
        OE_SET   DQ
        LDR      R0, #6 * US
        ADDS     R5, R5, R0
        WAIT_CMP R5
        OE_CLR   DQ
        LDR      R0, #9 * US
        ADDS     R5, R5, R0
        WAIT_CMP R5
        PIN_R    R1
        LDR      R0, #DQ
        AND      R1, R1, R0
        LDI      R0, 55 * US
        ADDS     R5, R5, R0
        WAIT_CMP R5
        RET
; read a byte into R1, LSB first. Uses R0-R3, R5; the return address waits in RAM 0.
rbyte:  LDR      R0, #0
        ST       R0, R7
        LDR      R2, #0
        LDR      R3, #8
rbb:    SHR      R2, R2, #1
        CALL     rbit
        ADDS     R1, R1, R1
        JZ       rb0
        LDI      R1, 0x80
        OR       R2, R2, R1
rb0:    DECR     R3
        JNZ      rbb
        MOV      R1, R2
        LDR      R0, #0
        LD       R7, R0
        RET
