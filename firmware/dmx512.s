; DMX512 controller (a lighting console's output): packets of the NULL start code
; and 512 slots, back to back, every bit on the cycle counter.
;   pin   : the line uio[0] (to an RS-485 driver), idle high (mark)
;   input : data RAM [0] = n, how many channels are given; [1 .. n] = the levels of
;           channels 1 .. n (one byte a word); channels n+1 .. 512 are sent as 0
;   PACKETS: how many packets; MTBF: mark time between slots, in cycles (0: none)
;   done  : uo_out = DONE
; Sources: ANSI E1.11-2008 (USITT DMX512-A) §8 and Table 6 (transmitter timing):
; 250 kbit/s, a slot is a start bit, 8 data bits LSB first and two stop bits (44 us);
; a break of 92 us or more (176 us here), a mark after break of 12 us or more, the
; NULL start code (0), up to 512 slots; the mark between slots and before the
; next break may be 0 to 1 s; break to break at least 1204 us.
.include "msf.inc"
.equ DMX,     0x01
.equ BIT,     48                    ; 4 us
.equ BRK,     2112                  ; 176 us
.equ MAB,     144                   ; 12 us
.equ SC,      0x00                  ; the NULL start code
.equ SLOTS,   512
.equ PACKETS, 2
.equ MTBF,    0

start:  PIN_WI   DMX                ; mark
        OE_SET   DMX
        LDR      R6, #BIT
        LDR      R0, #PACKETS
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
pkt:    WAIT_CMP R5
        PIN_CLR  DMX                ; break
        LDI      R1, BRK
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_SET  DMX                ; mark after break
        LDI      R1, MAB
        ADDS     R5, R5, R1
        LDR      R2, #0             ; R2: the slot (0: the start code)
slot:   LDR      R3, #SC
        ADDS     R1, R2, R2
        JZ       frame
        LDR      R3, #0             ; a channel past n: 0
        LDR      R1, #0
        LD       R7, R1
        SUBS     R7, R7, R2         ; N: past n
        JN       frame
        LD       R3, R2
frame:  ADDS     R3, R3, R3         ; the start bit, 0
        LDI      R1, 0x600          ; the two stop bits
        OR       R3, R3, R1
        LDR      R4, #11
bit:    LDR      R1, #1
        AND      R7, R3, R1
        WAIT_CMP R5
        PIN_W    R7
        SHR      R3, R3, #1
        ADDS     R5, R5, R6
        DECR     R4
        JNZ      bit
        LDI      R1, MTBF           ; (after the last slot: the mark before the break)
        ADDS     R5, R5, R1
        INCR     R2
        LDI      R1, SLOTS + 1
        SUBS     R1, R2, R1
        JNZ      slot
        DECR     R0                 ; the next break starts as the stop bits end
        JNZ      pkt
        WAIT_CMP R5
        UO_WI    DONE
halt:   JMP      halt
