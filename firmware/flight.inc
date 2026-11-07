; Reporter thread with a flight recorder: report.inc, plus the capture engine
; recording the uio pins into a ring all the time. The first time an event the
; monitor calls a fault is reported, this thread keeps FR_POST more words, stops
; the ring and sends it, so the host gets the waveform around the fault as well
; as the fault, with no other instrument on the bus.
;
; The monitor provides, besides `xlat` (see report.inc):
;   isfault: R1 = event (code << 8 | data) -> Z = 1 if a fault; may use R4, R5
; Sent, after the fault's events: ('D', FR_N), then the 2 << FR_N ring words,
; oldest first, 16 bits each, low byte first; then ('E', 1 if the engine lost a
; word, else 0). Words never written read 0 and have 0 ticks. Reporting then goes
; on as usual, but the ring stays stopped: this is the first fault's waveform.
; While the ring is being sent (about 5 ms) events wait in the FIFO, and if it
; fills they are counted and reported ('O'), as ever.
;
; The stop uses the engine's trigger: armed with a condition that can never hold
; (mask 0, value 1), then CAP_TRIG with mask 0, value 0 makes the next sample the
; trigger word, and the engine stops by itself FR_POST words later, keeping its
; place in the ring. (Writing CAP_CTL would stop it too, but also resets its place.)
; The ring records every uio pin, so the reporter's own UART on REPORT_PIN takes
; a share of it; tools/msf_capture.py can drop that pin.
.equ REPORT_PIN, 0
.equ REPORT_BIT, 12
.equ FR_N,    7                    ; ring of 2 << FR_N words ...
.equ FR_BASE, 768                  ; ... in the size-aligned block here
.equ FR_POST, 32                   ; words kept after the fault
.equ FR_SIZE, 2 << FR_N
.equ FR_BLOCK, FR_BASE & ~(FR_SIZE - 1)

report: PIN_SET  (1 << REPORT_PIN) ; idle high before the pin is driven
        OE_SET   (1 << REPORT_PIN)
        LDI      R1, REPORT_BIT
        TX_DIV   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | REPORT_PIN)
        TX_CFG   R1
        LDI      R1, FR_BLOCK      ; clear the ring: unwritten words read 0 ticks
        MEM_A    R1
        LDI      R2, FR_SIZE
        LDR      R1, #0
fr_clr: MEM_W    R1
        DECR     R2
        JNZ      fr_clr
        LDI      R1, FR_BASE | FR_N << CAP_N
        CAP_BUF  R1
        LDR      R1, #1
        CAP_DIV  R1
        LDI      R1, 0x0100        ; a trigger that can never hold
        CAP_TRIG R1
        LDI      R1, FR_POST | CAP_CHG | CAP_ARM
        CAP_CTL  R1
        LDI      R0, 0x200         ; stop bit
        LDR      R3, #0            ; drops already reported (modulo 128)
        LDR      R6, #0            ; 0 = armed, 1 = a fault to dump, 2 = dumped
rp:     FIFO_GET R1
        JZ       rp_drop
        CALL     xlat
        ADDS     R4, R1, R1        ; (xlat may return 0: nothing to report)
        JZ       rp_drop
        CALL     tx_ev
        CALL     isfault
        JNZ      rp_2
        ADDS     R4, R6, R6
        JNZ      rp_2
        LDR      R6, #1
rp_2:   MOV      R1, R2            ; a second event?
        ADDS     R2, R2, R2
        JZ       rp_f
        CALL     tx_ev
        CALL     isfault
        JNZ      rp_f
        ADDS     R4, R6, R6
        JNZ      rp_f
        LDR      R6, #1
rp_f:   LDR      R4, #1
        SUBS     R4, R6, R4
        JNZ      rp_drop

; ---- the first fault: keep FR_POST more words, then send the ring
        LDR      R1, #0
        CAP_TRIG R1                ; mask 0, value 0: holds from the next sample
fr_w:   CAP_STAT R1
        LDI      R2, CS_DONE
        AND      R2, R1, R2
        ADDS     R2, R2, R2
        JZ       fr_w
        LDI      R1, 'D' << 8 | FR_N
        CALL     tx_ev
        CAP_STAT R1                ; bits 9:0: the oldest word, once it has wrapped
        LDI      R4, 0x3FF
        AND      R6, R1, R4
        LDI      R2, FR_SIZE
fr_d:   LD       R1, R6
        SWAP     R1, R1            ; tx_ev sends the high byte first
        CALL     tx_ev
        INCR     R6
        LDI      R4, FR_SIZE - 1
        AND      R4, R6, R4
        ADDS     R4, R4, R4        ; Z: ran off the block's end
        JNZ      fr_n
        LDI      R4, FR_SIZE
        SUBS     R6, R6, R4
fr_n:   DECR     R2
        JNZ      fr_d
        CAP_STAT R1                ; bit 12: the engine lost a word
        SHR      R1, R1, #7
        SHR      R1, R1, #5
        LDR      R4, #1
        AND      R1, R1, R4
        LDH      R1, #'E'
        CALL     tx_ev
        LDR      R6, #2

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

; R1 = event -> its two bytes; keeps R1, R2, R3, R6
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
