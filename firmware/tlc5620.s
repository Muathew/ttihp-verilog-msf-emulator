; TLC5620 quad 8-bit DAC: load all four input registers, update the outputs together
; with an LDAC pulse, then hold LDAC low and set DAC A directly.
;   pins  : CLK uio[0], DATA uio[1], LOAD uio[2], LDAC uio[3], push-pull
;   table : words, an 11-bit command (A1 A0 RNG D7..D0), or UPDATE (pulse LDAC), or
;           LDAC_LO (hold LDAC low from here), 0xFFFF = the end
;   done  : uo_out = DONE
; Sources: TI TLC5620C/I data sheet (SLAS081): 11 bits, MSB first: A1 A0 (00 DAC A ..
; 11 DAC D), RNG (output range x1 or x2), D7-D0; DATA is taken on CLK's falling edge;
; LOAD low latches the word into the selected DAC's input register, and if LDAC is
; low the output follows at once; LDAC falling updates all four outputs from their
; registers. Timing at 12 MHz: data set-up and hold, CLK high and low 83 ns or more
; (data sheet 50 ns); LOAD and LDAC low for 333 ns (250 ns).
.include "msf.inc"
.equ CLK,     0x01
.equ DATA,    0x02
.equ LOAD,    0x04
.equ LDAC,    0x08
.equ UPDATE,  0x8000
.equ LDAC_LO, 0x4000
.equ END,     0xFFFF
.equ RNG,     0x100

start:  PIN_WI   LOAD | LDAC        ; CLK low, LOAD and LDAC high
        OE_SET   CLK | DATA | LOAD | LDAC
        LDR      R1, #1             ; SR out = DATA (pin 1), MSB first
        SER_CFG  R1
        LDI      R6, words
next:   LD       R3, R6
        INCR     R6
        INCR     R3
        JZ       fin
        DECR     R3
        LDI      R1, UPDATE
        SUBS     R1, R3, R1
        JZ       upd
        LDI      R1, LDAC_LO
        SUBS     R1, R3, R1
        JZ       hold
        SHL      R3, R3, #5         ; 11 bits, the first at bit 15
        SR_LD    R3
        LDR      R4, #11
bit:    XFER                        ; DATA <- the next bit
        PIN_SET  CLK
        DECR     R4
        PIN_CLR  CLK                ; the DAC takes it
        JNZ      bit
        PIN_CLR  LOAD               ; latch into the input register
        NOP
        NOP
        NOP
        PIN_SET  LOAD
        JMP      next
upd:    PIN_CLR  LDAC               ; all four outputs from their registers
        NOP
        NOP
        NOP
        PIN_SET  LDAC
        JMP      next
hold:   PIN_CLR  LDAC
        JMP      next
fin:    UO_WI    DONE
halt:   JMP      halt

; A = 64 (x1), B = 128 (x2), C = 192 (x1), D = 255 (x2), all out together; then
; with LDAC low, A = 16 at once
words:  .word    0 << 9 | 64, 1 << 9 | RNG | 128, 2 << 9 | 192, 3 << 9 | RNG | 255
        .word    UPDATE
        .word    LDAC_LO, 0 << 9 | 16
        .word    END
