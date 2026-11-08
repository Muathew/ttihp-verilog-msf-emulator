; MDIO station management (IEEE 802.3 clause 22): read a PHY's identifier registers
; and write its control register, the PHY answering on the same wire.
;   pins  : MDC uio[2] (push-pull), MDIO uio[3] (driven only while the station
;           sends; released for the turnaround and the PHY's data, pulled up)
;   table : ops, per operation READ | phy << 5 | reg, or WRITE | phy << 5 | reg and
;           the 16-bit value; 0xFFFF = the end
;   output: what each read returned, in data RAM [64 ..]
;   done  : uo_out = DONE
; Sources: IEEE 802.3-2018 clause 22.2.4.5 (management frame: 32 ones of preamble,
; start 01, op 10 read / 01 write, 5-bit PHY address, 5-bit register address,
; turnaround (Z0 on a read: the station releases MDIO and the PHY drives a 0; 10 on a
; write), 16 data bits, MSB first) and 22.3.4 (MDC period at least 400 ns, high and
; low at least 160 ns; MDIO from the PHY valid 0-300 ns after MDC rises, sampled by
; the station on MDC's rising edge). Here a bit is 12 cycles (1 MHz).
.include "msf.inc"
.equ MDC,   0x04
.equ MDIO,  0x08
.equ READ,  0x8000
.equ WRITE, 0x4000
.equ END,   0xFFFF

start:  PIN_CLR  MDC | MDIO         ; MDC low; MDIO's level comes from the SR
        OE_SET   MDC
        LDR      R1, #(3 | 3 << 3)  ; SR out and in = MDIO (pin 3), MSB first
        SER_CFG  R1
        LDI      R6, ops
        LDR      R0, #64
op:     LD       R3, R6
        INCR     R6
        INCR     R3
        JZ       fin
        DECR     R3
        ; the station drives: 32 ones, then 01, op, PHY, register (14 bits); MDIO
        ; high before it is driven, as the SR refills from the pin
        PIN_SET  MDIO
        OE_SET   MDIO
        LDR      R4, #32
        LDI      R1, 0xFFFF
        SR_LD    R1
pre:    XFER                        ; MDIO <- 1 (the SR refills with MDIO's 1s)
        NOP
        PIN_SET  MDC                ; the PHY takes it
        NOP
        NOP
        NOP
        PIN_CLR  MDC
        DECR     R4
        JNZ      pre
        LDR      R1, #0
        SUBS     R1, R3, R1         ; N: a read
        LDI      R2, 0x6000         ; 01 10 in bits 15-12: read
        JN       hdr
        LDI      R2, 0x5000         ; 01 01: write
hdr:    LDI      R1, 0x03FF         ; PHY and register
        AND      R1, R3, R1
        SHL      R1, R1, #2         ; to bits 11-2
        OR       R2, R2, R1
        LDR      R1, #0
        SUBS     R1, R3, R1
        JN       hdr_r
        LDR      R1, #0x02          ; a write's turnaround: 10, in bits 1-0
        OR       R2, R2, R1
        SR_LD    R2
        LDR      R4, #16            ; start, op, addresses, turnaround
        CALL     sendn
        LD       R2, R6             ; the value
        INCR     R6
        SR_LD    R2
        LDR      R4, #16
        CALL     sendn
        OE_CLR   MDIO
        JMP      op
hdr_r:  SR_LD    R2
        LDR      R4, #14            ; start, op, addresses
        CALL     sendn
        OE_CLR   MDIO               ; the turnaround: released
        PIN_SET  MDC                ; Z; the PHY drives its 0 after this edge
        NOP
        NOP
        NOP
        PIN_CLR  MDC
        NOP
        NOP
        NOP
        PIN_SET  MDC                ; the 0; bit 15 follows
        NOP
        NOP
        NOP
        PIN_CLR  MDC
        NOP
        LDR      R4, #16            ; 16 bits from the PHY: sample, then clock
rd:     XFER                        ; MDIO -> SR (valid since the last rising edge)
        PIN_SET  MDC                ; the PHY moves on to the next bit
        NOP
        NOP
        NOP
        PIN_CLR  MDC
        DECR     R4
        JNZ      rd
        SR_RD    R1
        ST       R0, R1
        INCR     R0
        JMP      op
fin:    UO_WI    DONE
halt:   JMP      halt

; R4 bits from the SR, MSB first, one clock each
sendn:  XFER
        NOP
        PIN_SET  MDC
        NOP
        NOP
        NOP
        PIN_CLR  MDC
        DECR     R4
        JNZ      sendn
        RET

; PHY 1: read the identifier (registers 2 and 3), then write the control register
; (register 0): restart auto-negotiation, auto-negotiation enabled
ops:    .word    READ | 1 << 5 | 2
        .word    READ | 1 << 5 | 3
        .word    WRITE | 1 << 5 | 0, 0x1200
        .word    END
