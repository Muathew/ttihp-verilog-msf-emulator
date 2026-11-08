; Seven-segment display: count 0 to 9 on a common-cathode digit, one digit every
; STEP_P pieces of 60,000 cycles (10 ms at 12 MHz), every change on the counter.
;   pins  : segments a-g on uio[0..6], the decimal point on uio[7], high = lit
;   done  : uo_out = DONE (the display left showing 9)
; Sources: the segment patterns of the Code B font in Maxim's MAX7219 data sheet
; (Code B): 0 abcdef, 1 bc, 2 abdeg, 3 abcdg, 4 bcfg, 5 acdfg, 6 acdefg, 7 abc,
; 8 abcdefg, 9 abcdfg.
.include "msf.inc"
.equ PIECE,  60000
.equ STEP_P, 2

start:  PIN_WI   0x00
        OE_SET   0xFF
        LDI      R6, font
        LDR      R4, #10
        LDI      R7, PIECE
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
dg:     LD       R3, R6
        INCR     R6
        WAIT_CMP R5
        PIN_W    R3
        LDR      R2, #STEP_P
hold:   ADDS     R5, R5, R7         ; (the last piece is waited for by the next digit)
        DECR     R2
        JZ       h_end
        WAIT_CMP R5
        JMP      hold
h_end:  DECR     R4
        JNZ      dg
        WAIT_CMP R5
        UO_WI    DONE
halt:   JMP      halt

;             0     1     2     3     4     5     6     7     8     9
font:   .word 0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F
