; HIL filter probe (test_peripherals.test_filter_delay): FILTER = FILT on
; uio2; stores at RAM[0] the cycles from a rise on uio3 (unfiltered) to the
; same rise on uio2. Shows 0x01 once waiting, then DONE.
.include "msf.inc"
.equ FILT, 0x0404                   ; uio2, 4 cycles

        LDI      R1, FILT
        FILTER   R1
        LDR      R1, #0
        TMO      R1
        UO_WI    0x01
        WAIT_EDGE 3, rise
        CNT_R    R5
        WAIT_EDGE 2, rise
        CNT_R    R6
        SUBS     R6, R6, R5
        LDR      R2, #0
        ST       R2, R6
        UO_WI    DONE
h:      JMP      h
