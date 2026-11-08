; WS2801 LED pixel chain: eight pixels of a rainbow, then the clock held low so the
; chain latches.
;   pins  : CKI uio[2], SDI uio[3], push-pull
;   table : pixels, each as (R << 8 | G) and (B << 8), first pixel first; NPIX of them
;           (counted: a pixel's first word may be anything, 0xFFFF for yellow)
;   done  : uo_out = DONE
; Sources: Worldsemi WS2801 data sheet: 24 bits a pixel, R, G and B, MSB first, taken
; on CKI's rising edge; each chip keeps the first 24 bits and passes the rest on; CKI
; low for 500 us or more latches the chain's data to the outputs.
.include "msf.inc"
.equ CKI,   0x04
.equ SDI,   0x08
.equ NPIX,  8
.equ LATCH, 7200                    ; 600 us

start:  PIN_WI   0x00
        OE_SET   CKI | SDI
        LDR      R1, #3             ; SR out = SDI (pin 3), MSB first
        SER_CFG  R1
        LDI      R6, pixels
        LDR      R2, #NPIX
next:   LD       R3, R6             ; R and G
        INCR     R6
        SR_LD    R3
        LDR      R4, #16
        CALL     shift
        LD       R3, R6             ; B
        INCR     R6
        SR_LD    R3
        LDR      R4, #8
        CALL     shift
        DECR     R2
        JNZ      next
latch:  CNT_R    R5                 ; CKI stays low: the chain latches
        LDI      R1, LATCH
        ADDS     R5, R5, R1
        WAIT_CMP R5
        UO_WI    DONE
halt:   JMP      halt

; R4 bits from the SR, MSB first
shift:  XFER
        PIN_SET  CKI
        DECR     R4
        PIN_CLR  CKI
        JNZ      shift
        RET

; red, orange, yellow, green, cyan, blue, violet, white (dim)
pixels: .word    0xFF00, 0x0000, 0xFF80, 0x0000, 0xFFFF, 0x0000, 0x00FF, 0x0000
        .word    0x00FF, 0xFF00, 0x0000, 0xFF00, 0x8000, 0xFF00, 0x2020, 0x2000
