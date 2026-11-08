; DALI control device (IEC 62386): forward frames to lighting control gear, every
; half bit on the cycle counter.
;   pin   : uio[0] to the bus interface, push-pull, high = bus idle (powered)
;   table : frames, one 16-bit forward frame a word (address byte, data byte);
;           0xFFFF = the end
;   done  : uo_out = DONE
; Sources: IEC 62386-101/-102 as summarised in the DALI AG (DiiA) "DALI manual" and
; NXP's AN10760 ("DALI slave using ..."): 1200 bit/s, bi-phase: a 1 is low then high,
; a 0 high then low, each half 416.67 us (Te); a forward frame is a start bit (1),
; the address byte and the data byte, MSB first, then two stop bits of idle; at least
; 22 Te between forward frames; address byte 0AAAAAAS (short address A, S = 1 for a
; command) or 1111111S (broadcast); DAPC (S = 0) sets the arc power level; commands
; 0 OFF, 5 RECALL MAX LEVEL, 144 QUERY STATUS.
.include "msf.inc"
.equ BUS,    0x01
.equ TE,     5000                   ; 416.67 us
.equ SETTLE, 60000                  ; two of these after the last bit: 24 Te (22 or more)
.equ END,    0xFFFF

start:  PIN_SET  BUS                ; idle
        OE_SET   BUS
        LDI      R6, frames
        LDI      R7, TE
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
fr:     LD       R3, R6
        INCR     R6
        INCR     R3
        JZ       fin
        DECR     R3
        LDR      R4, #17            ; the start bit and 16 bits
        LDR      R2, #1             ; R2: the bit (the start bit is a 1)
bit:    LDR      R1, #1
        XOR      R1, R2, R1         ; the first half: the bit inverted
        WAIT_CMP R5
        PIN_W    R1
        ADDS     R5, R5, R7
        WAIT_CMP R5
        PIN_W    R2                 ; the second half: the bit
        ADDS     R5, R5, R7
        LDR      R2, #0             ; the next bit: bit 15 of R3
        SUBS     R2, R3, R2
        LDR      R2, #0
        JP       b0
        LDR      R2, #1
b0:     ADDS     R3, R3, R3
        DECR     R4
        JNZ      bit
        WAIT_CMP R5                 ; back to idle: the stop bits and the settling time
        PIN_SET  BUS
        LDI      R1, SETTLE
        ADDS     R5, R5, R1
        WAIT_CMP R5
        ADDS     R5, R5, R1
        JMP      fr
fin:    UO_WI    DONE
halt:   JMP      halt

; broadcast DAPC 254; gear 5: RECALL MAX LEVEL; broadcast OFF; gear 5: QUERY STATUS
frames: .word    0xFEFE, 0x0B05, 0xFF00, 0x0B90
        .word    END
