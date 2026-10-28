; HIL Manchester loopback (test_peripherals.test_rx_manchester_loopback): T0
; sends the N words at RAM[0..] from the TX engine on uio0 (Manchester, idle
; low), T1 receives them on uio3 into RAM[64..]; 6 cycles a half-bit both
; ways. The bench wires uio0 to uio3. DONE when T1 has them all.
.include "msf.inc"
.equ N, 13

        LDR      R1, #0
        TMO      R1
        LDR      R1, #6
        TX_DIV   R1
        RX_DIV   R1
        LDI      R1, 0x27A0         ; tx_cfg(pin=0, mode=MANCH, idle=0)
        TX_CFG   R1
        OE_SET   1
        LDI      R1, 0x27A3         ; rx_cfg(pin=3, mode=MANCH)
        RX_CFG   R1
        LDI      R1, rx_thread
        TSTART   R1                 ; T1 receives while T0 transmits
        LDR      R2, #0
        LDR      R6, #N
tl:     LD       R3, R2
        TX_PUSH  R3
        INCR     R2
        DECR     R6
        JNZ      tl
halt:   JMP      halt

rx_thread:
        LDR      R2, #64
        LDR      R6, #N
rl:     RX_POP   R3
        ST       R2, R3
        INCR     R2
        DECR     R6
        JNZ      rl
        UO_WI    DONE
t1h:    JMP      t1h
