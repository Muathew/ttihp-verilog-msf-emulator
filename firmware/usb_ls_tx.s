; USB 1.x low-speed packet transmitter (1.5 Mbit/s at a 12 MHz clock = 8 cycles/bit).
; The SERDES does NRZI + bit stuffing on a differential pair; the CRC unit
; computes the CRC16; in release mode the pins show OUT (J before the packet,
; SE0 after it) except while a bit is on the line, so the EOP timing is exact.
;   pins   : D- uio[0] (TX line), D+ uio[1] (complement). Low speed: J = D- high.
;   input  : data RAM [0] = PID byte, [1] = payload length n, [2 ..] = payload
;   output : SYNC, PID, payload, CRC16, EOP (SE0 2 bits + J 1 bit), then the
;            pins are released (idle J by the host pull-up); uo_out = DONE
.include "msf.inc"
.equ BIT, 8

start:
        PIN_WI   0x01               ; OUT = J (D-=1, D+=0)
        OE_SET   0x03
        ; ---- CRC16 (reflected 0xA001, init 0xFFFF) over the payload ----
        LDI      R1, 0xA001
        CRC_POLL R1
        LDR      R1, #0
        CRC_POLH R1
        CRC_SETH R1
        LDI      R1, 0xFFFF
        CRC_SETL R1
        LDR      R1, #(CRC_REFL | 7)
        CRC_CFG  R1
        LDR      R2, #1
        LD       R6, R2             ; n
        MOV      R4, R6
        LDR      R2, #2
crc:    ADDS     R5, R4, R4
        JZ       crcd
        LD       R1, R2
        CRC_W    R1
        INCR     R2
        DECR     R4
        JMP      crc
crcd:   CRC_RDL  R3
        LDI      R1, 0xFFFF
        XOR      R3, R3, R1         ; CRC16 is sent inverted
        ; ---- transmit ----
        LDR      R1, #BIT
        TX_DIV   R1
        LDI      R1, (TX_EN | TX_RELEASE | TX_IDLE1 | TX_LSB | TX_STUFF | TX_NRZI | TX_DIFF | (8 - 1) << 8 | 0)
        TX_CFG   R1                 ; release mode: pins show OUT (= J) until SYNC
        LDR      R1, #0x80          ; SYNC = KJKJKJKK
        TX_PUSH  R1
        PIN_WI   0x00               ; OUT = SE0: appears the moment the stream ends
        LDR      R2, #0
        LD       R1, R2
        TX_PUSH  R1                 ; PID
        LDR      R2, #2
tx:     ADDS     R5, R6, R6
        JZ       txd
        LD       R1, R2
        TX_PUSH  R1
        INCR     R2
        DECR     R6
        JMP      tx
txd:    TX_PUSH  R3                 ; CRC low byte (8-bit words)
        SWAP     R3, R3
        TX_PUSH  R3                 ; CRC high byte
        TX_WAIT                     ; completes in the first SE0 cycle (call it E)
        CNT_R    R5                 ; E+1
        LDR      R1, #(2 * BIT - 3)
        ADDS     R5, R5, R1
        WAIT_CMP R5                 ; target E+2*BIT-2 -> next insn at E+2*BIT-1
        PIN_WI   0x01               ; J on the pad from E+2*BIT: SE0 lasted 2 bits
        NOP
        NOP
        NOP
        NOP
        NOP
        NOP
        NOP
        OE_CLR   0x03               ; after one J bit: release to the pull-up
        UO_WI    DONE
halt:   JMP      halt
