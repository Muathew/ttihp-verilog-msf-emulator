; SD card in SPI mode: power-up clocks, CMD0, CMD8, ACMD41 until ready, CMD58 (OCR),
; then read block 0 with CMD17 and check its CRC-16.
;   pins  : SCK uio[2], MOSI uio[3], MISO uio[4] (input), CS uio[5]
;   output: data RAM [64 .. 79] the block's first 16 bytes, [80], [81] the OCR (high,
;           low), [82] the ACMD41 tries; uo_out = DONE, or BAD (a wrong response, a
;           card not ready, no data token, or a CRC that does not match)
; Sources: SD Association, "SD Specifications Part 1, Physical Layer Simplified
; Specification" (v9.00), chapter 7 (SPI mode): at least 74 clocks with CS high and
; MOSI high after power-up, at 100-400 kHz until initialised; commands are 6 bytes,
; 01 + index, a 32-bit argument, CRC7 + 1 (checked for CMD0, 0x95, and CMD8, 0x87); the
; R1 response comes 1-8 bytes later (NCR), bit 7 clear, bit 0 = in idle state; CMD8
; (argument 0x1AA) answers R7: R1 and 4 bytes echoing the voltage (1) and the check
; pattern (0xAA); ACMD41 = CMD55 then CMD41 (HCS, bit 30), repeated until R1 = 0;
; CMD58 answers R3, R1 and the OCR (bit 30 CCS); CMD17 answers R1, then after NAC
; bytes the start token 0xFE, the 512 bytes and their CRC-16 (x^16 + x^12 + x^5 + 1).
.include "msf.inc"
.equ SCK,  0x04
.equ MOSI, 0x08
.equ CS,   0x20
.equ HALF, 8                        ; delay counts: about 15 cycles a half bit (400 kHz)
.equ BAD,  0xEE

start:  PIN_WI   CS | MOSI          ; CS high, MOSI high, SCK low
        OE_SET   SCK | MOSI | CS
        LDR      R1, #(3 | 4 << 3)  ; SR out = MOSI (3), in = MISO (4), MSB first
        SER_CFG  R1
        LDR      R4, #10            ; 80 clocks with CS high
pw:     LDR      R1, #0xFF
        CALL     xfer
        DECR     R4
        JNZ      pw
        PIN_CLR  CS
        LDI      R2, c_cmd0         ; CMD0: R1 must be 0x01
        CALL     cmd
        LDR      R2, #0x01
        SUBS     R2, R1, R2
        JNZ      bad
        LDI      R2, c_cmd8         ; CMD8: R1 0x01, then 00 00 01 AA
        CALL     cmd
        LDR      R2, #0x01
        SUBS     R2, R1, R2
        JNZ      bad
        CALL     ff
        CALL     ff
        CALL     ff
        LDR      R2, #0x0F
        AND      R1, R1, R2
        LDR      R2, #0x01
        SUBS     R1, R1, R2         ; the voltage accepted
        JNZ      bad
        CALL     ff
        LDR      R2, #0xAA
        SUBS     R1, R1, R2         ; the check pattern
        JNZ      bad
        LDR      R4, #0             ; ACMD41 until R1 = 0 (at most 200 tries)
a41:    INCR     R4
        LDI      R2, c_cmd55
        CALL     cmd
        LDI      R2, c_acmd41
        CALL     cmd
        ADDS     R1, R1, R1
        JZ       ready
        LDR      R2, #200
        SUBS     R2, R4, R2
        JNZ      a41
        JMP      bad
ready:  LDR      R2, #82
        ST       R2, R4
        LDI      R2, c_cmd58        ; CMD58: R1 0x00 and the OCR
        CALL     cmd
        ADDS     R1, R1, R1
        JNZ      bad
        CALL     ff
        SWAP     R3, R1
        CALL     ff
        OR       R3, R3, R1
        LDR      R2, #80
        ST       R2, R3
        CALL     ff
        SWAP     R3, R1
        CALL     ff
        OR       R3, R3, R1
        LDR      R2, #81
        ST       R2, R3
        LDI      R2, c_cmd17        ; CMD17, block 0
        CALL     cmd
        ADDS     R1, R1, R1
        JNZ      bad
        LDI      R4, 1000           ; the start token, within 1000 bytes
tok:    CALL     ff
        LDR      R2, #0xFE
        SUBS     R2, R1, R2
        JZ       data
        DECR     R4
        JNZ      tok
        JMP      bad
data:   LDR      R6, #64            ; 512 bytes; the first 16 kept; CRC-16 over all
        LDI      R4, 512
        LDR      R0, #0             ; R0: the CRC
db:     CALL     ff
        LDR      R2, #80
        SUBS     R2, R6, R2
        JZ       d_crc              ; (16 kept: R6 stays at 80)
        ST       R6, R1
        INCR     R6
d_crc:  SWAP     R1, R1             ; crc ^= byte << 8, then 8 steps of x^16+x^12+x^5+1
        XOR      R0, R0, R1
        LDR      R3, #8
dc:     LDR      R2, #0
        SUBS     R2, R0, R2         ; N: the top bit (ADDS would set the flags: test first)
        JP       dc0
        ADDS     R0, R0, R0
        LDI      R2, 0x1021
        XOR      R0, R0, R2
        JMP      dc_n
dc0:    ADDS     R0, R0, R0
dc_n:   DECR     R3
        JNZ      dc
        DECR     R4
        JNZ      db
        CALL     ff                 ; the card's CRC
        SWAP     R3, R1
        CALL     ff
        OR       R3, R3, R1
        SUBS     R3, R3, R0
        JNZ      bad
        PIN_SET  CS
        CALL     ff
        UO_WI    DONE
        JMP      halt
bad:    PIN_SET  CS
        UO_WI    BAD
halt:   JMP      halt

; send the 6-byte command at R2, then clock 0xFF until R1 has bit 7 clear (NCR <= 8).
; R7 and R4 wait in RAM 90, 91 (xfer uses R1 and R5)
cmd:    LDR      R5, #90
        ST       R5, R7
        INCR     R5
        ST       R5, R4
        CALL     ff                 ; one byte of gap first
        LDR      R4, #6
cmd_b:  LD       R1, R2
        INCR     R2
        CALL     xfer
        DECR     R4
        JNZ      cmd_b
        LDR      R4, #9
cmd_r:  CALL     ff
        SWAP     R5, R1             ; bit 7 to bit 15, where N looks
        LDR      R7, #0             ; (R7 is saved in RAM 90)
        SUBS     R5, R5, R7         ; N: bit 7 set, no response yet
        JP       cmd_ok
        DECR     R4
        JNZ      cmd_r
cmd_ok: LDR      R5, #91
        LD       R4, R5
        DECR     R5
        LD       R7, R5
        RET
; one byte of 0xFF out, the byte in back in R1
ff:     LDR      R1, #0xFF
; one byte, mode 0: R1 out, the byte in back in R1; uses R5 and SR
xfer:   SWAP     R1, R1
        SR_LD    R1
        LDR      R1, #8
x_b:    LDR      R5, #HALF          ; SCK low: MISO settles (moved on at the fall)
x_d1:   DECR     R5
        JNZ      x_d1
        XFER                        ; MOSI <- bit, MISO in, just before the rise
        PIN_SET  SCK                ; the card takes MOSI
        LDR      R5, #HALF
x_d2:   DECR     R5
        JNZ      x_d2
        PIN_CLR  SCK                ; the card moves MISO on
        DECR     R1
        JNZ      x_b
        SR_RD    R1
        LDR      R5, #0xFF
        AND      R1, R1, R5
        RET

c_cmd0:   .word  0x40, 0x00, 0x00, 0x00, 0x00, 0x95
c_cmd8:   .word  0x48, 0x00, 0x00, 0x01, 0xAA, 0x87
c_cmd55:  .word  0x77, 0x00, 0x00, 0x00, 0x00, 0x65
c_acmd41: .word  0x69, 0x40, 0x00, 0x00, 0x00, 0x77
c_cmd58:  .word  0x7A, 0x00, 0x00, 0x00, 0x00, 0xFD
c_cmd17:  .word  0x51, 0x00, 0x00, 0x00, 0x00, 0x55
