; NES controller reader (the console's side): latch the buttons, clock them out of the
; pad's 4021 shift register, POLLS times, a frame (16.7 ms) apart.
;   pins  : LATCH uio[0], CLK uio[1] (push-pull, CLK idles high), DATA uio[2] (input)
;   output: data RAM [64 ..] each poll's byte, A first (bit 7) to Right (bit 0), a 0 for
;           a pressed button; uo_out = DONE
; Sources: NESdev wiki, "Standard controller": a 12 us latch pulse loads the buttons
; into a CD4021; A is then on DATA; each clock pulse (low, then high: the 4021 shifts on
; the rising edge) brings the next: B, Select, Start, Up, Down, Left, Right; a pressed
; button reads 0; the console reads once a frame.
.include "msf.inc"
.equ LATCH,  0x01
.equ CLK,    0x02
.equ T_LAT,  144                    ; 12 us
.equ T_HALF, 72                     ; 6 us
.equ POLLS,  3
.equ FRAME_P, 4                     ; 4 x 50,000: 16.7 ms
.equ PIECE,  50000

start:  PIN_WI   CLK
        OE_SET   LATCH | CLK
        LDR      R1, #(7 | 2 << 3)  ; SR input = DATA (pin 2), MSB first
        SER_CFG  R1
        LDR      R6, #64
        LDR      R0, #POLLS
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
poll:   MOV      R4, R5             ; R4: this poll's start
        WAIT_CMP R5
        PIN_SET  LATCH
        LDR      R1, #T_LAT
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  LATCH              ; A is on DATA
        LDR      R1, #T_HALF
        ADDS     R5, R5, R1
        LDR      R3, #8
bit:    WAIT_CMP R5
        PIN_CLR  CLK                ; a clock pulse: read (DATA settled since the last edge)
        SHIFT_IN
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_SET  CLK                ; the pad shifts the next button out
        ADDS     R5, R5, R1
        DECR     R3
        JNZ      bit
        SR_RD    R2
        LDR      R1, #0xFF
        AND      R2, R2, R1
        ST       R6, R2
        INCR     R6
        DECR     R0
        JZ       fin
        LDR      R3, #FRAME_P       ; the next frame: PIECE x FRAME_P after this poll began
        LDI      R1, PIECE
        MOV      R5, R4
fw:     ADDS     R5, R5, R1
        DECR     R3
        JZ       poll
        WAIT_CMP R5
        JMP      fw
fin:    UO_WI    DONE
halt:   JMP      halt
