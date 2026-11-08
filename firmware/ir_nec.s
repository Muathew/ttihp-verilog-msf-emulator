; NEC infrared remote: the chip drives the IR LED, 38 kHz carrier included. One
; frame (address, its complement, command, its complement), then one repeat code
; 108 ms after the frame began, as a remote does while the key is held.
;   pin   : the LED uio[0], push-pull, high = LED on
;   ADDR, CMD: the address and command bytes
;   carrier: 316 cycles a period at 12 MHz (37.97 kHz), on for 105 (a third)
; Sources: NEC infrared protocol as described by SB-Projects ("NEC Protocol") and
; Vishay's "Data Formats for IR Remote Control": 38 kHz carrier, 1/3 duty; a 9 ms
; burst and a 4.5 ms space lead; each bit is a 562.5 us burst, then 562.5 us (a 0)
; or 1687.5 us (a 1) of space; address, ~address, command, ~command, LSB first; a
; final 562.5 us burst; the repeat code is a 9 ms burst, a 2.25 ms space and a
; 562.5 us burst, 108 ms after the start of the frame before it.
; How: the symbols (bursts in carrier periods, spaces in cycles) go into data RAM
; first, then one loop plays them, so every edge is on the cycle counter. A frame
; always holds sixteen 1s (a byte and its complement have eight between them), so
; it lasts 68.0625 ms, and the repeat code starts 39.9375 ms after it ends.
.include "msf.inc"
.equ LED,    0x01
.equ ADDR,   0x59
.equ CMD,    0x16
.equ PER,    316                    ; carrier period
.equ ON,     105                    ; carrier on time
.equ OFF,    PER - ON
.equ B_LEAD, 342                    ; 9 ms of carrier
.equ B_BIT,  21                     ; 562.5 us of carrier (21.3 periods)
.equ S_LEAD, 54000 - OFF            ; 4.5 ms, less the last period's off time
.equ S_ZERO, 6750 - OFF             ; 562.5 us
.equ S_ONE,  20250 - OFF            ; 1687.5 us
.equ S_REP,  27000 - OFF            ; 2.25 ms
.equ CHUNK,  60000                  ; a long space is waited for in pieces
.equ GAP,    479250                 ; the stop burst's end to the repeat code
.equ LAST,   GAP - 7 * CHUNK        ; 59,250
.equ S_MIN,  100                    ; (the last burst's space: the end follows)
.equ S_MAX,  CHUNK
.equ END,    0xFFFF
.equ SYM,    8                      ; RAM 8..: pairs (burst periods, space - OFF)

start:  PIN_CLR  LED
        OE_SET   LED
        LDR      R6, #SYM           ; R6: where the next symbol goes
        LDI      R1, B_LEAD
        LDI      R2, S_LEAD
        CALL     put
        LDI      R3, ADDR | ((~ADDR & 0xFF) << 8)
        CALL     word
        LDI      R3, CMD | ((~CMD & 0xFF) << 8)
        CALL     word
        LDR      R1, #B_BIT         ; the stop burst, then the gap in pieces
        LDI      R2, CHUNK - OFF
        CALL     put
        LDR      R0, #6
gap:    LDR      R1, #0
        LDI      R2, CHUNK
        CALL     put
        DECR     R0
        JNZ      gap
        LDR      R1, #0
        LDI      R2, LAST
        CALL     put
        LDI      R1, B_LEAD         ; the repeat code
        LDI      R2, S_REP
        CALL     put
        LDR      R1, #B_BIT
        LDR      R2, #S_MIN
        CALL     put
        LDI      R1, END
        ST       R6, R1
        ; play them all
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
        LDR      R6, #SYM
play:   LD       R4, R6             ; burst periods (0: only a space; END: the end)
        INCR     R6
        LD       R3, R6             ; the space after it
        INCR     R6
        INCR     R4
        JZ       p_end
        DECR     R4
        JNZ      p_on
        WAIT_CMP R5                 ; a piece of a long space
        ADDS     R5, R5, R3         ; @range R3 S_MIN S_MAX
        JMP      play
p_on:   WAIT_CMP R5
        PIN_SET  LED
        LDI      R1, ON
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  LED
        LDI      R1, OFF
        ADDS     R5, R5, R1
        DECR     R4
        JNZ      p_on
        ADDS     R5, R5, R3         ; @range R3 S_MIN S_MAX
        JMP      play
p_end:  UO_WI    DONE
halt:   JMP      halt

; R3: 16 bits, LSB first, as bit symbols. Returns through R0 (put uses R7).
word:   MOV      R0, R7
        LDR      R4, #16
w_bit:  LDR      R1, #1
        AND      R2, R3, R1
        ADDS     R2, R2, R2
        LDR      R1, #B_BIT
        LDI      R2, S_ZERO
        JZ       w_put
        LDI      R2, S_ONE
w_put:  ST       R6, R1
        INCR     R6
        ST       R6, R2
        INCR     R6
        SHR      R3, R3, #1
        DECR     R4
        JNZ      w_bit
        JR       R0

; one symbol: R1 periods of carrier, then R2 cycles more of space
put:    ST       R6, R1
        INCR     R6
        ST       R6, R2
        INCR     R6
        RET
