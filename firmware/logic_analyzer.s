; Logic analyser: records 8 pins with the capture engine (isa.md §6.6), then
; sends the buffer to the host as 8N1 bytes on uio[0] (TX engine).
;   SRC   : 0 = the uio pins, CAP_UI = the ui pins (define)
;   MODE  : 0 = two samples a word, CAP_CHG = change mode (define)
;   DIV   : cycles per sample (define)
;   MASK, VAL, EDGE: trigger when (pins & MASK) == VAL, as a level (EDGE 0)
;          or an edge (EDGE = CAP_EDGE); MASK 0 triggers at once (defines)
;   N, BASE: buffer of 2 << N words in the size-aligned block at BASE
;   POST  : words recorded after the trigger word (define)
;   BIT   : UART bit time in cycles (define; 12 = 1 Mbaud at 12 MHz)
; Sent, as 16-bit words low byte first: 0xA55A; CNT in the cycle before
; arming (so sample k saw the pins of cycle CNT + k * DIV, two cycles before
; it was taken); the trigger word's index counted from the oldest word;
; the capture status (isa.md §6.6); then the 2 << N words, oldest first.
; The buffer is cleared before arming: words that were never recorded are 0.
; uo_out = 0x55 when done.
.include "msf.inc"
.equ SRC,   0
.equ MODE,  0
.equ DIV,   1
.equ MASK,  0
.equ VAL,   0
.equ EDGE,  0
.equ N,     5
.equ BASE,  512
.equ POST,  16
.equ BIT,   12
.equ SIZE,  2 << N
.equ BLOCK, BASE & ~(SIZE - 1)

start:  LDR      R1, #0
        TMO      R1                ; no timeouts
        PIN_SET  1                 ; UART idle high before the pin is driven
        OE_SET   1
        LDI      R1, BIT
        TX_DIV   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | 0)
        TX_CFG   R1
        LDI      R0, 0x200         ; stop bit
        LDI      R1, BLOCK
        MEM_A    R1
        LDI      R2, SIZE
        LDR      R1, #0
clr:    MEM_W    R1                ; clear the buffer
        DECR     R2
        JNZ      clr
        LDI      R1, BASE | N << CAP_N
        CAP_BUF  R1
        LDI      R1, DIV
        CAP_DIV  R1
        LDI      R1, MASK | VAL << 8
        CAP_TRIG R1
        LDI      R1, POST | EDGE | SRC | MODE | CAP_ARM
        CNT_R    R6                ; the cycle before arming
        CAP_CTL  R1
wt:     CAP_STAT R1
        LDI      R2, CS_DONE
        AND      R2, R1, R2
        ADDS     R2, R2, R2
        JZ       wt
        MOV      R3, R1            ; status
        LDI      R1, 0xA55A
        CALL     tx16
        MOV      R1, R6
        CALL     tx16
        CAP_TPOS R2
        LDI      R4, SIZE - 1
        AND      R1, R3, R4        ; the oldest word's offset (next write)
        AND      R2, R2, R4
        SUBS     R2, R2, R1
        AND      R1, R2, R4        ; trigger word, counted from the oldest
        CALL     tx16
        MOV      R1, R3
        CALL     tx16
        LDI      R4, 0x3FF
        AND      R3, R3, R4        ; oldest word's address
        LDI      R4, SIZE          ; words left
dump:   LD       R1, R3
        CALL     tx16
        INCR     R3
        LDI      R2, SIZE - 1
        AND      R2, R3, R2
        ADDS     R2, R2, R2        ; Z: ran off the block's end
        JNZ      dn
        LDI      R2, SIZE
        SUBS     R3, R3, R2
dn:     DECR     R4
        JNZ      dump
        TX_WAIT                    ; the last bytes are on the line
        UO_WI    DONE
halt:   JMP      halt

; R1 -> two 8N1 frames, low byte first; uses R2, R5
tx16:   LDR      R2, #0xFF
        AND      R5, R1, R2
        ADDS     R5, R5, R5
        OR       R5, R5, R0
        TX_PUSH  R5
        SWAP     R5, R1
        AND      R5, R5, R2
        ADDS     R5, R5, R5
        OR       R5, R5, R0
        TX_PUSH  R5
        RET
