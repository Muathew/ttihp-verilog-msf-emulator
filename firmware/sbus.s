; S.BUS output (an RC receiver's serial bus to a flight controller): 16 channels of
; 11 bits and the flags, frames every 14 ms, every bit on the cycle counter.
;   pin   : uio[0], push-pull, the inverted line (idle low)
;   input : data RAM [0 .. 15] channels 1-16 (0-2047), [16] flags (bit 0 channel 17,
;           bit 1 channel 18, bit 2 frame lost, bit 3 failsafe)
;   FRAMES: how many frames; done: uo_out = DONE
; Sources: Futaba S.BUS as documented by PX4's and Betaflight's S.BUS drivers (the
; format is Futaba's and not published): 100,000 bit/s, 8 data bits LSB first, even
; parity, two stop bits, inverted; 25 bytes: 0x0F, 22 bytes holding the 16 channels'
; 11 bits each, LSB first and packed back to back, the flags byte, 0x00; a frame every
; 14 ms (7 ms in high-speed mode).
.include "msf.inc"
.equ OUT,    0x01
.equ BIT,    120                    ; 10 us
.equ PERIOD, 168000                 ; 14 ms
.equ FRAMES, 2
.equ FLAGS,  16
.equ BUF,    24                     ; RAM 24-48: the 25 bytes of a frame

start:  PIN_CLR  OUT                ; idle (the line inverted: a mark is low)
        OE_SET   OUT
        ; pack: 16 x 11 bits, LSB first, into BUF + 1 .. BUF + 22
        LDR      R6, #BUF
        LDR      R1, #0x0F
        ST       R6, R1
        INCR     R6
        LDR      R0, #0             ; R0: the channel
        LDR      R3, #0             ; R3: the byte being filled, R4: its next bit
        LDR      R4, #1
ch:     LD       R2, R0
        LDR      R7, #11
cb:     LDR      R1, #1
        AND      R1, R2, R1
        ADDS     R1, R1, R1
        JZ       cb0
        OR       R3, R3, R4
cb0:    SHR      R2, R2, #1
        ADDS     R4, R4, R4
        LDI      R1, 0x100
        SUBS     R1, R4, R1
        JNZ      cb_n
        ST       R6, R3             ; eight bits: a byte
        INCR     R6
        LDR      R3, #0
        LDR      R4, #1
cb_n:   DECR     R7
        JNZ      cb
        INCR     R0
        LDR      R1, #16
        SUBS     R1, R0, R1
        JNZ      ch
        LDR      R1, #FLAGS
        LD       R1, R1
        ST       R6, R1
        INCR     R6
        LDR      R1, #0x00
        ST       R6, R1
        ; send FRAMES frames, PERIOD apart
        LDR      R0, #FRAMES
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
fr:     LDR      R6, #BUF
        LDR      R7, #25
by:     LD       R3, R6
        INCR     R6
        MOV      R2, R3             ; even parity: fold the byte
        SHR      R1, R2, #4
        XOR      R2, R2, R1
        SHR      R1, R2, #2
        XOR      R2, R2, R1
        SHR      R1, R2, #1
        XOR      R2, R2, R1
        LDR      R1, #1
        AND      R2, R2, R1
        SHL      R2, R2, #7         ; the parity bit -> bit 9 of the frame
        SHL      R2, R2, #1
        OR       R3, R3, R2
        ADDS     R3, R3, R3         ; the start bit, 0
        LDI      R1, 0xC00          ; two stop bits
        OR       R3, R3, R1
        LDI      R1, 0xFFF          ; inverted
        XOR      R3, R3, R1
        LDR      R4, #12
bit:    LDR      R1, #1
        AND      R1, R3, R1
        WAIT_CMP R5
        PIN_W    R1
        SHR      R3, R3, #1
        LDR      R1, #BIT
        ADDS     R5, R5, R1
        DECR     R4
        JNZ      bit
        DECR     R7
        JNZ      by
        DECR     R0
        JZ       fin
        LDI      R1, 60000          ; the rest of the 14 ms: two waits of 60,000 and
        ADDS     R5, R5, R1         ; what is left
        WAIT_CMP R5
        ADDS     R5, R5, R1
        WAIT_CMP R5
        LDI      R1, PERIOD - 25 * 12 * BIT - 2 * 60000
        ADDS     R5, R5, R1
        JMP      fr
fin:    WAIT_CMP R5                 ; the last stop bit ends
        UO_WI    DONE
halt:   JMP      halt
