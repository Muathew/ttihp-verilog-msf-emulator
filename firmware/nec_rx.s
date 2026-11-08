; NEC infrared receiver: decode the output of an IR receiver module (low while the
; 38 kHz carrier is seen): a frame, then repeat codes, until EVENTS have been seen.
;   pin   : uio[0], an input (the module's open-collector output, pulled up)
;   output: data RAM [64] the address, [65] the command, [66] how many repeat codes;
;           uo_out = DONE, or BAD for anything out of tolerance or a byte whose
;           complement does not match
; Sources: NEC infrared protocol as described by SB-Projects and Vishay's "Data
; Formats for IR Remote Control": a 9 ms burst and a 4.5 ms space lead a frame (2.25 ms
; for a repeat code, then one 562.5 us burst); bits are a 562.5 us burst and a
; 562.5 us (0) or 1687.5 us (1) space; address, ~address, command, ~command, LSB first.
; Long lows and spaces are timed in steps of 0.5 ms (the 16-bit counter wraps after
; 5.46 ms): a level wait with a 0.5 ms time-out, counted; the windows are +-10 % or
; wider; bits are timed with the counter itself.
.include "msf.inc"
.equ LV1,    0x50                   ; WAIT_EDGE on uio[0]: level 1
.equ LV0,    0x40                   ; level 0
.equ RISE,   0x10
.equ FALL,   0x20
.equ STEP,   6000                   ; 0.5 ms
.equ BIT_MAX, 30000                 ; 2.5 ms: no bit space is longer
.equ ONE_MIN, 13500                 ; 1.125 ms: between a 0 and a 1
.equ EVENTS, 2                      ; the frame and one repeat
.equ BAD,    0xEE

start:  OE_CLR   0x01
        LDR      R1, #0
        LDR      R2, #66
        ST       R2, R1             ; no repeats yet
        LDR      R6, #EVENTS
ev:     LDR      R1, #0             ; wait as long as it takes for a burst
        TMO      R1
        WAIT_EDGE #LV1
        WAIT_EDGE #FALL
        LDI      R1, STEP           ; the leader's burst: 8.1-9.9 ms = 16-19 steps
        TMO      R1
        LDR      R3, #0
lo:     WAIT_EDGE #LV1
        JNZ      lo_end
        INCR     R3
        JMP      lo
lo_end: LDR      R1, #16
        SUBS     R1, R3, R1
        JN       bad
        LDR      R1, #20
        SUBS     R1, R3, R1
        JP       bad
        LDR      R3, #0             ; the space: 4.05-4.95 ms (8-9 steps): a frame;
sp:     WAIT_EDGE #LV0              ; 2.0-2.5 ms (4 steps): a repeat (a level wait:
                                    ;  an edge between two waits is not lost)
        JNZ      sp_end
        INCR     R3
        JMP      sp
sp_end: LDR      R1, #4
        SUBS     R1, R3, R1
        JZ       rep
        LDR      R1, #8
        SUBS     R1, R3, R1
        JN       bad
        LDR      R1, #10
        SUBS     R1, R3, R1
        JP       bad
        ; 32 bits, LSB first, into R4 (low word) and R5 (high word)
        LDI      R1, BIT_MAX
        TMO      R1
        LDR      R0, #32
bt:     WAIT_EDGE #RISE             ; the bit's burst ends
        JZ       bad
        CNT_R    R2
        WAIT_EDGE #FALL             ; the next burst starts: the space is the bit
        JZ       bad
        CNT_R    R3
        SUBS     R3, R3, R2
        LDI      R1, ONE_MIN
        SUBS     R3, R3, R1         ; N: a 0
        SHR      R4, R4, #1         ; R5:R4 >>= 1, the new bit in at the top
        LDR      R1, #1
        AND      R1, R5, R1
        SHL      R1, R1, #7
        SHL      R1, R1, #7
        SHL      R1, R1, #1
        OR       R4, R4, R1
        SHR      R5, R5, #1
        LDR      R1, #0
        SUBS     R1, R3, R1
        JN       bt0
        LDI      R1, 0x8000
        OR       R5, R5, R1
bt0:    DECR     R0
        JNZ      bt
        ; R4 = ~address << 8 | address, R5 = ~command << 8 | command
        SWAP     R1, R4
        LDI      R2, 0xFF
        XOR      R1, R1, R4
        AND      R1, R1, R2
        SUBS     R1, R1, R2         ; address ^ ~address must be 0xFF
        JNZ      bad
        SWAP     R1, R5
        XOR      R1, R1, R5
        AND      R1, R1, R2
        SUBS     R1, R1, R2
        JNZ      bad
        AND      R4, R4, R2
        AND      R5, R5, R2
        LDR      R1, #64
        ST       R1, R4
        INCR     R1
        ST       R1, R5
        JMP      ev_n
rep:    LDI      R1, BIT_MAX        ; (longer than the 0.5 ms steps)
        TMO      R1
        WAIT_EDGE #RISE             ; the repeat code's 562.5 us burst
        JZ       bad
        LDR      R2, #66
        LD       R1, R2
        INCR     R1
        ST       R2, R1
ev_n:   DECR     R6
        JNZ      ev
        UO_WI    DONE
        JMP      halt
bad:    UO_WI    BAD
halt:   JMP      halt
