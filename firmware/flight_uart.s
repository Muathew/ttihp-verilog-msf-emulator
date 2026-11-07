; Bus monitor, UART, with a flight recorder (flight.inc): as mon_uart.s, and the
; first time a frame breaks ('F'), a bit time is off ('B') or a glitch comes ('G'),
; it also sends the waveform around that fault. Thread 0's code is mon_uart.s's.
.include "mon_uart.inc"

; R1 = event -> Z = 1 if a fault
isfault: SHR     R4, R1, #7
        SHR      R4, R4, #1        ; code
        LDR      R5, #'F'
        SUBS     R5, R4, R5
        JZ       if_y
        LDR      R5, #'B'
        SUBS     R5, R4, R5
        JZ       if_y
        LDR      R5, #'G'
        SUBS     R5, R4, R5
if_y:   RET

.include "flight.inc"
