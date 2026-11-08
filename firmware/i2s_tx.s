; I2S audio transmitter (the chip as the clock master): 16-bit stereo, a sine on the
; left channel and a cosine on the right, from a table, every edge on the counter.
;   pins  : SCK uio[0], WS uio[1], SD uio[2], push-pull
;   HALF  : half an SCK period in cycles (30: SCK 200 kHz, 6.25 k stereo frames a second)
;   FRAMES: how many stereo frames (32 = two periods of the 16-point table)
;   done  : uo_out = DONE
; Sources: Philips Semiconductors, "I2S bus specification" (1986, revised 1996): SD MSB
; first, two's complement; the transmitter changes SD (and WS) on SCK's falling edge
; and the receiver takes it on the rising edge; WS = 0 for the left channel, 1 for the
; right, and it changes one clock period before the MSB of the word it announces.
.include "msf.inc"
.equ SCK,    0x01
.equ HALF,   30
.equ FRAMES, 32
.equ BUF,    16                     ; RAM 16..79: the stream, 2 words a frame

start:  PIN_WI   0x00
        OE_SET   0x07
        ; the stream first: left sin, right cos, frame after frame, at RAM BUF..
        LDR      R0, #0
        LDR      R6, #BUF
bld:    LDR      R1, #15
        AND      R1, R0, R1
        LDI      R2, sine
        ADDS     R2, R2, R1
        LD       R3, R2
        ST       R6, R3
        INCR     R6
        LDR      R1, #4
        ADDS     R1, R0, R1
        LDR      R2, #15
        AND      R1, R1, R2
        LDI      R2, sine
        ADDS     R2, R2, R1
        LD       R3, R2
        ST       R6, R3
        INCR     R6
        INCR     R0
        LDR      R1, #FRAMES
        SUBS     R1, R0, R1
        JNZ      bld
        ; play. WS must fall a slot before the left MSB: one slot of WS = 0, SD = 0
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_WI   0x00
        LDR      R1, #HALF
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_SET  SCK
        ADDS     R5, R5, R1
        LDR      R0, #BUF
wd:     LD       R3, R0             ; the next word
        INCR     R0
        LDR      R1, #1
        AND      R7, R0, R1         ; 1: a left word (BUF is even)
        XOR      R7, R7, R1
        SHL      R7, R7, #1         ; R7: this word's WS (0 left, 2 right)
        LDR      R4, #16
sl:     LDR      R1, #0
        SUBS     R1, R3, R1         ; N: this slot's bit
        LDR      R2, #0
        JP       s0
        LDR      R2, #0x04          ; SD
s0:     OR       R2, R2, R7
        LDR      R1, #1
        SUBS     R1, R4, R1
        JNZ      s1
        LDR      R1, #0x02          ; the last slot: the next word's WS already
        XOR      R2, R2, R1
s1:     WAIT_CMP R5
        PIN_W    R2                 ; SCK falls; SD and WS change
        LDR      R1, #HALF
        ADDS     R5, R5, R1
        ADDS     R3, R3, R3
        WAIT_CMP R5
        PIN_SET  SCK                ; the receiver takes it
        ADDS     R5, R5, R1
        DECR     R4
        JNZ      sl
        LDI      R1, BUF + 2 * FRAMES
        SUBS     R1, R0, R1
        JNZ      wd
        WAIT_CMP R5
        PIN_CLR  SCK
        UO_WI    DONE
halt:   JMP      halt

; round(16000 sin(2 pi k / 16)), as 16-bit two's complement
sine:   .word    0, 6123, 11314, 14782, 16000, 14782, 11314, 6123
        .word    0, 0x10000 - 6123, 0x10000 - 11314, 0x10000 - 14782, 0x10000 - 16000
        .word    0x10000 - 14782, 0x10000 - 11314, 0x10000 - 6123
