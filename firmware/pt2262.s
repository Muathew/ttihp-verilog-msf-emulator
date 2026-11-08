; PT2262-style remote control encoder (the 433 MHz OOK remotes of sockets, gates
; and alarms): 12 tri-state code bits and a sync bit, the code word sent WORDS times.
;   pin   : uio[0] to the transmitter's data input, push-pull, high = carrier on
;   table : trits, the 12 code bits A0 .. A11 (A6-A11 doubling as data D5-D0):
;           0, 1, or 2 for floating
;   Q     : the short pulse, 4 oscillator periods (4 alpha), in cycles: 4,200 = 350 us
;   done  : uo_out = DONE
; Sources: Princeton Technology PT2262 data sheet: a code bit is two pulse cycles of
; 16 alpha: bit 0 = high 4a, low 12a, high 4a, low 12a; bit 1 = high 12a, low 4a,
; high 12a, low 4a; floating = high 4a, low 12a, high 12a, low 4a; the sync bit =
; high 4a, low 124a; a code word is the 12 bits and the sync bit, sent at least four
; times while the key is down.
.include "msf.inc"
.equ OUT,   0x01
.equ Q,     4200
.equ WORDS, 4

start:  PIN_CLR  OUT
        OE_SET   OUT
        LDI      R7, Q
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
        LDR      R0, #WORDS
wd:     LDI      R6, trits
        LDR      R4, #12
tr:     LD       R1, R6
        INCR     R6
        LDI      R2, pat
        ADDS     R2, R2, R1
        LD       R3, R2             ; which of the four pulses are long
        SHL      R3, R3, #6
        SHL      R3, R3, #6         ; to bits 15-12
        LDR      R2, #4
pu:     WAIT_CMP R5
        PIN_TGL  OUT                ; high, low, high, low
        LDR      R1, #0
        SUBS     R1, R3, R1         ; N: a long one
        MOV      R1, R7
        JP       p_s
        ADDS     R1, R7, R7
        ADDS     R1, R1, R7
p_s:    ADDS     R5, R5, R1
        ADDS     R3, R3, R3
        DECR     R2
        JNZ      pu
        DECR     R4
        JNZ      tr
        WAIT_CMP R5                 ; the sync bit: high 4a, low 124a
        PIN_SET  OUT
        ADDS     R5, R5, R7
        WAIT_CMP R5
        PIN_CLR  OUT
        LDR      R2, #31
sy:     ADDS     R5, R5, R7         ; (the last piece is waited for by what follows)
        DECR     R2
        JZ       sy_end
        WAIT_CMP R5
        JMP      sy
sy_end: DECR     R0
        JNZ      wd
        WAIT_CMP R5
        UO_WI    DONE
halt:   JMP      halt

; a bit's four pulses, first at bit 3: 1 = long (12 alpha)
pat:    .word    0b0101, 0b1010, 0b0110
; address A0-A5 0 1 F 0 0 1, data D5-D0 (A6-A11) F F 0 1 F 0
trits:  .word    0, 1, 2, 0, 0, 1, 2, 2, 0, 1, 2, 0
