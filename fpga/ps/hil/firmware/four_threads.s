; HIL four-thread probe: T0, T1 and T2 make square waves on uio[4], uio[5]
; and uio[6] with exact half periods of 400, 600 and 1000 cycles (absolute
; WAIT_CMP schedules; with four threads running, each half period is a
; multiple of 4, so every target lands in its own thread's slot). Each time
; T0 toggles it pushes a count into the FIFO, and T3 pops it onto uo_out.
.include "msf.inc"
.equ H0, 400
.equ H1, 600
.equ H2, 1000

start:  OE_SET   0x70
        LDI      R1, t1
        TSTART   #1, R1
        LDI      R1, t2
        TSTART   #2, R1
        LDI      R1, t3
        TSTART   #3, R1
        LDI      R6, H0
        LDR      R4, #0
        CNT_R    R5
w0:     ADDS     R5, R5, R6
        WAIT_CMP R5
        PIN_TGL  0x10
        INCR     R4
        FIFO_PUT R4
        JMP      w0

t1:     LDI      R6, H1
        CNT_R    R5
w1:     ADDS     R5, R5, R6
        WAIT_CMP R5
        PIN_TGL  0x20
        JMP      w1

t2:     LDI      R6, H2
        CNT_R    R5
w2:     ADDS     R5, R5, R6
        WAIT_CMP R5
        PIN_TGL  0x40
        JMP      w2

t3:     FIFO_GET R2
        JZ       t3                ; empty: look again
        OUT_W    R2
        JMP      t3
