; SPI master, all four modes, full duplex through the shift register (XFER
; shifts MOSI out and MISO in in the same instruction).
;   pins   : SCK uio[2], MOSI uio[3], MISO uio[4] (input), CS uio[5] (active low)
;   input  : data RAM [0] = mode (bit0 CPHA, bit1 CPOL), [1] = byte count n,
;            [2 .. 2+n-1] = bytes to send
;   output : received bytes at data RAM [64 ..], uo_out = DONE
; One bit = 12 cycles (1 Mbit/s at 12 MHz).
.include "msf.inc"
.equ SCK, 0x04
.equ CS,  0x20

start:
        LDR      R2, #0
        LD       R5, R2             ; mode
        LDR      R2, #1
        LD       R6, R2             ; byte count
        LDR      R1, #(3 | 4 << 3)  ; SR out = MOSI (3), SR in = MISO (4), MSB-first
        SER_CFG  R1
        LDR      R1, #2
        AND      R1, R5, R1         ; CPOL
        SHL      R1, R1, #1         ; -> SCK idle level (bit 2)
        LDR      R4, #CS
        OR       R1, R1, R4
        PIN_W    R1                 ; SCK = CPOL, CS high
        OE_SET   (SCK | CS | 0x08)
        LDR      R1, #1
        AND      R5, R5, R1         ; R5 = CPHA
        LDR      R2, #2             ; tx pointer
        LDR      R3, #64            ; rx pointer
        PIN_CLR  CS
byte:
        LD       R1, R2
        SWAP     R1, R1             ; byte -> SR[15:8] (MSB-first)
        SR_LD    R1
        LDR      R4, #8
        ADDS     R7, R5, R5         ; Z=1 iff CPHA = 0
        JNZ      cpha1
cpha0:  XFER                        ; MOSI <- bit, sample MISO (valid since last trailing edge)
        NOP
        PIN_TGL  SCK                ; leading edge: slave samples MOSI
        NOP
        NOP
        NOP
        PIN_TGL  SCK                ; trailing edge: slave shifts MISO
        NOP
        DECR     R4
        JNZ      cpha0
        JMP      store
cpha1:  PIN_TGL  SCK                ; leading edge: slave shifts MISO
        NOP
        NOP
        NOP
        XFER                        ; MOSI <- bit, sample MISO
        NOP
        PIN_TGL  SCK                ; trailing edge: slave samples MOSI
        NOP
        DECR     R4
        JNZ      cpha1
store:
        SR_RD    R1                 ; received byte in SR[7:0] (SR[15:8] = 0)
        ST       R3, R1
        INCR     R2
        INCR     R3
        DECR     R6
        JNZ      byte
        LDR      R1, #8
d:      DECR     R1
        JNZ      d
        PIN_SET  CS
        UO_WI    DONE
halt:   JMP      halt
