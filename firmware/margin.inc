; Margin finder, the sweep: for each value p of one timing parameter, from
; SWEEP_LO to SWEEP_HI, runs the firmware's `trial` REPEATS times, and reports how
; many passed. No host, analyser or second board takes part: load the program,
; connect the device, read the table.
;
; The including firmware provides:
;   setup: pin levels and directions (a subroutine, with CALL)
;   trial: R3 = p; one transaction with the device; ends with JMP tdone with
;          R1 = 1 if the device answered correctly, else 0. May use every register.
;
; Reported on REPORT_PIN, 8N1 at REPORT_BIT cycles a bit (1 Mbaud at 12 MHz):
;   'P', p low, p high, passes       for each p, as soon as it is measured
;   'R', lo low, lo high, hi low, hi high
;        the unbroken run of values that always passed, the one holding NOM if
;        there is one (else the first). lo = 0: nothing passed every time.
; Also left in data RAM for a host that cannot read the pin: M_LO, M_HI, and from
; M_TABLE on the pairs p, passes, as far as they fit. Then uo_out = DONE.
.include "msf.inc"
.equ REPORT_PIN, 0
.equ REPORT_BIT, 12
.equ REPEATS,  8
.equ M_LO,     8
.equ M_HI,     9
.equ M_P,      10
.equ M_I,      11
.equ M_OK,     12
.equ M_OPEN,   13
.equ M_T,      14
.equ M_TABLE,  16
.equ M_END,    126

start:  LDR      R1, #0
        TMO      R1                ; no timeouts unless a trial sets one
        CALL     setup
        PIN_SET  (1 << REPORT_PIN) ; idle high before the pin is driven
        OE_SET   (1 << REPORT_PIN)
        LDR      R1, #0
        LDR      R2, #M_LO
        ST       R2, R1
        LDR      R2, #M_HI
        ST       R2, R1
        LDR      R2, #M_OPEN
        ST       R2, R1
        LDR      R2, #M_T
        LDR      R1, #M_TABLE
        ST       R2, R1
        LDI      R1, SWEEP_LO
        LDR      R2, #M_P
        ST       R2, R1

point:  LDR      R1, #REPEATS
        LDR      R2, #M_I
        ST       R2, R1
        LDR      R1, #0
        LDR      R2, #M_OK
        ST       R2, R1
again:  LDR      R2, #M_P
        LD       R3, R2
        JMP      trial
tdone:  LDR      R2, #M_OK
        LD       R4, R2
        ADDS     R4, R4, R1
        ST       R2, R4
        LDR      R2, #M_I
        LD       R4, R2
        DECR     R4
        ST       R2, R4
        JNZ      again

; ---- p is measured: R3 = p, R4 = passes
        LDR      R2, #M_P
        LD       R3, R2
        LDR      R2, #M_OK
        LD       R4, R2
        LDR      R2, #M_T          ; the table, while it fits
        LD       R5, R2
        LDR      R6, #M_END
        SUBS     R6, R5, R6
        JP       notab
        ST       R5, R3
        INCR     R5
        ST       R5, R4
        INCR     R5
        ST       R2, R5
notab:  LDR      R1, #REPEATS
        SUBS     R1, R4, R1
        JNZ      failed
        LDR      R2, #M_LO         ; every repeat passed: in a run
        LD       R5, R2
        ADDS     R5, R5, R5
        JNZ      inrun
        ST       R2, R3            ; the run starts here
        LDR      R2, #M_OPEN
        LDR      R5, #1
        ST       R2, R5
inrun:  LDR      R2, #M_OPEN
        LD       R5, R2
        ADDS     R5, R5, R5
        JZ       report
        LDR      R2, #M_HI
        ST       R2, R3
        JMP      report
failed: LDR      R2, #M_OPEN       ; a run that is still open ends here
        LD       R5, R2
        ADDS     R5, R5, R5
        JZ       report
        LDR      R5, #0
        ST       R2, R5
        LDI      R5, NOM
        SUBS     R5, R5, R3        ; it held NOM if p > NOM: keep it
        JN       report
        LDR      R2, #M_LO         ; it ended before NOM: forget it
        LDR      R5, #0
        ST       R2, R5
        LDR      R2, #M_HI
        ST       R2, R5
report: LDI      R1, REPORT_BIT
        TX_DIV   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | REPORT_PIN)
        TX_CFG   R1
        LDR      R1, #'P'
        CALL     txb
        MOV      R1, R3
        CALL     txb
        SWAP     R1, R3
        CALL     txb
        MOV      R1, R4
        CALL     txb
        TX_WAIT
        LDI      R1, SWEEP_HI
        SUBS     R1, R1, R3
        JZ       finish
        INCR     R3
        LDR      R2, #M_P
        ST       R2, R3
        JMP      point

finish: LDR      R1, #'R'
        CALL     txb
        LDR      R2, #M_LO
        LD       R3, R2
        MOV      R1, R3
        CALL     txb
        SWAP     R1, R3
        CALL     txb
        LDR      R2, #M_HI
        LD       R3, R2
        MOV      R1, R3
        CALL     txb
        SWAP     R1, R3
        CALL     txb
        TX_WAIT
        UO_WI    DONE
halt:   JMP      halt

; R1[7:0] -> one 8N1 frame; uses R1, R2
txb:    LDR      R2, #0xFF
        AND      R1, R1, R2
        ADDS     R1, R1, R1
        LDI      R2, 0x200
        OR       R1, R1, R2
        TX_PUSH  R1
        RET
