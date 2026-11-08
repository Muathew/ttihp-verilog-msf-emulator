; ADF4351 wideband synthesizer: program the six registers for 2.400 GHz from a 25 MHz
; reference (integer-N), R5 first and R0 last, as the data sheet asks.
;   pins  : CLK uio[2], DATA uio[3], LE uio[5], push-pull
;   table : regs, each register as two 16-bit halves, high first; 0xFFFF = the end
;   done  : uo_out = DONE
; Sources: Analog Devices ADF4351 data sheet (Rev. A): 32-bit words, MSB first, DATA
; taken on CLK's rising edge, the word latched on LE's rising edge; the three LSBs
; select the register; after power-up program R5, R4, R3, R2, R1, R0. The values:
; R0 INT 96, FRAC 0; R1 prescaler 8/9, phase 1, MOD 2; R2 MUXOUT digital lock
; detect, R = 1, charge pump 2.5 mA, LDF and LDP for integer-N, positive phase
; detector; R3 clock divider 150, ABP 3 ns; R4 fundamental feedback, RF divider 1,
; band-select clock divider 200 (125 kHz), RF output on at +5 dBm; R5 lock-detect
; pin digital, reserved bits 11. f_PFD = 25 MHz, VCO = 96 x 25 MHz = 2.4 GHz.
.include "msf.inc"
.equ CLK,  0x04
.equ DATA, 0x08
.equ LE,   0x20
.equ END,  0xFFFF

start:  PIN_WI   LE                 ; CLK low, LE high
        OE_SET   CLK | DATA | LE
        LDR      R1, #3             ; SR out = DATA (pin 3), MSB first
        SER_CFG  R1
        LDI      R6, regs
next:   LD       R3, R6             ; the high half (END: done)
        INCR     R6
        INCR     R3
        JZ       fin
        DECR     R3
        PIN_CLR  LE
        LDR      R0, #2             ; two halves
half:   SR_LD    R3
        LDR      R4, #16
bit:    XFER                        ; DATA <- the next bit
        PIN_SET  CLK                ; taken
        DECR     R4
        PIN_CLR  CLK
        JNZ      bit
        LD       R3, R6             ; the low half
        INCR     R6
        DECR     R0
        JNZ      half
        DECR     R6                 ; (one read too many)
        PIN_SET  LE                 ; latched
        JMP      next
fin:    UO_WI    DONE
halt:   JMP      halt

regs:   .word    0x0058, 0x0005     ; R5
        .word    0x008C, 0x803C     ; R4
        .word    0x0040, 0x04B3     ; R3
        .word    0x1800, 0x4FC2     ; R2
        .word    0x0800, 0x8011     ; R1
        .word    0x0030, 0x0000     ; R0
        .word    END
