; Margin finder, I2C: how short an SCL low or high does a device still follow?
; Addresses the device with one phase at p cycles, for each p in the sweep; it
; passes if the device ACKs. See margin.inc for the report.
;   pins  : SDA uio[5], SCL uio[6], open drain (pull-ups on the bus)
;   ADDR  : the device's 7-bit address (define)
;   SWEEP : 0 = sweep the SCL low, 1 = the SCL high (define)
;   TLOW, THIGH: the phase not swept, in cycles (define)
; Every edge is on the cycle counter, so a phase is p cycles exactly. Clock
; stretching is not followed: a stretching device reads as a fail. p must leave
; time for the bit loop, which msf_timing.py checks: SWEEP_LO is near its limit.
.equ SDA,  0x20
.equ SCL,  0x40
.equ ADDR, 0x50
.equ SWEEP, 0
.equ TLOW, 60
.equ THIGH, 60
.equ THOLD, 30
.equ SWEEP_LO, 24
.equ SWEEP_HI, 64
.equ NOM,  SWEEP_HI
.equ TMAX, SWEEP_HI + TLOW + THIGH
.include "margin.inc"

setup:  PIN_CLR  (SDA | SCL)       ; open drain: the pins only ever pull low
        OE_CLR   (SDA | SCL)
        RET

trial:  LDI      R6, TLOW
        LDI      R7, THIGH
        LDR      R1, #SWEEP
        ADDS     R1, R1, R1
        JZ       t_low
        MOV      R7, R3            ; sweep the high
        JMP      t_go
t_low:  MOV      R6, R3            ; sweep the low
t_go:   LDI      R0, THOLD
        LDI      R3, ADDR << 9     ; the address and a write bit, MSB first
        CNT_R    R5
        ADDS     R5, R5, R0        ; bus free, then the START
        WAIT_CMP R5
        OE_SET   SDA               ; SDA falls while SCL is high
        ADDS     R5, R5, R0
        WAIT_CMP R5
        OE_SET   SCL               ; SCL low
        LDR      R4, #8            ; bits to send
t_bit:  LDR      R1, #0
        SUBS     R1, R3, R1        ; N: the top bit
        JN       t_b1
        OE_SET   SDA               ; a 0: pull low
        JMP      t_b2
t_b1:   OE_CLR   SDA               ; a 1: release
t_b2:   ADDS     R3, R3, R3
        ADDS     R5, R5, R6        ; @range R6 SWEEP_LO TMAX
        WAIT_CMP R5
        OE_CLR   SCL               ; SCL rises
        ADDS     R5, R5, R7        ; @range R7 SWEEP_LO TMAX
        WAIT_CMP R5
        OE_SET   SCL               ; SCL falls
        DECR     R4
        JNZ      t_bit
        OE_CLR   SDA               ; the ACK bit: the device drives SDA
        ADDS     R5, R5, R6        ; @range R6 SWEEP_LO TMAX
        WAIT_CMP R5
        OE_CLR   SCL
        ADDS     R5, R5, R7        ; @range R7 SWEEP_LO TMAX
        WAIT_CMP R5
        PIN_R    R2                ; SDA at the end of the high: the ACK
        OE_SET   SCL
        OE_SET   SDA               ; the STOP: SDA low, SCL up, SDA up
        ADDS     R5, R5, R0
        WAIT_CMP R5
        OE_CLR   SCL
        ADDS     R5, R5, R0
        WAIT_CMP R5
        OE_CLR   SDA
        LDR      R1, #SDA
        AND      R2, R2, R1
        ADDS     R2, R2, R2        ; Z: SDA low, an ACK
        JZ       t_ok
        LDR      R1, #0
        JMP      tdone
t_ok:   LDR      R1, #1
        JMP      tdone
