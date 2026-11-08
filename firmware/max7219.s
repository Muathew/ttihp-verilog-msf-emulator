; MAX7219 8-digit LED display driver: set it up and show a date, 08.11.2026.
;   pins  : CLK uio[2], DIN uio[3], LOAD (CS) uio[5], push-pull
;   table : words, one 16-bit word a register write (address << 8 | data), 0xFFFF = the end
;   done  : uo_out = DONE
; Sources: Maxim MAX7219/MAX7221 data sheet (19-4452, rev 4): 16-bit words, D15-D12
; ignored, D11-D8 the register, D7-D0 the data, MSB first; DIN is taken on CLK's
; rising edge and the last 16 bits are latched on LOAD's rising edge; registers:
; digits 0-7 at 0x01-0x08, decode mode 0x09, intensity 0x0A, scan limit 0x0B,
; shutdown 0x0C (1 = normal operation), display test 0x0F; Code B font, D7 the
; decimal point. Timing at 12 MHz: every level holds for an instruction (83 ns) or
; more, past the data sheet's 50 ns clock high and low times and 25 ns set-up.
.include "msf.inc"
.equ CLK,  0x04
.equ DIN,  0x08
.equ LOAD, 0x20
.equ END,  0xFFFF
.equ DP,   0x80

start:  PIN_WI   LOAD               ; CLK low, LOAD high
        OE_SET   CLK | DIN | LOAD
        LDR      R1, #3             ; SR out = DIN (pin 3), MSB first
        SER_CFG  R1
        LDI      R6, words
next:   LD       R3, R6
        INCR     R6
        INCR     R3
        JZ       fin
        DECR     R3
        PIN_CLR  LOAD
        SR_LD    R3
        LDR      R4, #16
bit:    XFER                        ; DIN <- the next bit
        PIN_SET  CLK                ; the MAX7219 takes it
        DECR     R4
        PIN_CLR  CLK
        JNZ      bit
        PIN_SET  LOAD               ; latched
        JMP      next
fin:    UO_WI    DONE
halt:   JMP      halt

; display test off, scan all eight digits, Code B on all, half intensity, then the
; digits (digit 7 at the left), then out of shutdown
words:  .word    0x0F00, 0x0B07, 0x09FF, 0x0A08
        .word    0x0800, 0x0708 | DP, 0x0601, 0x0501 | DP
        .word    0x0402, 0x0300, 0x0202, 0x0106
        .word    0x0C01, END
