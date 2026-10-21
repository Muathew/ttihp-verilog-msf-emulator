; I2C master (open drain, clock stretching aware): one write and/or read
; transaction per run.
;   pins   : SDA uio[5], SCL uio[6]; OUT stays 0, OE=1 pulls the line low,
;            OE=0 releases it (external pull-up)
;   input  : data RAM [0] = 7-bit address, [1] = n_write, [2 ..] = write bytes,
;            [2+n_write] = n_read
;   output : read bytes at data RAM [64 ..]
;            uo_out = 0x80 | bit1 (all write bytes ACKed) | bit0 (address ACKed)
; Q = quarter-bit delay loop count (3 cycles per count): Q=9 ~ 100 kHz at 12 MHz.
; QL / QH = the delay counts of the SCL low / high phases (default Q): each
; count moves an edge by 3 cycles, so the controller can break a device's
; timing by exact amounts (timing-fault injection).
.include "msf.inc"
.equ SDA, 0x20
.equ SCL, 0x40
.equ Q,   9
.equ QL,  Q
.equ QH,  Q

start:
        PIN_WI   0x00               ; drive-low value for both lines
        OE_CLR   (SDA | SCL)        ; bus idle: both released
        LDR      R1, #(7 | 5 << 3)  ; SR input = SDA (for receiving)
        SER_CFG  R1
        LDR      R6, #0             ; status
        LDR      R2, #0
        LD       R3, R2             ; address
        LDR      R2, #1
        LD       R4, R2             ; n_write
        LDR      R2, #2
        ADDS     R5, R4, R4
        JZ       rphase             ; nothing to write
        CALL     i2c_start
        ADDS     R1, R3, R3         ; address << 1 | W
        CALL     i2c_wbyte
        JNZ      stop               ; address NACK
        LDR      R6, #1
wl:     LD       R1, R2
        CALL     i2c_wbyte
        JNZ      stop               ; data NACK
        INCR     R2
        DECR     R4
        JNZ      wl
        LDR      R5, #2
        OR       R6, R6, R5         ; every write byte ACKed
rphase:
        LD       R4, R2             ; n_read
        ADDS     R5, R4, R4
        JZ       stop
        CALL     i2c_start          ; (repeated) START
        ADDS     R1, R3, R3
        INCR     R1                 ; address << 1 | R
        CALL     i2c_wbyte
        JNZ      stop
        LDR      R5, #1
        OR       R6, R6, R5
        LDR      R2, #64
rl:     LDR      R5, #1
        SUBS     R5, R4, R5         ; Z=1 on the last byte -> NACK it
        CALL     i2c_rbyte
        ST       R2, R1
        INCR     R2
        DECR     R4
        JNZ      rl
stop:
        CALL     i2c_stop
        LDR      R5, #0x80
        OR       R6, R6, R5
        OUT_W    R6
halt:   JMP      halt

; ---- START (from idle or after an ACK clock: SCL low) ----
i2c_start:
        OE_CLR   SDA
        LDR      R5, #QL
s1:     DECR     R5
        JNZ      s1
        OE_CLR   SCL
        WAIT_EDGE 6, high           ; honour clock stretching
        LDR      R5, #QH
s2:     DECR     R5
        JNZ      s2
        OE_SET   SDA                ; SDA falls while SCL is high
        LDR      R5, #QH
s3:     DECR     R5
        JNZ      s3
        OE_SET   SCL
        RET

; ---- STOP ----
i2c_stop:
        OE_SET   SDA
        LDR      R5, #QL
p1:     DECR     R5
        JNZ      p1
        OE_CLR   SCL
        WAIT_EDGE 6, high
        LDR      R5, #QH
p2:     DECR     R5
        JNZ      p2
        OE_CLR   SDA                ; SDA rises while SCL is high
        RET

; ---- write byte R1, MSB first; returns Z=1 iff the slave ACKed ----
; clobbers R0, R1, R5
i2c_wbyte:
        SWAP     R1, R1             ; byte -> bits 15..8
        LDR      R0, #8
wb:     MOV      R5, R1
        INCR     R5                 ; N = bit 15 (low byte is 0, no carry)
        JN       wb1
        OE_SET   SDA                ; 0: pull low
        JMP      wbc
wb1:    OE_CLR   SDA                ; 1: release
        NOP                         ; equal path lengths
wbc:    LDR      R5, #QL
w1:     DECR     R5
        JNZ      w1
        OE_CLR   SCL
        WAIT_EDGE 6, high
        LDR      R5, #QH
w2:     DECR     R5
        JNZ      w2
        OE_SET   SCL
        SHL      R1, R1, #1
        DECR     R0
        JNZ      wb
        OE_CLR   SDA                ; ACK clock: release SDA for the slave
        LDR      R5, #QL
w3:     DECR     R5
        JNZ      w3
        OE_CLR   SCL
        WAIT_EDGE 6, high
        LDR      R5, #QH
w4:     DECR     R5
        JNZ      w4
        PIN_R    R5                 ; SDA = 0 -> ACK
        OE_SET   SCL
        LDR      R0, #SDA
        AND      R5, R5, R0
        ADDS     R5, R5, R5         ; Z=1 iff ACK
        RET

; ---- read byte -> R1; Z=1 on entry = last byte (send NACK) ----
; clobbers R0, R1, R5; bits are shifted in through SR (input pin = SDA)
i2c_rbyte:
        LDR      R1, #1             ; NACK
        JZ       rb0
        LDR      R1, #0             ; ACK
rb0:    OE_CLR   SDA
        LDR      R0, #8
rb:     LDR      R5, #QL
r1:     DECR     R5
        JNZ      r1
        OE_CLR   SCL
        WAIT_EDGE 6, high
        LDR      R5, #QH
r2:     DECR     R5
        JNZ      r2
        SHIFT_IN                    ; sample SDA
        OE_SET   SCL
        DECR     R0
        JNZ      rb
        ADDS     R5, R1, R1         ; Z=1 iff we ACK
        JNZ      rbn
        OE_SET   SDA
rbn:    LDR      R5, #QL
r3:     DECR     R5
        JNZ      r3
        OE_CLR   SCL
        WAIT_EDGE 6, high
        LDR      R5, #QH
r4:     DECR     R5
        JNZ      r4
        OE_SET   SCL
        OE_CLR   SDA
        SR_RD    R1
        LDR      R5, #0xFF
        AND      R1, R1, R5
        RET
