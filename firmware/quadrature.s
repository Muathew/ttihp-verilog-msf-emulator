; Incremental encoder emulator: A and B in quadrature and a gated index pulse Z,
; moving as a table says (counts forward or back, at a rate each), every count on
; the cycle counter. For testing motor drives and encoder inputs.
;   pins  : A uio[0], B uio[1], Z uio[2], push-pull
;   table : moves, per move the counts (bit 15 set = backward) and the cycles a count;
;           0 = the end
;   CPR   : counts a revolution (a power of two): Z is high for the count where the
;           position is a multiple of CPR (A and B both low there)
;   done  : uo_out = DONE
; Sources: incremental encoder outputs as in Broadcom's HEDS-5500/5540 and US Digital's
; E5 data sheets: two channels 90 electrical degrees apart, A leading B for one
; direction; four counts a cycle when every edge is counted; the index once a
; revolution, gated to a quarter cycle where A and B are both low.
.include "msf.inc"
.equ CPR,   16
.equ BACK,  0x8000
.equ P_MIN, 100
.equ P_MAX, 60000

start:  PIN_CLR  0x07
        OE_SET   0x07
        LDI      R6, moves
        LDR      R0, #0             ; R0: the position
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
        PIN_SET  0x04               ; position 0: the index
mv:     LD       R2, R6
        INCR     R6
        LD       R3, R6
        INCR     R6
        LDR      R4, #1             ; R4: +1 or -1
        LDR      R1, #0
        SUBS     R1, R2, R1         ; Z: the end, N: backward
        JZ       fin
        JP       fwd
        LDI      R4, 0xFFFF
fwd:    SHL      R2, R2, #1         ; the count, without the direction bit
        SHR      R2, R2, #1
step:   ADDS     R5, R5, R3         ; @range R3 P_MIN P_MAX
        ADDS     R0, R0, R4
        LDR      R1, #3
        AND      R1, R0, R1         ; the phase
        SHR      R7, R1, #1
        XOR      R1, R1, R7         ; Gray code: 0, A, A+B, B
        LDR      R7, #CPR - 1
        AND      R7, R0, R7
        ADDS     R7, R7, R7
        JNZ      out
        LDR      R7, #0x04
        OR       R1, R1, R7
out:    WAIT_CMP R5
        PIN_W    R1
        DECR     R2
        JNZ      step
        JMP      mv
fin:    UO_WI    DONE               ; (the last edge is already out)
halt:   JMP      halt

; 40 counts forward at 20 kcount/s, 24 back at 10 k, 8 forward at 40 k
moves:  .word    40, 600
        .word    BACK | 24, 1200
        .word    8, 300
        .word    0, 0
