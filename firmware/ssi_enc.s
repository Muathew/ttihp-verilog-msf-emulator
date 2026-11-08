; SSI absolute encoder reader: clock a 25-bit Gray-code position (12 bits of turns,
; 13 bits a turn) out of the encoder, READS times, and convert it to binary on the fly.
;   pins  : CLK uio[0] (push-pull, idles high), DATA uio[1] (input, idles high)
;   output: data RAM [64 ..] per reading the position's top 9 bits then its low 16, in
;           binary; uo_out = DONE
; Sources: SSI as SICK-Stegmann and Kuebler describe it for their absolute encoders:
; the clock idles high; its first falling edge latches the position, each rising edge
; puts the next bit out, MSB first, and the master reads it on the falling edge after;
; after the last bit the encoder holds DATA low for the monoflop time (about 20 us)
; before it can be read again; the code is Gray, so a binary bit is the Gray bit
; XOR the binary bit before it.
.include "msf.inc"
.equ CLK,   0x01
.equ READS, 3
.equ GAP,   12000                   ; 1 ms between readings

start:  PIN_SET  CLK
        OE_SET   CLK
        LDR      R6, #64
        LDR      R0, #READS
rd:     LDR      R2, #0             ; R2: the last binary bit; R3:R4 the position
        LDR      R3, #0
        LDR      R4, #0
        PIN_CLR  CLK                ; the position is latched
        NOP
        NOP
        NOP
        NOP
        NOP
        PIN_SET  CLK                ; the MSB comes out
        NOP
        NOP
        NOP
        NOP
        LDR      R5, #25
bl:     PIN_CLR  CLK                ; read the bit that came out at the last rising edge
        PIN_R    R1
        SHR      R1, R1, #1
        LDR      R7, #1
        AND      R1, R1, R7         ; the Gray bit
        XOR      R2, R2, R1         ; the binary bit
        PIN_SET  CLK                ; the next bit comes out
        ADDS     R3, R3, R3         ; R3:R4 <<= 1, R2 in at the bottom
        SHR      R1, R4, #7
        SHR      R1, R1, #7
        SHR      R1, R1, #1
        OR       R3, R3, R1
        ADDS     R4, R4, R4
        OR       R4, R4, R2
        DECR     R5
        JNZ      bl
        ST       R6, R3
        INCR     R6
        ST       R6, R4
        INCR     R6
        DECR     R0
        JZ       fin
        CNT_R    R5                 ; the monoflop time and the gap
        LDI      R1, GAP
        ADDS     R5, R5, R1
        WAIT_CMP R5
        JMP      rd
fin:    UO_WI    DONE
halt:   JMP      halt
