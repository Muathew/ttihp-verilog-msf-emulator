; Logic analyser, streaming: records 8 pins in change mode and sends each word
; to the host as it appears, so a recording is not limited to the buffer. The
; capture engine runs by itself and never changes this code's timing; this loop
; only has to keep up.
;   SRC   : 0 = the uio pins, CAP_UI = the ui pins (define)
;   DIV   : cycles per sample (define)
;   N, BASE: ring of 2 << N words in the size-aligned block at BASE
;   BIT   : UART bit time in cycles, on uio[0] (define; 12 = 1 Mbaud at 12 MHz)
; Sent, as 16-bit words low byte first: 0xA55B; CNT in the cycle before arming;
; then change-mode words {pins, ticks since the last word}, oldest first. Two
; bytes take 20 bits, so at 1 Mbaud it carries 50,000 words a second (bursts up
; to the ring's size). A word also goes out every 255 ticks with no change, so
; at DIV 1 an idle line alone uses nearly all of that: raise DIV for a long
; recording. If the ring fills before a word was sent, the rest of the recording
; cannot be trusted: it sends the word 0x0000 (ticks are never 0, so no recorded
; word looks like that), sets uo_out = LOST and stops.
; tools/msf_capture.py turns the stream into a VCD or a sigrok session.
.include "msf.inc"
.equ SRC,   0
.equ DIV,   1
.equ N,     7
.equ BASE,  512
.equ BIT,   12
.equ LOST,  0xEE
.equ SIZE,  2 << N
.equ BLOCK, BASE & ~(SIZE - 1)
.equ NEVER, 0x0100                 ; trigger: mask 0, value 1 can never hold

start:  LDR      R1, #0
        TMO      R1                ; no timeouts
        PIN_SET  1                 ; UART idle high before the pin is driven
        OE_SET   1
        LDI      R1, BIT
        TX_DIV   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | 0)
        TX_CFG   R1
        LDI      R0, 0x200         ; stop bit
        LDI      R1, 0xA55B
        CALL     tx16
        LDI      R1, BASE | N << CAP_N
        CAP_BUF  R1
        LDI      R1, DIV
        CAP_DIV  R1
        LDI      R1, NEVER
        CAP_TRIG R1
        LDI      R3, BLOCK         ; R3 = next word to send
        LDI      R1, SRC | CAP_CHG | CAP_ARM
        CNT_R    R6                ; the cycle before arming
        CAP_CTL  R1
        MOV      R1, R6
        CALL     tx16
        LDI      R4, SIZE - 1      ; R4 = ring offset mask
loop:   CAP_STAT R1                ; bits 9:0: where the next word goes
        LDI      R2, 0x3FF
        AND      R1, R1, R2
        SUBS     R2, R1, R3        ; words waiting
        AND      R2, R2, R4
        ADDS     R2, R2, R2        ; (Z: none; AND sets no flags)
        JZ       loop
        ; one word waiting
        LDI      R2, SIZE - 8      ; waiting >= this: about to be overwritten
        SUBS     R1, R1, R3
        AND      R1, R1, R4
        SUBS     R1, R1, R2
        JP       lost
        LD       R1, R3
        CALL     tx16
        INCR     R3
        AND      R2, R3, R4
        ADDS     R2, R2, R2        ; Z: ran off the block's end
        JNZ      loop
        LDI      R2, SIZE
        SUBS     R3, R3, R2
        JMP      loop
lost:   LDR      R1, #0
        CALL     tx16
        TX_WAIT
        UO_WI    LOST
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
