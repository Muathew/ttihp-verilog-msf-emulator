; Bus monitor, I2C, with a flight recorder (flight.inc): as mon_i2c.s, and the
; first time the bus breaks a timing rule or a byte is cut short ('H', 'L', 'X'),
; it also sends the waveform around that fault. Thread 0's code is mon_i2c.s's.
.include "mon_i2c.inc"

; R1 = event -> Z = 1 if a fault
isfault: SHR     R4, R1, #7
        SHR      R4, R4, #1        ; code
        LDR      R5, #'H'
        SUBS     R5, R4, R5
        JZ       if_y
        LDR      R5, #'L'
        SUBS     R5, R4, R5
        JZ       if_y
        LDR      R5, #'X'
        SUBS     R5, R4, R5
if_y:   RET

.include "flight.inc"
