; Bus monitor, SPI, with a flight recorder (flight.inc): as mon_spi.s, and the
; first time the controller uses another mode ('M') or a transfer ends part way
; through a byte ('E' with bits left over), it also sends the waveform around that
; fault. Thread 0's code is mon_spi.s's.
.include "mon_spi.inc"

; R1 = event -> Z = 1 if a fault
isfault: SHR     R4, R1, #7
        SHR      R4, R4, #1        ; code
        LDR      R5, #'M'
        SUBS     R5, R4, R5
        JZ       if_y
        LDR      R5, #'E'
        SUBS     R5, R4, R5
        JNZ      if_y              ; (Z = 0: not a fault)
        LDR      R5, #0xFF
        AND      R5, R1, R5        ; 'E': bits left over?
        ADDS     R5, R5, R5
        JNZ      if_f              ; yes: Z must come out 1
        LDR      R5, #1            ; none: Z must come out 0
        ADDS     R5, R5, R5
        RET
if_f:   LDR      R5, #0
        ADDS     R5, R5, R5
if_y:   RET

.include "flight.inc"
