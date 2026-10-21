; WS2812 ("NeoPixel") LED chain, 800 kHz: the TX engine sends 5-cycle NRZ
; symbols, three per data bit: 1 = 110 (high 10 cycles, low 5), 0 = 100
; (high 5, low 10), within the LEDs' +-150 ns at 12 MHz. A 16-entry table
; turns each nibble into its 12 symbols, so firmware pushes two words per
; byte and the double-buffered engine keeps the stream gapless.
;   pin   : uio[0] (idles low)
;   input : data RAM [0] = number of LEDs n, [1 .. 3n] = their bytes, in the
;           chain's order (G, R, B for each LED; MSB first on the wire)
;   done  : the line held low for the 50 us reset, then uo_out = DONE
.include "msf.inc"
.equ PIN,   0
.equ RESET, 640                    ; low this long latches the colours (53 us)

start:  LDR      R1, #5
        TX_DIV   R1                ; a symbol = 5 cycles
        LDI      R1, (TX_EN | (12 - 1) << 8 | PIN)
        TX_CFG   R1                ; NRZ, MSB first, 12 symbols a push, idle 0
        LDR      R2, #0
        LD       R3, R2            ; LEDs
        ADDS     R4, R3, R3
        ADDS     R3, R4, R3        ; bytes = 3 n
        JZ       latch
        LDI      R6, sym
        LDR      R5, #0x0F
        LDR      R2, #1
byte:   LD       R1, R2            ; the byte
        SHR      R4, R1, #4
        ADDS     R4, R4, R6
        LD       R4, R4            ; high nibble's symbols
push1:  TX_PUSH  R4
        JZ       push1
        AND      R1, R1, R5
        ADDS     R1, R1, R6
        LD       R1, R1            ; low nibble's
push2:  TX_PUSH  R1
        JZ       push2
        INCR     R2
        DECR     R3
        JNZ      byte
        TX_WAIT                    ; the last symbol out (the line low again)
latch:  CNT_R    R1
        LDI      R2, RESET
        ADDS     R1, R1, R2
        WAIT_CMP R1
        UO_WI    DONE
halt:   JMP      halt

; nibble -> 12 symbols, MSB first: each bit b becomes 1 b 0
sym:    .word    0x924, 0x926, 0x934, 0x936, 0x9A4, 0x9A6, 0x9B4, 0x9B6
        .word    0xD24, 0xD26, 0xD34, 0xD36, 0xDA4, 0xDA6, 0xDB4, 0xDB6
