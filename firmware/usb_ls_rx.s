; USB 1.x low-speed packet receiver: RX engine in continuous NRZI mode with
; bit de-stuffing and edge resync (8 cycles/bit at 12 MHz) on D-; EOP (SE0 =
; both lines low) detected from the pins; CRC16 checked with the CRC unit.
;   pins   : D- uio[0], D+ uio[1] (inputs)
;   output : data RAM [64 ..] = received bytes (SYNC, PID, data..., CRC16)
;            data RAM [63]    = byte count
;            uo_out = 0x80 | 0x01 (CRC16 residual ok) or 0x80 | 0x02 (bad)
.include "msf.inc"
.equ BIT, 8

start:
        LDR      R1, #0
        TMO      R1
        LDR      R1, #BIT
        RX_DIV   R1
        LDI      R1, (RX_EN | RX_RESYNC | RX_LSB | RX_DESTUFF | RX_NRZI | (8 - 1) << 8 | 0)
        RX_CFG   R1                 ; NRZI reference = idle J
        UO_WI    0x40               ; armed
        LDR      R2, #64
        LDR      R0, #3             ; D+/D- mask
rx:     RX_POP   R3
        ST       R2, R3
        INCR     R2
        ; EOP check: sample the lines ~1 and ~2 bit-times after the last
        ; data bit (the second sample covers a trailing stuffed bit).
        ; SE0 = both lines low in either sample.
        LDR      R5, #2
w1:     DECR     R5
        JNZ      w1
        PIN_R    R4
        AND      R4, R4, R0
        ADDS     R4, R4, R4         ; Z=1 iff SE0
        JZ       eop
        LDR      R5, #2
w2:     DECR     R5
        JNZ      w2
        PIN_R    R4
        AND      R4, R4, R0
        ADDS     R4, R4, R4
        JNZ      rx                 ; not SE0 -> next byte
eop:    LDR      R1, #0
        RX_CFG   R1                 ; stop the receiver
        LDR      R1, #64
        SUBS     R4, R2, R1         ; byte count
        LDR      R1, #63
        ST       R1, R4
        ; ---- CRC16 over data + received CRC (bytes 2 .. count-1) ----
        LDI      R1, 0xA001
        CRC_POLL R1
        LDR      R1, #0
        CRC_POLH R1
        CRC_SETH R1
        LDI      R1, 0xFFFF
        CRC_SETL R1
        LDR      R1, #(CRC_REFL | 7)
        CRC_CFG  R1
        LDR      R2, #66
        LDR      R1, #2
        SUBS     R4, R4, R1
cl:     LD       R1, R2
        CRC_W    R1
        INCR     R2
        DECR     R4
        JNZ      cl
        CRC_RDL  R1
        LDI      R5, 0xB001         ; residual of the reflected USB CRC16
        SUBS     R1, R1, R5
        JNZ      bad
        UO_WI    0x81
halt:   JMP      halt
bad:    UO_WI    0x82
halt2:  JMP      halt2
