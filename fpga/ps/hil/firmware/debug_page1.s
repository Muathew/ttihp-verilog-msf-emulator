; HIL debug view page 1 (test_debug.test_debug_view_page1): four threads,
; T2 and T3 leave known registers and flags, T3 stalls for good, all parked.
        LDI    R2, t1
        TSTART R2
        LDI    R2, t2
        TSTART #2, R2
        LDI    R2, t3
        TSTART #3, R2
h0:     JMP    h0
t1:     JMP    t1
t2:     LDI    R1, 0x2211
        LDI    R7, 0x2277
        LDR    R3, #0
        DECR   R3                   ; Z2 = 0, N2 = 1
h2:     JMP    h2
t3:     LDI    R1, 0x3311
        LDR    R3, #1
        DECR   R3                   ; Z3 = 1
        WAIT_EDGE 0                 ; T3 stalls for good (uio0 never moves)
