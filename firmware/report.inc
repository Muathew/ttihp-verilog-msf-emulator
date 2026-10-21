; Reporter thread for the bus monitors: sends every event the watching
; thread pushes into the FIFO to the host as two UART bytes, code then data,
; 8N1 on REPORT_PIN at REPORT_BIT cycles per bit (defaults: uio[0], 12 =
; 1 Mbaud at 12 MHz; .equ them again after the .include to change). Words
; a full FIFO dropped are reported as ('O', how many: at most 127 at once).
; The monitor provides `xlat`: R1 = queued word -> R1 = event (code << 8 |
; data) or 0 (nothing), and R2 = a second event or 0; it may use R4, R5.
; That lets the watching thread push raw words cheaply and leaves the
; formatting to this thread.
; Start it with TSTART on `report`; it keeps the TMO it starts with and
; retries a TX_PUSH that timed out (nothing is pushed then).
.equ REPORT_PIN, 0
.equ REPORT_BIT, 12

report: PIN_SET  (1 << REPORT_PIN) ; idle high before the pin is driven
        OE_SET   (1 << REPORT_PIN)
        LDI      R1, REPORT_BIT
        TX_DIV   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | REPORT_PIN)
        TX_CFG   R1
        LDI      R0, 0x200         ; stop bit
        LDR      R3, #0            ; drops already reported (modulo 128)
rp:     FIFO_GET R1
        JZ       rp_drop
        CALL     xlat
        ADDS     R4, R1, R1        ; (xlat may return 0: nothing to report)
        JZ       rp_drop
        CALL     tx_ev
        MOV      R1, R2            ; a second event?
        ADDS     R2, R2, R2
        JZ       rp_drop
        CALL     tx_ev
rp_drop:
        FIFO_STAT R4
        SHR      R4, R4, #7
        SHR      R4, R4, #2        ; drops, modulo 128
        SUBS     R2, R4, R3        ; newly dropped
        JZ       rp
        MOV      R3, R4
        LDR      R5, #0x7F
        AND      R1, R2, R5
        LDH      R1, #'O'
        CALL     tx_ev
        JMP      rp

; R1 = event -> its two bytes; keeps R1, R2, R3
tx_ev:  MOV      R4, R7
        SHR      R5, R1, #7
        SHR      R5, R5, #1        ; code
        CALL     tx_byte
        SWAP     R5, R1
        SHR      R5, R5, #7
        SHR      R5, R5, #1        ; data
        CALL     tx_byte
        JR       R4
; R5 = byte -> one 8N1 frame (start bit in bit 0, stop bit in bit 9)
tx_byte:
        ADDS     R5, R5, R5
        OR       R5, R5, R0
tx_p:   TX_PUSH  R5
        JZ       tx_p              ; timed out: nothing pushed, retry
        RET
