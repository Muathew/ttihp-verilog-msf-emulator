; 10BASE-T transmitter at a 20 MHz clock (one cycle per Manchester half-bit
; = 10 Mbit/s). Sends one frame then keeps the link up with normal link pulses.
;   pins   : TD+ uio[2], TD- uio[3] (differential pair -> magnetics / resistor net)
;   input  : data RAM [0] = N frame words, [1 .. N] = DA, SA, EtherType, payload
;            as little-endian byte pairs (>= 30 words = the 60-byte minimum)
;   output : 7 x 0x55 preamble, SFD 0xD5, the frame, FCS (CRC-32 from the CRC
;            unit, 16 bits per CRC_W), TP_IDL (TD+ high 300 ns), then an NLP
;            (100 ns positive pulse) every NLP_CHUNKS * NLP_CHUNK cycles
;            (16 ms by default). uo_out = DONE after the frame, then the
;            low bits of uo_out count link pulses.
.include "msf.inc"
.equ NLP_CHUNK,  64000        ; 3.2 ms at 20 MHz (WAIT_CMP spans < 65536)
.equ NLP_CHUNKS, 5            ; 5 x 3.2 ms = 16 ms between link pulses

start:
        PIN_WI   0x00               ; idle: no differential voltage
        OE_SET   0x0C
        ; ---- CRC-32: reflected 0xEDB88320, init 0xFFFFFFFF, 16 bits per CRC_W ----
        LDI      R1, 0x8320
        CRC_POLL R1
        LDI      R1, 0xEDB8
        CRC_POLH R1
        LDI      R1, 0xFFFF
        CRC_SETL R1
        CRC_SETH R1
        LDR      R1, #(CRC_REFL | 15)
        CRC_CFG  R1
        ; ---- Manchester TX on TD+/TD-, LSB-first 16-bit words ----
        LDR      R1, #1
        TX_DIV   R1                 ; one cycle per half-bit
        LDI      R1, (TX_EN | TX_RELEASE | TX_LSB | TX_MANCH | TX_DIFF | (16 - 1) << 8 | 2)
        TX_CFG   R1                 ; release mode: pins show OUT (idle) until the preamble
        LDI      R1, 0x5555
        TX_PUSH  R1
        PIN_WI   0x04               ; OUT = TP_IDL (TD+ high): shown when the frame ends
        TX_PUSH  R1
        TX_PUSH  R1
        LDI      R1, 0xD555         ; last preamble byte + SFD
        TX_PUSH  R1
        LDR      R2, #0
        LD       R6, R2             ; N
        LDR      R2, #1
fl:     LD       R1, R2
        TX_PUSH  R1
        CRC_W    R1
        INCR     R2
        DECR     R6
        JNZ      fl
        CRC_RDL  R1
        CRC_RDH  R3
        LDI      R4, 0xFFFF
        XOR      R1, R1, R4         ; FCS = ~CRC, sent LSB first
        XOR      R3, R3, R4
        TX_PUSH  R1
        TX_PUSH  R3
        TX_WAIT                     ; completes in the first TP_IDL cycle (E)
        NOP
        NOP
        NOP
        NOP
        PIN_WI   0x00               ; idle from E+6: TP_IDL lasted 300 ns
        UO_WI    DONE
        ; ---- normal link pulses, drift-free schedule ----
        LDR      R7, #0
        CNT_R    R5
        LDI      R6, NLP_CHUNK
nlp:    LDR      R4, #NLP_CHUNKS
ch:     ADDS     R5, R5, R6
        WAIT_CMP R5
        DECR     R4
        JNZ      ch
        PIN_WI   0x04               ; 100 ns positive pulse (2 cycles)
        NOP
        PIN_WI   0x00
        INCR     R7
        OUT_W    R7
        JMP      nlp
