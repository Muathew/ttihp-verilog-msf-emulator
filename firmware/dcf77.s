; DCF77 time code: the output of a DCF77 receiver module (high while the carrier is
; lowered), carrying a date and time given in data RAM, for testing radio clocks and
; anything with a DCF77 input without the transmitter in Mainflingen.
;   pin   : uio[0], push-pull, high = second marker (carrier lowered)
;   input : data RAM [0] minute, [1] hour, [2] day, [3] day of the week (1 = Monday),
;           [4] month, [5] year (00-99), all BCD; [6] flags: bit 0 CEST (else CET),
;           bit 1 summer time announcement (A1), bit 2 leap second announcement (A2)
;   timing: a second is SEC_P pieces of PIECE cycles: 200 x 60,000 at 12 MHz
;   done  : uo_out = DONE
; Sources: PTB, "DCF77 time code" (ptb.de, Time dissemination, DCF77): at the start of
; each second the carrier is lowered for 0.1 s (a 0) or 0.2 s (a 1); no lowering in
; second 59 marks the minute; bit 0 = 0; bits 1-14 civil warnings and weather, bit 15
; the call bit, 16 A1, 17 Z1 and 18 Z2 (CEST: 10, CET: 01), 19 A2, 20 = 1 (start of
; time); minute 21-27 and its even parity 28, hour 29-34 and parity 35, day 36-41,
; day of the week 42-44, month 45-49, year 50-57, the date's even parity 58; BCD,
; least significant bit first.
; It starts with a lead-in, second 58 of the minute before (a 0) and the minute mark,
; so that a receiver is in step for the first frame, and ends with the next minute's
; second 0.
.include "msf.inc"
.equ OUT,    0x01
.equ PIECE,  60000
.equ SEC_P,  200
.equ ZERO_P, 20                     ; 0.1 s
.equ ONE_P,  40                     ; 0.2 s
.equ MARK,   0x100                  ; second 59 follows: no marker in it
.equ FLAGS,  6
.equ SYMS,   8                      ; the seconds: marker pieces | MARK; 0 = the end

start:  PIN_CLR  OUT
        OE_SET   OUT
        LDR      R6, #SYMS
        LDI      R1, ZERO_P | MARK  ; the lead-in
        ST       R6, R1
        INCR     R6
        LDR      R3, #0             ; bits 0-15: no warnings, no call
        LDR      R4, #16
        CALL     field
        LDR      R1, #FLAGS         ; bits 16-20: A1, Z1, Z2, A2, 1
        LD       R2, R1
        LDR      R1, #1
        AND      R4, R2, R1         ; CEST
        XOR      R0, R4, R1         ; CET
        SHL      R4, R4, #1         ; Z1
        SHL      R0, R0, #2         ; Z2
        OR       R4, R4, R0
        SHR      R3, R2, #1
        AND      R3, R3, R1         ; A1
        OR       R4, R4, R3
        LDR      R1, #4
        AND      R3, R2, R1         ; A2 (bit 2 -> bit 3)
        SHL      R3, R3, #1
        OR       R4, R4, R3
        LDR      R1, #16            ; start of time
        OR       R3, R4, R1
        LDR      R4, #5
        CALL     field
        LDR      R0, #0             ; minute and P1
        LDR      R1, #0
        LD       R3, R1
        LDR      R4, #7
        CALL     field
        MOV      R1, R0
        CALL     pbit
        LDR      R0, #0             ; hour and P2
        LDR      R1, #1
        LD       R3, R1
        LDR      R4, #6
        CALL     field
        MOV      R1, R0
        CALL     pbit
        LDR      R0, #0             ; the date and P3
        LDR      R1, #2
        LD       R3, R1
        LDR      R4, #6
        CALL     field
        LDR      R1, #3
        LD       R3, R1
        LDR      R4, #3
        CALL     field
        LDR      R1, #4
        LD       R3, R1
        LDR      R4, #5
        CALL     field
        LDR      R1, #5
        LD       R3, R1
        LDR      R4, #8
        CALL     field
        MOV      R1, R0
        CALL     pbit
        DECR     R6                 ; second 59 follows bit 58
        LD       R1, R6
        LDI      R2, MARK
        OR       R1, R1, R2
        ST       R6, R1
        INCR     R6
        LDR      R1, #ZERO_P        ; the next minute's second 0
        ST       R6, R1
        INCR     R6
        LDR      R1, #0
        ST       R6, R1
        ; play: each second, R2 pieces of low, then the marker
        LDI      R7, PIECE
        LDR      R6, #SYMS
        CNT_R    R5
        LDR      R2, #1
sym:    LD       R3, R6
        INCR     R6
        ADDS     R3, R3, R3
        JZ       fin
        SHR      R3, R3, #1
w1:     ADDS     R5, R5, R7
        WAIT_CMP R5
        DECR     R2
        JNZ      w1
        PIN_SET  OUT
        LDR      R4, #0xFF
        AND      R2, R3, R4         ; the marker's pieces
        LDI      R4, SEC_P
        SUBS     R4, R4, R2         ; the rest of the second
w2:     ADDS     R5, R5, R7
        WAIT_CMP R5
        DECR     R2
        JNZ      w2
        PIN_CLR  OUT
        LDI      R1, MARK
        AND      R1, R3, R1
        ADDS     R1, R1, R1
        JZ       nomark
        LDI      R1, SEC_P          ; and second 59
        ADDS     R4, R4, R1
nomark: MOV      R2, R4
        JMP      sym
fin:    UO_WI    DONE
halt:   JMP      halt

; R4 bits of R3, LSB first, as seconds; R0 ^= each bit (parity)
field:  MOV      R5, R7
fl:     LDR      R1, #1
        AND      R1, R3, R1
        CALL     pbit
        SHR      R3, R3, #1
        DECR     R4
        JNZ      fl
        MOV      R7, R5
        RET
; one second of bit R1 (0 or 1)
pbit:   LDR      R2, #ZERO_P
        ADDS     R1, R1, R1
        JZ       pb0
        LDR      R2, #ONE_P
        LDR      R1, #1
        XOR      R0, R0, R1
pb0:    ST       R6, R2
        INCR     R6
        RET
