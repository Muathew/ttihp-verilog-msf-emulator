; HIL capture overflow probe (test_capture.test_cap_overflow_flag): armed
; at every cycle, then eight MEM_W in a row keep port B busy 8 cycles while
; the engine makes a word every 2. RAM[2] = R5, RAM[4] = the status right
; after the burst; once done, RAM[0] = the final status.
.include "msf.inc"
.equ BUF,  0x1A00                   ; 512, 128 words
.equ DIV,  1
.equ TRIG, 0
.equ CTL,  0x803C                   ; armed, POST 60

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
        LDR    R6, #0
        MEM_A  R6
        MEM_W  R6
        MEM_W  R6
        MEM_W  R6
        MEM_W  R6
        MEM_W  R6
        MEM_W  R6
        MEM_W  R6
        MEM_W  R6
        LDR    R3, #2
        ST     R3, R5
        CAP_STAT R2
        LDR    R3, #4
        ST     R3, R2
wt:     CAP_STAT R2
        LDI    R3, CS_DONE
        AND    R3, R2, R3
        ADDS   R3, R3, R3
        JZ     wt
        LDR    R3, #0
        ST     R3, R2
h:      JMP    h
