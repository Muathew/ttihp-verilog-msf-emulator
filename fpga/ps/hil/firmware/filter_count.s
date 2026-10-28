; HIL filter probe (test_peripherals.test_filter_removes_short_pulses and
; test_filter_every_pin): FILTER = FILT; T0 counts rising edges on PA and T1
; on PB, each until 300 quiet cycles, into RAM[0] and RAM[1]. Shows 0x01 once
; counting, DONE when T0 has stored its count.
.include "msf.inc"
.equ FILT, 0x0404                   ; uio2, 4 cycles
.equ PA,   2
.equ PB,   3

        LDI      R1, 300
        TMO      R1                 ; a count ends after 300 quiet cycles
        LDI      R1, FILT
        FILTER   R1
        LDI      R2, t1
        TSTART   R2
        LDR      R3, #0
        UO_WI    0x01
c0:     WAIT_EDGE PA, rise
        JZ       d0
        INCR     R3
        JMP      c0
d0:     LDR      R2, #0
        ST       R2, R3
        UO_WI    DONE
h0:     JMP      h0

t1:     LDR      R3, #0
c1:     WAIT_EDGE PB, rise
        JZ       d1
        INCR     R3
        JMP      c1
d1:     LDR      R2, #1
        ST       R2, R3
h1:     JMP      h1
