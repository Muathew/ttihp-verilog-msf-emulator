; SENT (SAE J2716) sensor transmitter: frames of two 12-bit fast-channel signals, as
; a pressure or position sensor sends them to an engine controller.
;   pin   : uio[0], push-pull, idle high (the sensor's output stage)
;   table : frames, per frame the status nibble, signal 1 and signal 2 (12 bits each);
;           0xFFFF = the end
;   tick  : TICK cycles (36 = 3 us at 12 MHz, the standard's shortest)
;   done  : uo_out = DONE
; Sources: SAE J2716 (SENT) as described in the SENT application notes of NXP and
; Infineon (the standard itself is SAE's, not public): every nibble starts
; with a falling edge and a low of at least 4 ticks (5 here), and lasts 12 + value
; ticks; a frame is the 56-tick synchronisation pulse, the status nibble, six data
; nibbles (here signal 1 then signal 2, most significant nibble first) and a CRC
; nibble; the CRC is x^4 + x^3 + x^2 + 1 with seed 0101 over the data nibbles,
; followed by one more nibble of 0 (the recommended form); no pause pulse.
.include "msf.inc"
.equ SENT,  0x01
.equ TICK,  36
.equ LOW,   5 * TICK
.equ H_MIN, 7 * TICK                ; a nibble of 0: 12 ticks, 5 low
.equ H_MAX, 51 * TICK               ; the sync pulse: 56 ticks, 5 low
.equ END,   0xFFFF
.equ SCR,   0                       ; RAM 0-7: the frame's nibbles, then the 0
.equ SYM,   16                      ; RAM 16..: each nibble's high time

start:  PIN_SET  SENT
        OE_SET   SENT
        LDI      R6, frames
        LDR      R0, #SYM
bf:     LD       R1, R6             ; the status nibble (END: done building)
        INCR     R6
        INCR     R1
        JZ       play0
        DECR     R1
        LDR      R7, #SCR
        ST       R7, R1
        LDR      R4, #2             ; two signals, three nibbles each
sig:    LD       R3, R6
        INCR     R6
        INCR     R7
        SHR      R1, R3, #4
        SHR      R1, R1, #4
        ST       R7, R1             ; bits 11-8
        INCR     R7
        SHR      R1, R3, #4
        LDR      R2, #0x0F
        AND      R1, R1, R2
        ST       R7, R1             ; bits 7-4
        INCR     R7
        AND      R1, R3, R2
        ST       R7, R1             ; bits 3-0
        DECR     R4
        JNZ      sig
        INCR     R7
        LDR      R1, #0
        ST       R7, R1             ; SCR + 7: the augmenting 0
        ; the CRC over SCR + 1 .. SCR + 7, four bits a nibble, MSB first
        LDR      R2, #5
        LDR      R7, #SCR + 1
cn:     LD       R1, R7
        LDR      R4, #4
cb:     SHR      R5, R2, #3         ; the bit about to leave
        SHL      R2, R2, #1
        SHR      R3, R1, #3         ; the next message bit
        OR       R2, R2, R3
        LDR      R3, #0x0F
        AND      R2, R2, R3
        SHL      R1, R1, #1
        AND      R1, R1, R3
        ADDS     R5, R5, R5
        JZ       cb_n
        LDR      R3, #0x0D          ; x^4 + x^3 + x^2 + 1, less x^4
        XOR      R2, R2, R3
cb_n:   DECR     R4
        JNZ      cb
        INCR     R7
        LDR      R1, #SCR + 8
        SUBS     R1, R7, R1
        JNZ      cn
        LDR      R7, #SCR + 7
        ST       R7, R2             ; the CRC takes the 0's place
        ; each nibble's high time: (12 + v - 5) ticks; the sync pulse first
        LDI      R1, H_MAX
        ST       R0, R1
        INCR     R0
        LDR      R7, #SCR
em:     LD       R1, R7
        LDR      R2, #7
        ADDS     R1, R1, R2
        SHL      R2, R1, #5         ; x 36 = x 32 + x 4
        SHL      R1, R1, #2
        ADDS     R1, R1, R2
        ST       R0, R1
        INCR     R0
        INCR     R7
        LDR      R1, #SCR + 8
        SUBS     R1, R7, R1
        JNZ      em
        JMP      bf
        ; play: a falling edge, LOW cycles low, the rest high
play0:  MOV      R2, R0             ; R2: the end of the list
        LDR      R6, #SYM
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
pl:     LD       R3, R6
        INCR     R6
        WAIT_CMP R5
        PIN_CLR  SENT
        LDR      R1, #LOW
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_SET  SENT
        ADDS     R5, R5, R3         ; @range R3 H_MIN H_MAX
        SUBS     R1, R6, R2
        JNZ      pl
        WAIT_CMP R5                 ; the last nibble ends
        UO_WI    DONE
halt:   JMP      halt

; status 0; signal 1 rising, signal 2 steady
frames: .word    0x0, 0x123, 0x800
        .word    0x0, 0x456, 0x800
        .word    0x0, 0x789, 0x7FF
        .word    0x0, 0xABC, 0x801
        .word    END
