; MIDI OUT: plays a short phrase from a table in the program, running status and a
; real-time message included, every bit on the cycle counter.
;   pin   : TX uio[0], idle high (to the MIDI OUT current loop driver)
;   table : song, one word an entry: 0x00-0xFF a byte to send, 0x100 + n a pause of
;           n ms (n >= 1), 0xFFFF the end
;   done  : uo_out = DONE
; Sources: MIDI 1.0 Detailed Specification (MMA, 4.2), "Hardware": 31.25 kbaud
; +-1 %, asynchronous, a start bit, 8 data bits LSB first, a stop bit; "Data
; format": status bytes 0x80-0xFF, data bytes 0x00-0x7F, running status (a
; channel message's status byte may be left out while it stays the same), Note On
; with velocity 0 is a Note Off, system real-time bytes (0xF8-0xFF) may appear
; anywhere.
.include "msf.inc"
.equ TX,    0x01
.equ BIT,   384                     ; 12 MHz / 31,250
.equ MS,    12000
.equ END,   0xFFFF
.equ PAUSE, 0x100

start:  PIN_WI   TX
        OE_SET   TX
        LDI      R6, song
        LDI      R7, BIT
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
next:   LD       R3, R6
        INCR     R6
        INCR     R3
        JZ       fin
        DECR     R3
        LDI      R1, PAUSE
        SUBS     R1, R3, R1         ; N: a byte; else R1 = the pause in ms
        JN       byte
        WAIT_CMP R5                 ; the stop bit before the pause ends
pz:     LDI      R2, MS             ; the last ms is not waited for here: the next
        ADDS     R5, R5, R2         ; start bit (or pause) waits for it
        DECR     R1
        JZ       next
        WAIT_CMP R5
        JMP      pz
byte:   ADDS     R3, R3, R3         ; the start bit, 0
        LDI      R1, 0x200          ; the stop bit
        OR       R3, R3, R1
        LDR      R4, #10
bit:    LDR      R1, #1
        AND      R2, R3, R1
        WAIT_CMP R5
        PIN_W    R2
        SHR      R3, R3, #1
        ADDS     R5, R5, R7
        DECR     R4
        JNZ      bit
        JMP      next
fin:    WAIT_CMP R5
        UO_WI    DONE
halt:   JMP      halt

; channel 1: Program Change to 0 (piano), volume (controller 7) 100, middle C on, a
; pause, C off by running status (velocity 0), E on by running status, active
; sensing, a pause, E off (explicit Note Off, release velocity 64), pitch bend
; centred (0x2000), active sensing
song:   .word    0xC0, 0x00
        .word    0xB0, 0x07, 100
        .word    0x90, 60, 100
        .word    PAUSE + 5
        .word    60, 0
        .word    64, 100
        .word    0xFE
        .word    PAUSE + 5
        .word    0x80, 64, 64
        .word    0xE0, 0x00, 0x40
        .word    0xFE
        .word    END
