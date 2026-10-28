; HIL capture probe with work (test_capture.WORK,
; test_cap_never_changes_core_timing): as cap_arm.s, but after arming a
; data-RAM-heavy loop runs (ST, LD, FIFO_PUT/GET 40 times) and R6 = its
; cycles from arming. RAM[2] = R5, RAM[3] = R6, RAM[4] = the status then;
; once done, RAM[0] = the final status, RAM[1] = the trigger position.
; With CTL's arm bit clear, done never comes: RAM[2..4] are still written.
.include "msf.inc"
.equ BUF,  0x1A00                   ; 512, 128 words
.equ DIV,  1
.equ TRIG, 0
.equ CTL,  0x806E                   ; armed, POST 110

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
        LDR    R6, #40
wl:     LDR    R7, #3
        ST     R7, R6
        LD     R1, R7
        NOP
        NOP
        FIFO_PUT R6
        FIFO_GET R1
        DECR   R6
        JNZ    wl
        CNT_R  R6
        SUBS   R6, R6, R5
        LDR    R3, #2
        ST     R3, R5
        INCR   R3
        ST     R3, R6
        CAP_STAT R2
        INCR   R3
        ST     R3, R2
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
h:      JMP    h
