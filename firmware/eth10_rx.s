; 10BASE-T receiver, for a 50 MHz clock: 10 Mbit/s is 5 cycles a bit, a half-bit
; 2.5. The RX engine's phase-tracking Manchester mode (mode 3) recovers the clock
; from the bit centres; the program skips the preamble, checks the SFD, keeps the
; frame and checks its FCS with the CRC unit as the bytes come in (a byte every 40
; cycles, the loop takes about 18).
;   pin    : RD uio[0] (the receive pair through a comparator; idle low)
;   output : data RAM [0 .. 95] = the frame after the SFD (destination .. FCS), a
;            byte a word, its first 96 bytes; data RAM [100] = its length in bytes
;            (the FCS is checked over all of it, up to 1518 bytes)
;            uo_out = 0x81 FCS right, 0x82 FCS wrong, 0x83 no SFD after the preamble
; Sources: IEEE 802.3 clause 7.3 (Manchester: the second half of a bit cell is the
; bit, low->high = 1) and clause 14 (10BASE-T: 10 Mbit/s +-0.01 %, the receiver
; takes up to 13.5 ns of edge jitter), clause 3 (preamble of 7 x 0x55, SFD 0xD5,
; bytes LSB first; FCS = CRC-32, reflected 0xEDB88320, init all ones; over the
; frame and its FCS the register ends at 0xDEBB20E3).
; The engine takes the frame from its first transition, the first preamble bit's
; centre (idle low, a 1 is low then high), so the bytes are aligned from there.
.include "msf.inc"
.equ STEP,   13107                  ; RX_DIV: 65536 x 10 MHz / 50 MHz
.equ GAP,    200                    ; RX_POP time-out: 5 byte times without one ends a frame
.equ BUF,    0
.equ LEN,    100
.equ KEEP,   96                     ; bytes kept in data RAM
.equ OK,     0x81
.equ BADFCS, 0x82
.equ NOSFD,  0x83

start:  LDI      R1, STEP
        RX_DIV   R1
        LDI      R1, 0x8320         ; CRC-32, reflected, 8 bits per CRC_W
        CRC_POLL R1
        LDI      R1, 0xEDB8
        CRC_POLH R1
        LDI      R1, 0xFFFF
        CRC_SETL R1
        CRC_SETH R1
        LDR      R1, #(CRC_REFL | 7)
        CRC_CFG  R1
        LDI      R1, (RX_EN | RX_LSB | RX_MANPLL | (8 - 1) << 8 | 0)
        RX_CFG   R1
        LDR      R1, #0
        TMO      R1                 ; the first byte: as long as it takes
        UO_WI    0x40               ; armed
        LDI      R5, GAP
        LDR      R4, #0x55
        LDR      R7, #0xD5
pre:    RX_POP   R3                 ; preamble bytes, then the SFD
        TMO      R5                 ; from now a gap ends the frame
        SUBS     R2, R3, R4
        JZ       pre
        SUBS     R2, R3, R7
        JNZ      nosfd
        LDR      R6, #BUF
        LDR      R0, #KEEP
data:   RX_POP   R3
        JZ       end                ; timed out: the frame has ended
        SUBS     R2, R6, R0         ; N: still under KEEP
        JP       count
        ST       R6, R3
count:  INCR     R6
        CRC_W    R3                 ; over the frame and its FCS
        JMP      data
end:    LDR      R1, #LEN
        ST       R1, R6             ; (BUF = 0: R6 is the length)
        CRC_RDL  R1
        LDI      R2, 0x20E3
        SUBS     R1, R1, R2
        JNZ      bad
        CRC_RDH  R1
        LDI      R2, 0xDEBB
        SUBS     R1, R1, R2
        JNZ      bad
        UO_WI    OK
halt:   JMP      halt
bad:    UO_WI    BADFCS
        JMP      halt
nosfd:  UO_WI    NOSFD
        JMP      halt
