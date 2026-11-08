; DS1307 real-time clock over I2C: set the date and time (oscillator running), turn on
; the 1 Hz square wave, keep a byte in its battery-backed RAM, then read it all back.
;   pins  : SDA uio[5], SCL uio[6] (open drain, pulled up)
;   output: data RAM [64 .. 72] registers 0x00-0x08 as read back; uo_out = DONE, or
;           BAD if the device did not acknowledge
; Sources: Maxim DS1307 data sheet (19-5017): address 1101000 (0x68); registers in
; BCD: 0x00 seconds (bit 7 CH, clock halt: 0 to run), 0x01 minutes, 0x02 hours (bit 6
; 0: 24-hour), 0x03 day of the week (1-7), 0x04 date, 0x05 month, 0x06 year, 0x07
; control (bit 4 SQWE, bits 1-0 RS: 00 = 1 Hz), 0x08-0x3F RAM; a write's first byte
; sets the register pointer, which counts up after each byte, as it does when reading.
.include "msf.inc"
.equ SDA,  0x20
.equ SCL,  0x40
.equ QL,   9
.equ QH,   9
.equ ADDR, 0x68
.equ BAD,  0xEE

start:  PIN_WI   0x00
        OE_CLR   SDA | SCL
        LDR      R1, #(7 | 5 << 3)  ; SR input = SDA
        SER_CFG  R1
        ; one write: pointer 0, then 0x00-0x08
        CALL     i2c_start
        LDR      R1, #ADDR << 1
        CALL     i2c_wbyte
        JNZ      fail
        LDI      R2, setting
        LDR      R3, #10
wb:     LD       R1, R2
        INCR     R2
        CALL     i2c_wbyte
        JNZ      fail
        DECR     R3
        JNZ      wb
        ; read back: the pointer to 0 (a write of it alone), repeated START, 9 bytes
        CALL     i2c_start
        LDR      R1, #ADDR << 1
        CALL     i2c_wbyte
        JNZ      fail
        LDR      R1, #0x00
        CALL     i2c_wbyte
        JNZ      fail
        CALL     i2c_start
        LDR      R1, #ADDR << 1 | 1
        CALL     i2c_wbyte
        JNZ      fail
        LDR      R6, #64
        LDR      R3, #9
rb:     LDR      R5, #1
        SUBS     R5, R3, R5         ; Z = 1 on the last byte: NACK it
        CALL     i2c_rbyte
        ST       R6, R1
        INCR     R6
        DECR     R3
        JNZ      rb
        CALL     i2c_stop
        UO_WI    DONE
        JMP      halt
fail:   CALL     i2c_stop
        UO_WI    BAD
halt:   JMP      halt

.include "i2c.inc"

; pointer 0; 00 seconds (CH = 0), 37 minutes, 14 hours (24-hour), Sunday (7),
; 8th, November, (20)26; control: SQWE, 1 Hz; RAM 0x08: 0xA5
setting: .word   0x00, 0x00, 0x37, 0x14, 0x07, 0x08, 0x11, 0x26, 0x10, 0xA5
