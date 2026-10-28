; HIL debug view, single step (test_debug.test_debug_single_step): R1 counts
; the loop's INCRs.
        LDR    R1, #0
lp:     INCR   R1
        NOP
        JMP    lp
