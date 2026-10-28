; HIL capture probe (test_capture.arm_prog): CAP_BUF = BUF, CAP_DIV = DIV,
; CAP_TRIG = TRIG, CAP_CTL = CTL (R5 = CNT in the cycle before), then wait
; for done. RAM[0] = the final status, RAM[1] = the trigger position,
; RAM[2] = R5.
.include "msf.inc"
.equ BUF,  0x1200                   ; 512, 32 words
.equ DIV,  1
.equ TRIG, 0
.equ CTL,  0x800F                   ; armed, POST 15

        LDR    R0, #0
        LDR    R1, #0
        LDR    R2, #0
        LDR    R3, #0
        LDR    R4, #0
        LDR    R5, #0
        LDR    R6, #0
        LDR    R7, #0
        LDI    R1, BUF
        CAP_BUF R1
        LDI    R1, DIV
        CAP_DIV R1
        LDI    R1, TRIG
        CAP_TRIG R1
        LDI    R1, CTL
        CNT_R  R5
        CAP_CTL R1
wt:     CAP_STAT R2
        LDI    R3, CS_DONE
        AND    R3, R2, R3
        ADDS   R3, R3, R3
        JZ     wt
        CAP_TPOS R4
        LDR    R3, #0
        ST     R3, R2
        INCR   R3
        ST     R3, R4
        INCR   R3
        ST     R3, R5
h:      JMP    h
