; LM75B digital thermometer over I2C: set the over-temperature and hysteresis limits,
; then read the temperature READS times, a conversion time apart.
;   pins  : SDA uio[5], SCL uio[6] (open drain, pulled up)
;   output: data RAM [64 ..] the raw temperature registers, [72 ..] the same in
;           eighths of a degree (signed); uo_out = DONE, or BAD if the device did not
;           acknowledge
; Sources: NXP LM75B data sheet (rev. 6): address 1001 A2 A1 A0 (0x48 with A = 000);
; the pointer register picks Temp (0x00, two bytes, 11 bits two's complement left
; aligned, 0.125 degrees a step), Conf (0x01), Thyst (0x02) and Tos (0x03), both two
; bytes, 9 bits, 0.5 degrees a step; a write sets the pointer, then the register; a
; read starts at the pointer; a conversion every 100 ms. I2C bus: NXP UM10204.
.include "msf.inc"
.equ SDA,   0x20
.equ SCL,   0x40
.equ QL,    9
.equ QH,    9
.equ ADDR,  0x48
.equ READS, 3
.equ GAP_P, 20                      ; 20 x 60,000 cycles: 100 ms
.equ BAD,   0xEE

start:  PIN_WI   0x00
        OE_CLR   SDA | SCL
        LDR      R1, #(7 | 5 << 3)  ; SR input = SDA
        SER_CFG  R1
        LDI      R2, writes
wt:     LD       R3, R2             ; bytes in this write (0: done)
        INCR     R2
        ADDS     R3, R3, R3
        JZ       rd
        SHR      R3, R3, #1
        CALL     i2c_start
        LDR      R1, #ADDR << 1
        CALL     i2c_wbyte
        JNZ      fail
wb:     LD       R1, R2
        INCR     R2
        CALL     i2c_wbyte
        JNZ      fail
        DECR     R3
        JNZ      wb
        CALL     i2c_stop
        JMP      wt
rd:     LDR      R6, #64
        LDR      R4, #READS
rl:     CALL     i2c_start
        LDR      R1, #ADDR << 1 | 1
        CALL     i2c_wbyte
        JNZ      fail
        LDR      R5, #1
        ADDS     R5, R5, R5         ; Z = 0: acknowledge the first byte
        CALL     i2c_rbyte
        SWAP     R3, R1             ; the MSB
        LDR      R5, #0
        ADDS     R5, R5, R5         ; Z = 1: the last byte
        CALL     i2c_rbyte
        OR       R3, R3, R1
        CALL     i2c_stop
        ST       R6, R3             ; raw
        SHR      R1, R3, #5         ; eighths of a degree, sign extended
        LDR      R5, #0
        SUBS     R5, R3, R5
        JP       pos
        LDI      R5, 0xF800
        OR       R1, R1, R5
pos:    LDR      R5, #8
        ADDS     R5, R6, R5
        ST       R5, R1
        INCR     R6
        DECR     R4
        JZ       done
        CNT_R    R5                 ; the next conversion
        LDR      R3, #GAP_P
gp:     LDI      R1, 60000
        ADDS     R5, R5, R1
        WAIT_CMP R5
        DECR     R3
        JNZ      gp
        JMP      rl
done:   UO_WI    DONE
        JMP      halt
fail:   CALL     i2c_stop
        UO_WI    BAD
halt:   JMP      halt

.include "i2c.inc"

; Tos = 60.0 (0x3C00), Thyst = 55.0 (0x3700), Conf = 0 (comparator, OS active low),
; then the pointer back to Temp
writes: .word    3, 0x03, 0x3C, 0x00
        .word    3, 0x02, 0x37, 0x00
        .word    2, 0x01, 0x00
        .word    1, 0x00
        .word    0
