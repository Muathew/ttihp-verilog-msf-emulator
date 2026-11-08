; Philips RC-6 (mode 0) infrared remote: the chip drives the IR LED, 36 kHz carrier
; included. Two key presses: the toggle (trailer) bit flips between them.
;   pin   : the LED uio[0], push-pull, high = LED on
;   ADDR, CMD1, CMD2: the address and the two commands
;   carrier: 333 cycles a period at 12 MHz (36.04 kHz), on for 111 (a third)
; Sources: Philips RC-6 as described by SB-Projects ("Philips RC-6 Protocol"): 36 kHz;
; the time unit T is 16 carrier periods (444 us); leader: a 6T burst and a 2T space;
; then Manchester bits of 2T, a 1 being a burst then a space (the reverse of RC-5):
; the start bit (1), three mode bits (000 for mode 0), the trailer bit (the toggle)
; twice as long, 8 address bits and 8 command bits, MSB first; 52T (23.1 ms) in all,
; at least 6T free after it.
; How: as in ir_rc5.s, the units become symbols in data RAM (carrier periods, the
; space after them in cycles), or (SPACE + k, a space waited for k times); one loop
; plays them, so every edge is on the cycle counter.
.include "msf.inc"
.equ LED,    0x01
.equ ADDR,   0x00                   ; TV
.equ CMD1,   0x0C                   ; standby
.equ CMD2,   0x10                   ; volume +
.equ PER,    333
.equ ON,     111
.equ OFF,    PER - ON
.equ TP,     16                     ; carrier periods in T
.equ T,      TP * PER               ; 5,328 cycles: 444 us
.equ CHUNK,  60000
.equ GAP,    5                      ; 5 chunks + what is left of the frame's end: 25 ms free
.equ S_MIN,  100
.equ S_MAX,  CHUNK
.equ SPACE,  0x8000
.equ SAVE,   0                      ; RAM 0-4: return addresses, the command, the toggle
.equ SYM,    8

start:  PIN_CLR  LED
        OE_SET   LED
        LDR      R6, #SYM
        LDR      R1, #0
        LDR      R2, #0
        LDI      R3, CMD1           ; R0: the toggle
        LDR      R0, #0
        CALL     frame
        LDI      R3, CMD2
        LDR      R0, #1
        CALL     frame
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

; one frame: R3 the command, R0 the toggle; R1/R2 the pending burst/space
frame:  LDR      R4, #SAVE + 1      ; the return address, the command and the toggle
        ST       R4, R7             ; to RAM (one and zero use R0)
        INCR     R4
        ST       R4, R3
        INCR     R4
        ST       R4, R0
        LDR      R4, #6             ; leader: 6T burst, 2T space
ld_on:  CALL     on
        DECR     R4
        JNZ      ld_on
        CALL     off
        CALL     off
        CALL     one                ; start bit
        CALL     zero               ; mode 000
        CALL     zero
        CALL     zero
        LDR      R0, #SAVE + 3
        LD       R0, R0
        ADDS     R0, R0, R0         ; the trailer: the toggle, 2T + 2T
        JZ       tr0
        CALL     on
        CALL     on
        CALL     off
        CALL     off
        JMP      tr_end
tr0:    CALL     off
        CALL     off
        CALL     on
        CALL     on
tr_end: LDR      R4, #SAVE + 2      ; 16 bits: the address, the command
        LD       R3, R4
        LDI      R4, ADDR << 8
        OR       R3, R3, R4
        LDR      R4, #16
fb:     LDR      R0, #0
        SUBS     R0, R3, R0         ; N: a 1
        JN       fb1
        CALL     zero
        JMP      fb_n
fb1:    CALL     one
fb_n:   ADDS     R3, R3, R3
        DECR     R4
        JNZ      fb
        LDI      R0, CHUNK - T      ; at least 6T free: the end of the frame, then
        ADDS     R2, R2, R0         ; GAP chunks more
        CALL     flush
        LDI      R1, SPACE + GAP
        LDI      R2, CHUNK
        CALL     put
        LDR      R4, #SAVE + 1
        LD       R7, R4
        RET

; a bit: 1 = burst then space, 0 = space then burst (the return address waits in RAM:
; on and off use R5, flush R5 and R7)
one:    LDR      R0, #SAVE
        ST       R0, R7
        CALL     on
        CALL     off
        LDR      R0, #SAVE
        LD       R7, R0
        RET
zero:   LDR      R0, #SAVE
        ST       R0, R7
        CALL     off
        CALL     on
        LDR      R0, #SAVE
        LD       R7, R0
        RET
; a unit T: a burst adds to R1 (writing out the space before it first); a space
; adds to R2. on's return address waits in RAM while flush runs.
on:     ADDS     R2, R2, R2
        JZ       on_add             ; no space pending
        SHR      R2, R2, #1
        LDR      R5, #SAVE + 4
        ST       R5, R7
        CALL     flush
        LDR      R5, #SAVE + 4
        LD       R7, R5
on_add: LDR      R5, #TP
        ADDS     R1, R1, R5
        RET
off:    LDI      R5, T
        ADDS     R2, R2, R5
        RET
; write the pending (bursts, space) as a symbol: the play loop's bursts already end
; with the last period's off time, so the space is the whole space and every burst
; starts on the T grid
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
