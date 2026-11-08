; RC servo driver, two channels: a frame every 20 ms, channel 1's pulse at the start
; of the frame and channel 2's 2.5 ms later (in turn, as an RC receiver puts them out),
; each pulse 1.0 to 2.0 ms long, from a table of positions.
;   pins  : servo 1 uio[0], servo 2 uio[1], push-pull, high = pulse
;   table : moves, per frame two pulse widths in cycles (12 a microsecond), 0 = the end
;   done  : uo_out = DONE after the last frame
; Sources: the de facto RC servo interface, as in Futaba's and Hitec's servo manuals
; and the Robotis/Pololu servo application notes: a positive pulse every 20 ms (50 Hz),
; 1.5 ms is the centre, 1.0 and 2.0 ms the ends of the normal travel.
.include "msf.inc"
.equ S1,     0x01
.equ S2,     0x02
.equ SLOT,   30000                  ; 2.5 ms: channel 2's pulse starts here
.equ PIECE,  52500                  ; the rest of the frame: four pieces
.equ W_MIN,  12000
.equ W_MAX,  24000

start:  PIN_CLR  S1 | S2
        OE_SET   S1 | S2
        LDI      R6, moves
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
frame:  LD       R2, R6             ; channel 1's width (0: the end)
        INCR     R6
        LD       R3, R6
        INCR     R6
        ADDS     R2, R2, R2
        JZ       fin
        SHR      R2, R2, #1
        MOV      R0, R5             ; R0: the frame's start; every edge is timed from it
        WAIT_CMP R5
        PIN_SET  S1
        ADDS     R5, R0, R2         ; @range R2 W_MIN W_MAX
        WAIT_CMP R5
        PIN_CLR  S1
        LDI      R1, SLOT
        ADDS     R0, R0, R1         ; R0: channel 2's start
        WAIT_CMP R0
        PIN_SET  S2
        ADDS     R5, R0, R3         ; @range R3 W_MIN W_MAX
        WAIT_CMP R5
        PIN_CLR  S2
        LDI      R1, PIECE          ; the next frame: 2.5 ms + 4 pieces after this one
        ADDS     R5, R0, R1
        LDR      R4, #3
rest:   WAIT_CMP R5
        ADDS     R5, R5, R1
        DECR     R4
        JNZ      rest
        JMP      frame
fin:    WAIT_CMP R5
        UO_WI    DONE
halt:   JMP      halt

; full travel one way, centre, the other way, a quarter
moves:  .word    12000, 24000
        .word    18000, 18000
        .word    24000, 12000
        .word    15000, 21000
        .word    0, 0
