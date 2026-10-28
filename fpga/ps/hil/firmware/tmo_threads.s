; HIL per-thread timeouts (test_threads.test_tmo_per_thread): T1 inherits
; T0's TMO = 10 at TSTART, then T0 sets 0 and waits on uio0 for ever. T1's
; WAIT times out with its 10; T1 then sets its own TMO = 3 and waits again.
; RAM[0] = 1 if T1's first WAIT timed out, RAM[1] and RAM[2] = CNT_R to
; CNT_R across the first and the second WAIT.
        LDR    R0, #0
        LDR    R1, #0
        LDR    R2, #0
        LDR    R3, #0
        LDR    R4, #0
        LDR    R5, #0
        LDR    R6, #0
        LDR    R7, #0
        LDR    R1, #10
        TMO    R1
        LDI    R2, t1
        TSTART R2                   ; T1 inherits TMO = 10
        LDR    R1, #0
        TMO    R1                   ; T0: no timeout from now on
        WAIT_EDGE 0                 ; uio[0] never moves: T0 waits for ever
h0:     JMP    h0
t1:     CNT_R  R3
        WAIT_EDGE 0
        CNT_R  R4
        LDR    R5, #0
        JNZ    t1n
        LDR    R5, #1               ; Z = 1: timed out
t1n:    LDR    R1, #3
        TMO    R1                   ; T1's own TMO
        CNT_R  R6
        WAIT_EDGE 0
        CNT_R  R7
        SUBS   R7, R7, R6
        SUBS   R4, R4, R3
        LDR    R2, #0
        ST     R2, R5
        INCR   R2
        ST     R2, R4
        INCR   R2
        ST     R2, R7
h1:     JMP    h1
