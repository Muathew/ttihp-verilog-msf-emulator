; I2C master bus routines (open drain: OUT stays 0, OE = 1 pulls a line low), the
; ones firmware/i2c_master.s uses, for programs that talk to one I2C device.
; The including program defines SDA (uio[5] = 0x20), SCL (uio[6] = 0x40) and the
; quarter-bit delay counts QL and QH (9: about 100 kHz at 12 MHz), sets SER_CFG's
; input pin to SDA and starts with both lines released.
;   i2c_start, i2c_stop; i2c_wbyte (R1; Z = 1 if ACKed); i2c_rbyte (-> R1; Z = 1 on
;   entry for the last byte, NACKed). They use R0, R1 and R5, and R7 for the return.
; ---- START (from idle or after an ACK clock: SCL low) ----
i2c_start:
        OE_CLR   SDA
        LDR      R5, #QL
i2_s1:     DECR     R5
        JNZ      i2_s1
        OE_CLR   SCL
        WAIT_EDGE 6, high           ; honour clock stretching
        LDR      R5, #QH
i2_s2:     DECR     R5
        JNZ      i2_s2
        OE_SET   SDA                ; SDA falls while SCL is high
        LDR      R5, #QH
i2_s3:     DECR     R5
        JNZ      i2_s3
        OE_SET   SCL
        RET

; ---- STOP ----
i2c_stop:
        OE_SET   SDA
        LDR      R5, #QL
i2_p1:     DECR     R5
        JNZ      i2_p1
        OE_CLR   SCL
        WAIT_EDGE 6, high
        LDR      R5, #QH
i2_p2:     DECR     R5
        JNZ      i2_p2
        OE_CLR   SDA                ; SDA rises while SCL is high
        RET

; ---- write byte R1, MSB first; returns Z=1 iff the slave ACKed ----
; clobbers R0, R1, R5
i2c_wbyte:
        SWAP     R1, R1             ; byte -> bits 15..8
        LDR      R0, #8
i2_wb:     MOV      R5, R1
        INCR     R5                 ; N = bit 15 (low byte is 0, no carry)
        JN       i2_wb1
        OE_SET   SDA                ; 0: pull low
        JMP      i2_wbc
i2_wb1:    OE_CLR   SDA                ; 1: release
        NOP                         ; equal path lengths
i2_wbc:    LDR      R5, #QL
i2_w1:     DECR     R5
        JNZ      i2_w1
        OE_CLR   SCL
        WAIT_EDGE 6, high
        LDR      R5, #QH
i2_w2:     DECR     R5
        JNZ      i2_w2
        OE_SET   SCL
        SHL      R1, R1, #1
        DECR     R0
        JNZ      i2_wb
        OE_CLR   SDA                ; ACK clock: release SDA for the slave
        LDR      R5, #QL
i2_w3:     DECR     R5
        JNZ      i2_w3
        OE_CLR   SCL
        WAIT_EDGE 6, high
        LDR      R5, #QH
i2_w4:     DECR     R5
        JNZ      i2_w4
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
        JZ       i2_rb0
        LDR      R1, #0             ; ACK
i2_rb0:    OE_CLR   SDA
        LDR      R0, #8
i2_rb:     LDR      R5, #QL
i2_r1:     DECR     R5
        JNZ      i2_r1
        OE_CLR   SCL
        WAIT_EDGE 6, high
        LDR      R5, #QH
i2_r2:     DECR     R5
        JNZ      i2_r2
        SHIFT_IN                    ; sample SDA
        OE_SET   SCL
        DECR     R0
        JNZ      i2_rb
        ADDS     R5, R1, R1         ; Z=1 iff we ACK
        JNZ      i2_rbn
        OE_SET   SDA
i2_rbn:    LDR      R5, #QL
i2_r3:     DECR     R5
        JNZ      i2_r3
        OE_CLR   SCL
        WAIT_EDGE 6, high
        LDR      R5, #QH
i2_r4:     DECR     R5
        JNZ      i2_r4
        OE_SET   SCL
        OE_CLR   SDA
        SR_RD    R1
        LDR      R5, #0xFF
        AND      R1, R1, R5
        RET
