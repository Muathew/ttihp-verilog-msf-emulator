; S/PDIF (IEC 60958, consumer) transmitter: 48 kHz, 16-bit stereo, for ever.
; Drawn at random (docs/draw/).
;   pin   : data uio[0], push-pull
;   clock : 30.72 MHz, inside the 33.33 MHz sign-off. A half-cell is 5 cycles, so
;           the line runs at 6.144 M half-cells a second: 64 cells a frame, two
;           half-cells a cell, 48,000 frames a second, exactly
;   audio : this table, left then right; the right channel starts at its third
;           entry: 0000 5A82 7FFF 5A82 0000 A57E 8001 A57E (a sine, 6 kHz)
; Sources: IEC 60958-1 and -3 as summarised in the AES/EBU and S/PDIF literature
; (e.g. the "AES3" and "S/PDIF" Wikipedia articles, and Philips/Cirrus application
; notes on the subframe): a subframe is 32 time slots: 0-3 the preamble, 4-7
; auxiliary, 8-27 audio LSB first (a 16-bit sample goes in the top 16 bits,
; slots 12-27), 28 V, 29 U, 30 C, 31 P (even parity over 4-31); biphase-mark
; coding, a transition at every cell boundary and one in the middle of a 1; the
; preambles break that rule: B (Z) 11101000 starts a 192-frame block on the left
; subframe, M (X) 11100010 the other left subframes, W (Y) 11100100 the right,
; shown for a line that was low before them (inverted after a high one). Channel
; status: one bit a frame, the same in both subframes, 192 a block; consumer (bit
; 0 = 0), linear PCM (bit 1 = 0), sampling frequency in bits 24-27 = 0100, 48 kHz;
; everything else 0 (copyright asserted, no pre-emphasis, mode 0, category general,
; clock accuracy level II, word length not indicated).
; How: the TX engine in NRZI (a 0 symbol toggles the line), LSB first, 16 symbols a
; word. Biphase-mark is then a toggle at each cell start and another for a 1: a bit
; b is the symbols 0, not b. The preambles become fixed toggle patterns, the same
; for either starting level. LUT spreads a nibble into its 8 symbols.
.include "msf.inc"
.equ PIN,   0
.equ HALF,  5
.equ PRE_B, 0xC6                   ; B: toggle, -, -, toggle, toggle, toggle, -, -
.equ PRE_M, 0x36                   ; M: toggle, -, -, toggle, -, -, toggle, toggle
.equ PRE_W, 0x96                   ; W: toggle, -, -, toggle, -, toggle, toggle, -
.equ AUX0,  0xAA00                 ; slots 4-7, all 0
.equ CS48,  25                     ; the only channel status bit that is 1
.equ BLOCK, 192
.equ FRAME, 0                      ; RAM: the frame in the block
.equ IDX,   1                      ; RAM: the left sample's place in the table

start:  PIN_CLR  (1 << PIN)
        OE_SET   (1 << PIN)
        LDR      R1, #0
        TMO      R1                ; a push waits at most a word, 80 cycles
        LDR      R2, #FRAME
        ST       R2, R1
        LDR      R2, #IDX
        ST       R2, R1
        LDR      R1, #HALF
        TX_DIV   R1
        LDI      R1, (TX_EN | TX_NRZI | TX_LSB | (16 - 1) << 8 | PIN)
        TX_CFG   R1
frame:  LDR      R2, #FRAME
        LD       R6, R2            ; R6: the frame
        LDR      R1, #CS48
        SUBS     R1, R6, R1
        LDR      R4, #0            ; R4: C, the frame's channel status bit
        JNZ      f_c
        LDR      R4, #1
f_c:    LDR      R2, #PRE_M
        LDR      R1, #0
        SUBS     R1, R6, R1
        JNZ      f_l
        LDR      R2, #PRE_B        ; a block starts
f_l:    LDR      R1, #IDX
        LD       R5, R1
        LDI      R1, TAB
        ADDS     R1, R1, R5
        LD       R3, R1
        CALL     subf              ; left
        LDR      R1, #IDX
        LD       R5, R1
        LDR      R1, #2
        ADDS     R5, R5, R1
        LDR      R1, #7
        AND      R5, R5, R1
        LDI      R1, TAB
        ADDS     R1, R1, R5
        LD       R3, R1
        LDR      R2, #PRE_W
        CALL     subf              ; right
        LDR      R1, #IDX          ; next sample, next frame
        LD       R5, R1
        INCR     R5
        LDR      R2, #7
        AND      R5, R5, R2
        ST       R1, R5
        INCR     R6
        LDR      R1, #BLOCK
        SUBS     R1, R6, R1
        JNZ      f_st
        LDR      R6, #0
f_st:   LDR      R2, #FRAME
        ST       R2, R6
        JMP      frame

; one subframe: R2 the preamble, R3 the sample, R4 C. Four pushes; uses R0, R1, R5.
subf:   LDI      R1, AUX0
        OR       R1, R1, R2
        TX_PUSH  R1                ; the preamble and slots 4-7
        LDR      R0, #15
        AND      R1, R3, R0
        LDI      R0, LUT
        ADDS     R1, R1, R0
        LD       R1, R1
        SWAP     R1, R1            ; sample bits 0-3 in slots 12-15
        LDR      R0, #0xAA         ; slots 8-11, the field's four low bits, 0
        OR       R1, R1, R0
        TX_PUSH  R1
        SHR      R5, R3, #4
        LDR      R0, #15
        AND      R1, R5, R0
        LDI      R0, LUT
        ADDS     R1, R1, R0
        LD       R1, R1            ; bits 4-7
        SHR      R5, R5, #4
        LDR      R0, #15
        AND      R5, R5, R0
        LDI      R0, LUT
        ADDS     R5, R5, R0
        LD       R5, R5
        SWAP     R5, R5            ; bits 8-11
        OR       R1, R1, R5
        TX_PUSH  R1
        SWAP     R1, R3            ; P: even parity over the sample, V, U and C
        XOR      R1, R1, R3
        SHR      R0, R1, #4
        XOR      R1, R1, R0
        SHR      R0, R1, #2
        XOR      R1, R1, R0
        SHR      R0, R1, #1
        XOR      R1, R1, R0
        XOR      R1, R1, R4
        LDR      R0, #1
        AND      R1, R1, R0
        ADDS     R1, R1, R1
        OR       R1, R1, R4
        SHL      R1, R1, #2        ; V = U = 0, C, P
        LDI      R0, LUT
        ADDS     R1, R1, R0
        LD       R1, R1
        SWAP     R1, R1
        SHR      R5, R3, #7
        SHR      R5, R5, #5        ; bits 12-15
        LDI      R0, LUT
        ADDS     R5, R5, R0
        LD       R5, R5
        OR       R1, R1, R5
        TX_PUSH  R1
        RET

; a nibble's bits, first in bit 0, as NRZI symbols: each bit b is 0, not b
LUT:    .word 0xAA, 0xA8, 0xA2, 0xA0, 0x8A, 0x88, 0x82, 0x80
        .word 0x2A, 0x28, 0x22, 0x20, 0x0A, 0x08, 0x02, 0x00
TAB:    .word 0x0000, 0x5A82, 0x7FFF, 0x5A82, 0x0000, 0xA57E, 0x8001, 0xA57E
