; Philips RC-5 infrared remote: the chip drives the IR LED, 36 kHz carrier
; included. A key held: the same frame (toggle bit unchanged) every 113.778 ms.
;   pin   : the LED uio[0], push-pull, high = LED on
;   ADDR, CMD, TOG: 5-bit address, 6-bit command, toggle bit; FRAMES: how many
;   carrier: 333 cycles a period at 12 MHz (36.04 kHz), on for 111 (a third)
; Sources: Philips RC-5 as described by SB-Projects ("Philips RC-5 Protocol") and
; Vishay's "Data Formats for IR Remote Control": 36 kHz carrier; bi-phase bits of
; 1.778 ms, each half 889 us (32 carrier periods); a 1 is a space then a burst, a
; 0 a burst then a space; 14 bits, MSB first: S1 = 1, S2 = 1 (standard RC-5),
; toggle, 5 address bits, 6 command bits; a held key repeats every 113.778 ms
; (64 bit times).
; How: the half-bits become symbols in data RAM, merged where two halves of the
; same kind meet: (carrier periods, the space after them in cycles), or (SPACE + k,
; a space waited for k times); one loop plays them, so every edge is on the cycle
; counter.
.include "msf.inc"
.equ LED,    0x01
.equ ADDR,   0x05
.equ CMD,    0x35
.equ TOG,    0
.equ FRAMES, 2
.equ PER,    333
.equ ON,     111
.equ OFF,    PER - ON
.equ HALFP,  32                     ; carrier periods in a half bit
.equ HALF,   HALFP * PER            ; 10,656 cycles: 888 us
.equ CHUNK,  60000
.equ FRAME,  64 * 2 * HALF          ; 1,363,968 cycles from frame to frame
.equ GAP1,   CHUNK - HALF           ; 49,344
.equ GAP3,   FRAME - 28 * HALF - GAP1 - 16 * CHUNK   ; 56,256
.equ S_MIN,  100
.equ S_MAX,  CHUNK
.equ SPACE,  0x8000                 ; a symbol 0x8000 + k: k pieces of space; 0x8000: the end
.equ SAVE,   7
.equ SYM,    8
.equ BITS,   (0b11 << 12) | (TOG << 11) | (ADDR << 6) | CMD

start:  PIN_CLR  LED
        OE_SET   LED
        LDR      R6, #SYM
        LDR      R0, #FRAMES
fr:     LDI      R3, BITS << 2      ; 14 bits, first at bit 15
        LDR      R4, #14
        LDR      R1, #0             ; R1: carrier periods so far, R2: space so far
        LDR      R2, #0
bit:    LDR      R5, #0
        SUBS     R5, R3, R5         ; N: the bit
        JN       b_one
        CALL     on                 ; a 0: burst, space
        CALL     off
        JMP      b_next
b_one:  CALL     off                ; a 1: space, burst
        CALL     on
b_next: ADDS     R3, R3, R3
        DECR     R4
        JNZ      bit
        ; the rest of the 64 bit times. The frame ends with a burst and 0 or a half bit
        ; of space pending: add GAP1 to it (so the symbol stays within CHUNK), then
        ; 16 pieces of CHUNK and GAP3: 28 halves + GAP1 + 16 x CHUNK + GAP3 = FRAME
        LDI      R5, GAP1
        ADDS     R2, R2, R5
        CALL     flush
        LDI      R1, SPACE + 16
        LDI      R2, CHUNK
        CALL     put
        LDI      R1, SPACE + 1
        LDI      R2, GAP3
        CALL     put
        DECR     R0
        JNZ      fr
        LDI      R1, SPACE
        CALL     put
        ; play
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
        LDR      R6, #SYM
play:   LD       R4, R6
        INCR     R6
        LD       R3, R6
        INCR     R6
        LDR      R1, #0
        SUBS     R1, R4, R1         ; N: a space symbol
        JN       p_sp
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
p_sp:   LDI      R1, SPACE
        SUBS     R4, R4, R1         ; how many pieces (0: the end)
        JZ       p_end
sp_l:   WAIT_CMP R5
        ADDS     R5, R5, R3         ; @range R3 S_MIN S_MAX
        DECR     R4
        JNZ      sp_l
        JMP      play
p_end:  UO_WI    DONE
halt:   JMP      halt

; half-bits: a burst adds to R1 (writing out the space before it first); a space
; adds to R2
on:     ADDS     R2, R2, R2
        JZ       on_add             ; no space pending
        SHR      R2, R2, #1
        LDR      R5, #SAVE          ; (flush uses R5: the return address goes to RAM)
        ST       R5, R7
        CALL     flush
        LDR      R5, #SAVE
        LD       R7, R5
on_add: LDR      R5, #HALFP
        ADDS     R1, R1, R5
        RET
off:    LDI      R5, HALF
        ADDS     R2, R2, R5
        RET
; write the pending (bursts, space) as a symbol: the play loop's bursts already end
; with the last period's off time, so the space is the whole space and every burst
; starts on the half-bit grid; a space with no
; burst before it as a space symbol
flush:  ADDS     R1, R1, R1
        JZ       fl_sp
        SHR      R1, R1, #1
        JMP      put
fl_sp:  LDI      R1, SPACE + 1
put:    ST       R6, R1
        INCR     R6
        ST       R6, R2
        INCR     R6
        LDR      R1, #0
        LDR      R2, #0
        RET
