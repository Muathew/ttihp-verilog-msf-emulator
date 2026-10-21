; I2C target: the chip plays a 128-byte register device (24C01-style EEPROM)
; at a 7-bit address. Open drain: OUT stays 0 and OE pulls a line low.
;   pins   : SDA uio[5], SCL uio[6] (external pull-ups)
;   regs   : data RAM [0..127]
;   bus    : write  S, ADDR+W, A, pointer, A, data, A, data, A ... P
;            read   S, ADDR+R, A, data, A, data, A ... data, NA, P
;            data comes from / goes to the pointer, which increments after
;            each byte (mod 128); a repeated START may follow the pointer
;            (random read). Another address is not acknowledged and the
;            transfer is ignored. START or STOP anywhere restarts / ends.
;   ADDR   : 7-bit address (define, default 0x50)
;   STRETCH: 1 = hold SCL low while preparing an ACK or the next byte, which
;            makes 400 kHz work at a 12 MHz core; 0 = never stretch (default)
; Response times (core cycles from SCL falling at the pin to SDA at the pin;
; docs/instruments.md, measured by test_targets.py): see the table there.
.include "msf.inc"
.equ ADDR,    0x50
.equ STRETCH, 0
.equ SDAP,    5
.equ SCLP,    6
.equ SDA,     1 << SDAP
.equ SCL,     1 << SCLP
.equ HOLD,    SCL * STRETCH        ; SCL in an OE mask only when stretching

; R0 = SCL mask, R5 = SDA mask, R7 = SDA|SCL, R3 = register pointer,
; R2 = byte, R4 = bit count, R6 = both lines sampled at SCL high, R1 = scratch
start:
        PIN_WI   0x00
        OE_WI    0x00
        LDR      R1, #(7 | SDAP << 3)   ; SR in = SDA (SR out uio7, never shifted out)
        SER_CFG  R1
        LDR      R0, #SCL
        LDR      R5, #SDA
        LDR      R7, #(SDA | SCL)
        LDR      R3, #0
        LDR      R1, #0
        TMO      R1                ; no timeouts: an I2C target just waits
idle:   OE_CLR   (SDA | SCL)
iw:     WAIT_EDGE SDAP, fall       ; START = SDA falls while SCL is high
        PIN_R    R1
        AND      R1, R1, R0
        ADDS     R1, R1, R1
        JZ       iw                ; SCL low: just data
start_c:
        WAIT_EDGE SCLP, low

; ---- address byte ----
        LDR      R1, #0
        SR_LD    R1
        LDR      R4, #8
ab:     WAIT_EDGE SCLP, high
        SHIFT_IN                   ; SDA while SCL is high
        PIN_R    R6
        AND      R6, R6, R7        ; both lines at the sample
ap:     PIN_R    R1                ; watch both lines until SCL falls
        AND      R1, R1, R7
        SUBS     R2, R1, R6        ; (AND sets no flags)
        JZ       ap
        SUBS     R2, R1, R0        ; N = SCL fell
        JP       sdachg            ; SCL still high: SDA moved = START / STOP
        DECR     R4
        JNZ      ab
        OE_SET   HOLD              ; stretch first: the compare takes time
        SR_RD    R2
        LDR      R1, #(ADDR << 1)
        SUBS     R1, R2, R1        ; 0 = write to us, 1 = read from us
        JZ       a_w
        DECR     R1
        JZ       a_r
        JMP      idle              ; not us: no ACK, wait for the next START

; ---- write: pointer byte, then data bytes ----
a_w:    OE_SET   SDA               ; ACK
        OE_CLR   HOLD
        WAIT_EDGE SCLP, high       ; ACK clock
        WAIT_EDGE SCLP, low
        OE_CLR   SDA
        LDR      R1, #0
        SR_LD    R1
        LDR      R4, #8
pb:     WAIT_EDGE SCLP, high
        SHIFT_IN
        PIN_R    R6
        AND      R6, R6, R7
pp:     PIN_R    R1
        AND      R1, R1, R7
        SUBS     R2, R1, R6
        JZ       pp
        SUBS     R2, R1, R0
        JP       sdachg
        DECR     R4
        JNZ      pb
        OE_SET   (SDA | HOLD)      ; ACK the pointer
        SR_RD    R3
        LDR      R1, #0x7F
        AND      R3, R3, R1
        OE_CLR   HOLD
wd:     WAIT_EDGE SCLP, high       ; ACK clock
        WAIT_EDGE SCLP, low
        OE_CLR   SDA
        LDR      R1, #0
        SR_LD    R1
        LDR      R4, #8
db:     WAIT_EDGE SCLP, high
        SHIFT_IN
        PIN_R    R6
        AND      R6, R6, R7
dp:     PIN_R    R1
        AND      R1, R1, R7
        SUBS     R2, R1, R6
        JZ       dp
        SUBS     R2, R1, R0
        JP       sdachg
        DECR     R4
        JNZ      db
        OE_SET   (SDA | HOLD)      ; ACK the data byte
        SR_RD    R2
        ST       R3, R2
        INCR     R3
        LDR      R1, #0x7F
        AND      R3, R3, R1
        OE_CLR   HOLD
        JMP      wd

; ---- read: send bytes until the controller does not acknowledge ----
a_r:    OE_SET   SDA               ; ACK
        LD       R2, R3
        SWAP     R2, R2            ; byte in bits 15..8, low byte 0
        OE_CLR   HOLD
        WAIT_EDGE SCLP, high       ; ACK clock
        WAIT_EDGE SCLP, low
tx:     LDR      R4, #8
txb:    MOV      R1, R2
        INCR     R1                ; N = next bit (bit 15; low byte 0, no carry)
        JN       tx1
        OE_SET   SDA               ; 0: pull low
        JMP      txc
tx1:    OE_CLR   SDA               ; 1: release
        NOP
txc:    SHL      R2, R2, #1
        WAIT_EDGE SCLP, high
        WAIT_EDGE SCLP, low
        DECR     R4
        JNZ      txb
        OE_CLR   SDA               ; ACK clock: the controller's turn
        INCR     R3
        LDR      R1, #0x7F
        AND      R3, R3, R1
        LD       R2, R3
        SWAP     R2, R2            ; next byte, in case it asks for one
        WAIT_EDGE SCLP, high
        PIN_R    R6                ; SDA low = ACK
        WAIT_EDGE SCLP, low
        AND      R6, R6, R5
        ADDS     R6, R6, R6
        JZ       tx                ; ACK: next byte
        JMP      idle              ; NACK: done

; ---- SDA moved while SCL was high ----
sdachg: AND      R1, R6, R5        ; SDA before the change
        ADDS     R1, R1, R1
        JNZ      start_c           ; it fell: (repeated) START
        JMP      idle              ; it rose: STOP
