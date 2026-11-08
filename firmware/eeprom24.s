; 24C02 serial EEPROM over I2C: write a page of 8 bytes, poll for the end of the
; write cycle by its acknowledge, read the page back and compare.
;   pins  : SDA uio[5], SCL uio[6] (open drain, pulled up)
;   output: data RAM [64 .. 71] the bytes read back, [80] how many polls were NACKed;
;           uo_out = DONE if they match what was written, else BAD
; Sources: Microchip 24AA02/24LC02B data sheet (DS21709): control byte 1010 A2 A1 A0
; R/W (0x50); page write: the word address, then up to 8 bytes, the address wrapping
; within the 8-byte page; the write cycle (tWC, 5 ms at most) starts at the STOP and the
; device does not acknowledge its control byte until it is over (acknowledge polling);
; random read: a write of the word address, a repeated START, then sequential reads,
; the master acknowledging every byte but the last.
.include "msf.inc"
.equ SDA,  0x20
.equ SCL,  0x40
.equ QL,   9
.equ QH,   9
.equ ADDR, 0x50
.equ WADR, 0x10
.equ BAD,  0xEE

start:  PIN_WI   0x00
        OE_CLR   SDA | SCL
        LDR      R1, #(7 | 5 << 3)  ; SR input = SDA
        SER_CFG  R1
        CALL     i2c_start          ; page write
        LDR      R1, #ADDR << 1
        CALL     i2c_wbyte
        JNZ      fail
        LDR      R1, #WADR
        CALL     i2c_wbyte
        JNZ      fail
        LDI      R2, page
        LDR      R3, #8
wb:     LD       R1, R2
        INCR     R2
        CALL     i2c_wbyte
        JNZ      fail
        DECR     R3
        JNZ      wb
        CALL     i2c_stop           ; the write cycle starts
        LDR      R4, #0             ; R4: polls NACKed
poll:   CALL     i2c_start
        LDR      R1, #ADDR << 1
        CALL     i2c_wbyte
        JZ       ready              ; acknowledged: the cycle is over
        INCR     R4
        LDI      R1, 200            ; (a polling limit: 200 polls, well over 5 ms)
        SUBS     R1, R4, R1
        JZ       fail
        JMP      poll
ready:  LDR      R1, #80
        ST       R1, R4
        LDR      R1, #WADR          ; random read: the address (the poll's write)
        CALL     i2c_wbyte
        JNZ      fail
        CALL     i2c_start
        LDR      R1, #ADDR << 1 | 1
        CALL     i2c_wbyte
        JNZ      fail
        LDR      R6, #64
        LDR      R3, #8
rb:     LDR      R5, #1
        SUBS     R5, R3, R5         ; Z = 1 on the last byte: NACK it
        CALL     i2c_rbyte
        ST       R6, R1
        INCR     R6
        DECR     R3
        JNZ      rb
        CALL     i2c_stop
        LDI      R2, page           ; compare
        LDR      R6, #64
        LDR      R3, #8
cmp:    LD       R1, R2
        LD       R4, R6
        SUBS     R1, R1, R4
        JNZ      bad
        INCR     R2
        INCR     R6
        DECR     R3
        JNZ      cmp
        UO_WI    DONE
        JMP      halt
fail:   CALL     i2c_stop
bad:    UO_WI    BAD
halt:   JMP      halt

.include "i2c.inc"

; "MSF v3.0"
page:   .word    0x4D, 0x53, 0x46, 0x20, 0x76, 0x33, 0x2E, 0x30
