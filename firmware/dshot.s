; DShot ESC throttle output (DShot150 by default): 16-bit frames, every edge on the
; cycle counter, a frame every PERIOD cycles, from a table of throttle values.
;   pin   : uio[0] to the ESC's signal input, push-pull, idle low
;   table : frames, per frame the 11-bit throttle and the telemetry request bit
;           (as throttle << 1 | telemetry); 0xFFFF = the end
;   BIT, T1H, T0H: the bit time and the high times of a 1 and a 0, in cycles
;   done  : uo_out = DONE
; Sources: the DShot protocol as Betaflight and BLHeli_32 implement it, written up in
; "DSHOT - the missing handbook" (brushlesswhoop.com): 16 bits MSB first, 11 bits of
; throttle (0 disarmed, 1-47 commands, 48-2047 throttle), 1 telemetry request bit,
; a 4-bit checksum = (v ^ v >> 4 ^ v >> 8) & 0xF of those 12 bits; DShot150: a bit of
; 6.67 us, high for 5.00 us (a 1) or 2.50 us (a 0).
.include "msf.inc"
.equ OUT,    0x01
.equ BIT,    80                     ; 6.67 us
.equ T1H,    60
.equ T0H,    30
.equ PERIOD, 6000                   ; 500 us: frames at 2 kHz
.equ END,    0xFFFF

start:  PIN_CLR  OUT
        OE_SET   OUT
        LDI      R6, frames
        CNT_R    R5
        LDR      R1, #100
        ADDS     R5, R5, R1
fr:     LD       R3, R6
        INCR     R6
        INCR     R3
        JZ       fin
        DECR     R3
        SHR      R1, R3, #4         ; the checksum
        XOR      R1, R1, R3
        SHR      R2, R3, #4
        SHR      R2, R2, #4
        XOR      R1, R1, R2
        LDR      R2, #0x0F
        AND      R1, R1, R2
        SHL      R3, R3, #4
        OR       R3, R3, R1         ; the 16-bit frame
        LDR      R4, #16
bit:    LDR      R1, #0
        SUBS     R1, R3, R1         ; N: a 1
        LDR      R1, #T0H
        LDR      R2, #BIT - T0H
        JP       b_go
        LDR      R1, #T1H
        LDR      R2, #BIT - T1H
b_go:   WAIT_CMP R5
        PIN_SET  OUT
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  OUT
        ADDS     R5, R5, R2
        ADDS     R3, R3, R3
        DECR     R4
        JNZ      bit
        LDI      R1, PERIOD - 16 * BIT   ; the next frame (each bit added BIT)
        ADDS     R5, R5, R1
        JMP      fr
fin:    UO_WI    DONE
halt:   JMP      halt

; disarmed twice, minimum throttle, half (with a telemetry request), full
frames: .word    0 << 1, 0 << 1, 48 << 1, 1046 << 1 | 1, 2047 << 1
        .word    END
