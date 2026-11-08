; Wiegand 26-bit (H10301): the chip is a card reader sending a card to a panel.
;   pins  : D0 uio[0], D1 uio[1], push-pull, idle high: a 0 is a low pulse on D0,
;           a 1 a low pulse on D1
;   FAC, CARD: facility code (8 bits) and card number (16 bits)
;   PULSE, PERIOD: pulse width and bit period in cycles (50 us and 1 ms at 12 MHz)
;   FRAMES: how many times the card is sent, GAPF cycles apart
; Sources: HID, "Understanding Card Data Formats" (H10301 26-bit: bit 1 even parity
; over bits 2-13, bits 2-9 facility code, bits 10-25 card number, bit 26 odd parity
; over bits 14-25, sent bit 1 first); the SIA AC-01 Wiegand interface (pulse width
; 20-100 us, pulse interval 200 us to 20 ms).
.include "msf.inc"
.equ D0, 0x01
.equ D1, 0x02
.equ FAC, 0x5A
.equ CARD, 0x04D2
.equ PULSE, 600
.equ PERIOD, 12000
.equ GAPF, 60000                   ; 5 ms between frames
.equ FRAMES, 2

start:  PIN_WI   (D0 | D1)
        OE_SET   (D0 | D1)
        ; even parity over FAC and CARD[15:12]; odd parity over CARD[11:0]
        LDI      R3, (FAC << 4) | (CARD >> 12)
        LDR      R4, #12
        CALL     parity            ; R2 = parity of R3's 12 low bits
        MOV      R6, R2            ; even parity: the bit that makes the ones even
        LDI      R3, CARD & 0x0FFF
        LDR      R4, #12
        CALL     parity
        LDR      R1, #1
        XOR      R0, R2, R1        ; odd parity
        ; the frame: W1 = EP, FAC, CARD[15:9] (16 bits); W2 = CARD[8:0], OP (10 bits)
        SHL      R6, R6, #7
        SHL      R6, R6, #7
        SHL      R6, R6, #1        ; EP in bit 15
        LDI      R1, (FAC << 7) | (CARD >> 9)
        OR       R6, R6, R1
        LDR      R2, #8
        ST       R2, R6
        SHL      R0, R0, #6        ; OP in bit 6
        LDI      R1, (CARD & 0x1FF) << 7
        OR       R0, R0, R1
        LDR      R2, #9
        ST       R2, R0
        LDR      R6, #FRAMES
        CNT_R    R5
        LDI      R1, 1200
        ADDS     R5, R5, R1        ; the first bit 100 us from now
frame:  LDR      R2, #8
        LD       R3, R2            ; bits 1-16, first at bit 15
        LDR      R2, #9
        LD       R0, R2            ; bits 17-26, first at bit 15
        LDR      R4, #26
bit:    LDR      R2, #0
        SUBS     R2, R3, R2        ; N: the bit (WAIT_CMP leaves N alone)
        WAIT_CMP R5
        JN       b_1
        PIN_CLR  D0                ; a 0
        JMP      b_p
b_1:    PIN_CLR  D1                ; a 1
b_p:    LDI      R1, PULSE
        ADDS     R2, R5, R1
        WAIT_CMP R2
        PIN_SET  (D0 | D1)
        LDI      R1, PERIOD
        ADDS     R5, R5, R1
        ADDS     R3, R3, R3        ; shift the 26 bits on, R0's top into R3
        SHR      R1, R0, #7
        SHR      R1, R1, #7
        SHR      R1, R1, #1
        OR       R3, R3, R1
        ADDS     R0, R0, R0
        DECR     R4
        JNZ      bit
        WAIT_CMP R5                ; the end of the last bit period, then the gap
        LDI      R1, GAPF
        ADDS     R5, R5, R1
        DECR     R6
        JNZ      frame
        UO_WI    DONE
halt:   JMP      halt

; R2 <- XOR of R3's R4 low bits. Uses R1, R3, R4.
parity: LDR      R2, #0
p_l:    LDR      R1, #1
        AND      R1, R3, R1
        XOR      R2, R2, R1
        SHR      R3, R3, #1
        DECR     R4
        JNZ      p_l
        RET
