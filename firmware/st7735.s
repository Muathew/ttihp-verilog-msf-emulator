; ST7735 TFT display controller over 4-wire SPI: reset, wake, set 16-bit colour, turn
; the display on, then fill a 4 x 2 pixel block with red through a column/row window.
;   pins  : SCL uio[2], SDA uio[3], CSX uio[5], D/CX uio[4] (0 command, 1 data)
;   table : seq, words: CMD | c (a command), DATA | d, WAIT | n (n x 60,000 cycles),
;           0xFFFF = the end
;   W120  : the 120 ms waits, in pieces of 60,000 cycles (24)
;   done  : uo_out = DONE
; Sources: Sitronix ST7735S data sheet (v1.x): 4-line serial interface, SDA taken on
; SCL's rising edge, MSB first, D/CX sampled with the byte's last bit; SWRESET (0x01)
; then 120 ms before the next command; SLPOUT (0x11) then 120 ms; COLMOD (0x3A) 0x05:
; 16 bits a pixel (RGB 5-6-5); MADCTL (0x36); CASET (0x2A) and RASET (0x2B), start and
; end as 16-bit pairs; RAMWR (0x2C), pixels row by row in the window; DISPON (0x29).
.include "msf.inc"
.equ SCL,  0x04
.equ SDA,  0x08
.equ DC,   0x10
.equ CSX,  0x20
.equ CMD,  0x0000
.equ DATA, 0x0100
.equ WAIT, 0x4000
.equ END,  0xFFFF
.equ W120, 24

start:  PIN_WI   CSX                ; CSX high, SCL low
        OE_SET   SCL | SDA | DC | CSX
        LDR      R1, #3             ; SR out = SDA (pin 3), MSB first
        SER_CFG  R1
        LDI      R6, seq
next:   LD       R3, R6
        INCR     R6
        INCR     R3
        JZ       fin
        DECR     R3
        LDI      R1, WAIT
        AND      R1, R3, R1
        ADDS     R1, R1, R1
        JNZ      wait
        LDI      R1, DATA
        AND      R1, R3, R1
        ADDS     R1, R1, R1
        PIN_CLR  DC                 ; a command
        JZ       dc_set
        PIN_SET  DC                 ; data
dc_set: PIN_CLR  CSX
        SHL      R3, R3, #7         ; the byte to bits 15-8
        SHL      R3, R3, #1
        SR_LD    R3
        LDR      R4, #8
bit:    XFER
        PIN_SET  SCL                ; taken
        DECR     R4
        PIN_CLR  SCL
        JNZ      bit
        PIN_SET  CSX
        JMP      next
wait:   LDR      R1, #0xFF          ; n pieces of 60,000 cycles
        AND      R2, R3, R1
        CNT_R    R5
        LDI      R1, 60000
w_l:    ADDS     R5, R5, R1
        WAIT_CMP R5
        DECR     R2
        JNZ      w_l
        JMP      next
fin:    UO_WI    DONE
halt:   JMP      halt

.equ RED_H, 0xF8                    ; RGB 5-6-5 red: 0xF800
seq:    .word    CMD | 0x01, WAIT | W120             ; SWRESET
        .word    CMD | 0x11, WAIT | W120             ; SLPOUT
        .word    CMD | 0x3A, DATA | 0x05             ; COLMOD: 16 bits a pixel
        .word    CMD | 0x36, DATA | 0x00             ; MADCTL: rows and columns as they are
        .word    CMD | 0x29                          ; DISPON
        .word    CMD | 0x2A, DATA | 0, DATA | 10, DATA | 0, DATA | 13   ; columns 10-13
        .word    CMD | 0x2B, DATA | 0, DATA | 20, DATA | 0, DATA | 21   ; rows 20-21
        .word    CMD | 0x2C                          ; RAMWR: 8 red pixels
        .word    DATA | RED_H, DATA | 0, DATA | RED_H, DATA | 0, DATA | RED_H, DATA | 0
        .word    DATA | RED_H, DATA | 0, DATA | RED_H, DATA | 0, DATA | RED_H, DATA | 0
        .word    DATA | RED_H, DATA | 0, DATA | RED_H, DATA | 0
        .word    END
