; Morse code keyer: keys a transmitter (or a buzzer driver) with a message held in
; the program, at a fixed speed, every element on the cycle counter.
;   pin   : the key uio[0], push-pull, high = key down (carrier on)
;   message: msg, one word a letter (see below), 0xFFFF = the end
;   speed : one unit = UNITC pieces of CHUNK cycles; 12 x 60,000 at 12 MHz = 60 ms (20 wpm)
; Sources: ITU-R M.1677-1 "International Morse code" (2009), part I §2: a dash is three
; dots; the space within a letter is one dot, between letters three dots, between
; words seven dots; part I §1.1 for the letters and figures. 20 words a minute is
; 60 ms a dot by the standard word PARIS (50 units).
; A letter is a word: its elements left-aligned from bit 15 (1 = dash), the number of
; elements in bits 2..0, bit 3 set when a word space follows it. Five elements at most
; are shifted out, so bits 3..0 never reach bit 15. (A space is not a word of its own:
; a run of them would be a loop with no wait in it, and the checker could not bound how
; far the deadline register falls behind.)
; How: every edge comes D pieces after the one before; each piece is a WAIT_CMP, so a
; unit of 60 ms is twelve deadlines 60,000 cycles apart and no wait is longer than the
; counter allows.
.include "msf.inc"
.equ KEY,    0x01
.equ CHUNK,  60000
.equ UNITC,  12                     ; pieces in a unit
.equ DOT,    UNITC
.equ DASH,   3 * UNITC
.equ GAP_EL, UNITC                  ; within a letter
.equ GAP_LT, 3 * UNITC - GAP_EL     ; added after a letter's last element
.equ GAP_WD, 7 * UNITC - 3 * UNITC  ; added for a word space
.equ END,    0xFFFF

start:  PIN_CLR  KEY
        OE_SET   KEY
        LDI      R7, CHUNK
        LDI      R6, msg
        CNT_R    R5
        LDR      R2, #UNITC         ; R2: pieces of quiet before the next key-down
next:   LD       R3, R6
        INCR     R6
        INCR     R3
        JZ       fin
        DECR     R3
        LDR      R4, #8
        AND      R0, R3, R4         ; R0: a word space after this letter
        LDR      R4, #7
        AND      R4, R3, R4         ; R4: elements left
el:     ; quiet: wait R2 pieces, then key down
qw:     ADDS     R5, R5, R7
        WAIT_CMP R5
        DECR     R2
        JNZ      qw
        PIN_SET  KEY
        LDR      R2, #0
        SUBS     R2, R3, R2         ; N: a dash
        LDR      R2, #DOT
        JP       el_on
        LDR      R2, #DASH
el_on:  ADDS     R5, R5, R7         ; key down for R2 pieces, then up
        WAIT_CMP R5
        DECR     R2
        JNZ      el_on
        PIN_CLR  KEY
        LDR      R2, #GAP_EL
        ADDS     R3, R3, R3
        DECR     R4
        JNZ      el
        LDR      R1, #GAP_LT        ; the letter ends: three units of quiet so far
        ADDS     R2, R2, R1
        ADDS     R0, R0, R0
        JZ       next
        LDR      R1, #GAP_WD        ; a word space: seven
        ADDS     R2, R2, R1
        JMP      next
fin:    ADDS     R5, R5, R7         ; the last space, then done
        WAIT_CMP R5
        DECR     R2
        JNZ      fin
        UO_WI    DONE
halt:   JMP      halt

; the letters, from ITU-R M.1677-1 part I §1.1
.equ L_C, (0b1010 << 12) | 4
.equ L_D, (0b100 << 13) | 3
.equ L_E, (0b0 << 15) | 1
.equ L_F, (0b0010 << 12) | 4
.equ L_M, (0b11 << 14) | 2
.equ L_Q, (0b1101 << 12) | 4
.equ L_S, (0b000 << 13) | 3
.equ L_6, (0b10000 << 11) | 5
.equ L_0, (0b11111 << 11) | 5
.equ SP,  8                         ; a word space after the letter
msg:    .word    L_C, L_Q | SP, L_D, L_E | SP, L_M, L_S, L_F | SP, L_6, L_0, END
