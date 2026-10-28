; HIL debug-view program (test_debug.PROG): T0 and T1 leave known values in
; registers, flags, SR and the pins; T1 stops, T0 parks at h0 (the first
; self-jump).
        LDI    R6, t1
        TSTART R6
        LDI    R1, 0x1234
        LDI    R2, 0xBEEF
        LDR    R3, #0
        DECR   R3                   ; 0xFFFF: Z0 = 0, N0 = 1
        LDI    R4, 0x8001
        SR_LD  R4
        PIN_WI 0x3C
        OE_WI  0xC3
        UO_WI  0x5A
h0:     JMP    h0
t1:     LDI    R1, 0xCAFE
        LDI    R5, 0x0FF0
        SUBS   R2, R2, R2           ; Z1 = 1, N1 = 0
        TSTOP                       ; single mode again: T0 alone at h0
