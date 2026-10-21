; Reporter thread for the bus monitors (needs ring.inc): sends every event
; the watching thread queues to the host as two UART bytes, code then data,
; 8N1 on REPORT_PIN at REPORT_BIT cycles per bit (defaults: uio[0], 12 =
; 1 Mbaud at 12 MHz; .equ them again after the .include to change). Events
; the queue had to drop are reported as ('O', how many, at most 255).
; The monitor provides `xlat`: R1 = queued word -> R1 = event (code << 8 |
; data) or 0 (nothing), and R2 = a second event or 0; it may use R4, R5.
; That lets the watching thread queue raw words cheaply and leaves the
; formatting to this thread.
; Start it with TSTART on `report`; it never writes TMO, and retries a
; TX_PUSH that the other thread's timeout cut short (nothing is pushed then).
.equ REPORT_PIN, 0
.equ REPORT_BIT, 12

report: PIN_SET  (1 << REPORT_PIN) ; idle high before the pin is driven
        OE_SET   (1 << REPORT_PIN)
        LDI      R1, REPORT_BIT
        TX_DIV   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | REPORT_PIN)
        TX_CFG   R1
        LDI      R0, 0x200         ; stop bit
        LDR      R6, #RING         ; queue: next slot to read
        LDR      R3, #0            ; dropped events already reported
rp:     CALL     get
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
        LDR      R4, #RING_DROP
        LD       R4, R4
        SUBS     R2, R4, R3        ; newly dropped
        JZ       rp
        MOV      R3, R4
        LDI      R5, 0x100
        SUBS     R5, R2, R5
        JN       rp_n
        LDR      R2, #0xFF         ; saturate
rp_n:   MOV      R1, R2
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
        JZ       tx_p              ; timed out (shared TMO): nothing pushed, retry
        RET
