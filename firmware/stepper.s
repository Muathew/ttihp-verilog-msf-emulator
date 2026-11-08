; Stepper motor moves through a STEP/DIR driver, with a computed acceleration ramp:
; up, cruise, and the same ramp backwards down; then back half way.
;   pins  : STEP uio[0], DIR uio[1] (1 = forward), push-pull
;   STEPS : the first move (forward); the second goes back STEPS / 2
;   C0, C_MIN: the first step interval and the shortest, in cycles
;   done  : uo_out = DONE
; Sources: the ramp is D. Austin's recurrence ("Generate stepper-motor speed profiles
; in real time", Embedded Systems Programming, January 2005): c(n) = c(n-1) -
; 2 c(n-1) / (4n + 1), integer division here, until c reaches C_MIN; deceleration is
; the same intervals in reverse. The driver's timing is Allegro's A4988 data sheet:
; STEP high and low at least 1 us (2 us high here), DIR set up 200 ns before STEP's
; rising edge and held 200 ns after.
.include "msf.inc"
.equ STEP,  0x01
.equ DIR,   0x02
.equ STEPS, 100
.equ C0,    24000                   ; 2 ms
.equ C_MIN, 3000                    ; 250 us: 4,000 steps a second
.equ PW,    24                      ; 2 us
.equ RAMP,  16                      ; RAM 16..: the ramp's intervals
.equ R_MAX, 48                      ; at most this many
.equ PAUSE, 60000

start:  PIN_CLR  STEP | DIR
        OE_SET   STEP | DIR
        ; the ramp: c(0) = C0, c(n) = c(n-1) - 2 c(n-1) / (4n + 1)
        LDI      R0, C0             ; R0: c
        LDR      R1, #RAMP
        LDR      R2, #0
        ST       R2, R1             ; RAM 0: where the next interval goes
        LDR      R2, #1
        LDR      R1, #1
        ST       R1, R2             ; RAM 1: n
rp:     LDR      R1, #0
        LD       R2, R1
        ST       R2, R0             ; the interval
        INCR     R2
        ST       R1, R2
        LDI      R1, C_MIN
        SUBS     R1, R0, R1
        JN       rp_end             ; reached C_MIN (the last one may be under it)
        JZ       rp_end
        LDR      R1, #RAMP + R_MAX
        SUBS     R1, R2, R1
        JZ       rp_end
        ADDS     R1, R0, R0         ; 2c
        LDR      R2, #1
        LD       R2, R2             ; n
        SHL      R2, R2, #2
        INCR     R2                 ; 4n + 1
        CALL     div
        SUBS     R0, R0, R3
        LDR      R2, #1
        LD       R1, R2
        INCR     R1
        ST       R2, R1
        JMP      rp
rp_end: LDR      R1, #0
        LD       R2, R1
        LDR      R1, #RAMP
        SUBS     R2, R2, R1
        LDR      R1, #2
        ST       R1, R2             ; RAM 2: the ramp's length
        ; the moves
        PIN_SET  DIR
        LDR      R0, #STEPS
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
        LDR      R6, #2             ; two moves
mv:     ; R0 steps: k = min(ramp, R0 / 2) up, R0 - 2k cruise, k down
        LDR      R1, #2
        LD       R4, R1
        SHR      R1, R0, #1
        SUBS     R2, R1, R4
        JP       k_ok
        MOV      R4, R1
k_ok:   SUBS     R0, R0, R4
        SUBS     R0, R0, R4         ; R0: the cruise steps
        LDR      R2, #RAMP          ; up
        MOV      R7, R4
up:     LD       R3, R2
        INCR     R2
        WAIT_CMP R5
        PIN_SET  STEP
        LDR      R1, #PW
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  STEP
        SUBS     R3, R3, R1
        ADDS     R5, R5, R3         ; @range R3 C_MIN-PW-2000 C0-PW
        DECR     R7
        JNZ      up
        DECR     R2                 ; cruise at the ramp's last interval
        LD       R3, R2
        ADDS     R0, R0, R0
        JZ       down0
        SHR      R0, R0, #1
cr:     WAIT_CMP R5
        PIN_SET  STEP
        LDR      R1, #PW
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  STEP
        SUBS     R1, R3, R1
        ADDS     R5, R5, R1         ; @range R1 C_MIN-PW-2000 C0-PW
        DECR     R0
        JNZ      cr
down0:  MOV      R7, R4             ; down: the ramp backwards
dn:     LD       R3, R2
        DECR     R2
        WAIT_CMP R5
        PIN_SET  STEP
        LDR      R1, #PW
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  STEP
        SUBS     R3, R3, R1
        ADDS     R5, R5, R3         ; @range R3 C_MIN-PW-2000 C0-PW
        DECR     R7
        JNZ      dn
        DECR     R6
        JZ       fin
        WAIT_CMP R5                 ; the last interval ends; stop, turn round, go
        LDI      R1, PAUSE          ; back half way
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  DIR
        ADDS     R5, R5, R1
        LDR      R0, #STEPS >> 1
        JMP      mv
fin:    WAIT_CMP R5
        UO_WI    DONE
halt:   JMP      halt

; R3 = R1 / R2, unsigned (R4: the remainder); uses R5, R6 (saved), R1
div:    LDR      R4, #3
        ST       R4, R5
        INCR     R4
        ST       R4, R6
        LDR      R3, #0
        LDR      R4, #0
        LDR      R5, #16
dv:     ADDS     R3, R3, R3
        ADDS     R4, R4, R4
        LDR      R6, #0
        SUBS     R6, R1, R6         ; N: the dividend's top bit
        JP       dv0
        INCR     R4
dv0:    ADDS     R1, R1, R1
        SUBS     R6, R4, R2
        JN       dv1
        MOV      R4, R6
        INCR     R3
dv1:    DECR     R5
        JNZ      dv
        LDR      R4, #3
        LD       R5, R4
        INCR     R4
        LD       R6, R4
        RET
