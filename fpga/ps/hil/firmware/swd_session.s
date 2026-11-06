; HIL SWD session: firmware/swd.s's probe without the UART, for a real
; target on PMOD JA. The board wrapper gives uio_in either to the PS or to
; PMOD JA, so with a target attached no host can reach the UART. Instead the
; PS leaves a list of transactions in data RAM and this runs them in order:
; a line reset and the JTAG-to-SWD switch, then each transaction, retrying a
; WAIT up to MAXW times. It stops at the first other ACK that is not OK.
;   pins : SWCLK uio[2], SWDIO uio[3] (the target's pull-up holds it high)
;   HALF : SWCLK delay count, as in swd.s (about 1 MHz at 12 MHz)
;   data RAM, 16-bit words:
;     0      N, the number of transactions (1-31)
;     1      out: the index of the transaction that stopped the run, or N
;     2      scratch: the end of the list
;     4+4i   transaction i: h (as in swd.s: APnDP | RnW << 1 | A2 << 2 |
;            A3 << 3), data[15:0], data[31:16], result. A write sends the
;            data; a read leaves the data read there. result = ACK |
;            parity bit as read << 4 | WAIT retries << 8
; Shows DONE on uo_out at the end. The bit level and the edges are swd.s's
; (send, recv, clk_cycle, idle, parity4), for up to 16 bits a call.
.include "msf.inc"
.equ HALF,  1
.equ MAXW,  32
.equ CLK,   0x04
.equ DIO,   0x08

start:  LDR      R1, #(CLK | DIO)
        PIN_W    R1                ; SWCLK high, SWDIO high
        OE_SET   (CLK | DIO)
        LDR      R1, #(3 | 3 << 3 | 1 << 6)
        SER_CFG  R1                ; SR out and in both SWDIO, LSB first
        LDR      R1, #200          ; ~400 cycles: the PS hands the pins to
settle: DECR     R1                ; PMOD JA before the first SWCLK edge
        JNZ      settle

; ---- line reset, JTAG-to-SWD, line reset, idle
        LDR      R3, #7
lr1:    LDR      R1, #0xFF
        LDR      R4, #8
        CALL     send
        DECR     R3
        JNZ      lr1
        LDR      R1, #0x9E
        LDH      R1, #0xE7         ; 0xE79E, LSB first
        LDR      R4, #16
        CALL     send
        LDR      R3, #7
lr2:    LDR      R1, #0xFF
        LDR      R4, #8
        CALL     send
        DECR     R3
        JNZ      lr2
        CALL     idle

; ---- the transactions; R3 points at the current one, R0 counts its WAITs
        LDR      R2, #0
        LD       R1, R2            ; N
        SHL      R1, R1, #2
        LDR      R2, #4
        ADDS     R1, R1, R2
        LDR      R2, #2
        ST       R2, R1            ; end = 4 + 4N
        LDR      R3, #4
next:   LDR      R2, #2
        LD       R1, R2
        SUBS     R1, R3, R1
        JZ       finish
        LDR      R0, #0
again:  LD       R1, R3            ; h
        LDR      R2, #0x0F
        AND      R1, R1, R2
        CALL     parity4           ; R2 = parity of h
        SHL      R1, R1, #1
        SHL      R2, R2, #5
        OR       R1, R1, R2
        LDR      R2, #0x81         ; start bit, park bit
        OR       R1, R1, R2
        LDR      R4, #8
        CALL     send              ; the request
        OE_CLR   DIO               ; turnaround: the target takes the line
        CALL     clk_cycle
        LDR      R4, #3
        CALL     recv              ; R1 = ACK
        SWAP     R2, R0            ; WAITs << 8
        OR       R2, R2, R1
        MOV      R4, R3
        INCR     R4
        INCR     R4
        INCR     R4
        ST       R4, R2            ; result
        LDR      R2, #1
        SUBS     R2, R1, R2
        JZ       ok
        CALL     clk_cycle         ; no data phase: turnaround, then idle
        OE_SET   DIO
        CALL     idle
        MOV      R4, R3
        INCR     R4
        INCR     R4
        INCR     R4
        LD       R1, R4
        LDR      R2, #7
        AND      R1, R1, R2        ; the ACK again
        LDR      R2, #2
        SUBS     R2, R1, R2
        JNZ      stop              ; FAULT or no answer
        LDR      R2, #MAXW
        SUBS     R2, R0, R2
        JZ       stop              ; WAIT, MAXW times already
        INCR     R0
        JMP      again

ok:     LD       R1, R3
        LDR      R2, #2
        AND      R2, R1, R2
        ADDS     R2, R2, R2        ; Z: a write
        JZ       write
        LDR      R4, #16           ; read: 32 bits and their parity
        CALL     recv
        MOV      R2, R3
        INCR     R2
        ST       R2, R1            ; data[15:0]
        LDR      R4, #16
        CALL     recv
        MOV      R2, R3
        INCR     R2
        INCR     R2
        ST       R2, R1            ; data[31:16]
        LDR      R4, #1
        CALL     recv              ; the parity bit as read
        SHL      R1, R1, #4
        INCR     R2
        LD       R5, R2
        OR       R5, R5, R1
        ST       R2, R5
        CALL     clk_cycle         ; turnaround back to the host
        OE_SET   DIO
        CALL     idle
        JMP      step

write:  CALL     clk_cycle         ; turnaround back to the host
        OE_SET   DIO
        MOV      R2, R3
        INCR     R2
        LD       R1, R2            ; data[15:0]
        LDR      R4, #16
        CALL     send
        INCR     R2
        LD       R1, R2            ; data[31:16]
        LDR      R4, #16
        CALL     send
        LD       R1, R2
        DECR     R2
        LD       R5, R2
        XOR      R1, R1, R5        ; fold the 32 bits to 4 for the parity
        SWAP     R5, R1
        XOR      R1, R1, R5
        SHR      R5, R1, #4
        XOR      R1, R1, R5
        CALL     parity4           ; R2 = parity
        MOV      R1, R2
        LDR      R4, #1
        CALL     send
        CALL     idle
step:   LDR      R2, #4
        ADDS     R3, R3, R2
        JMP      next

stop:   LDR      R2, #4
        SUBS     R1, R3, R2
        SHR      R1, R1, #2        ; the index of the transaction
        JMP      fin1
finish: LDR      R2, #0
        LD       R1, R2            ; N: all ran
fin1:   LDR      R2, #1
        ST       R2, R1
        UO_WI    DONE
halt:   JMP      halt

; R2 <- parity of R1[3:0]; keeps R1
parity4:
        SHR      R2, R1, #2
        XOR      R2, R2, R1
        SHR      R5, R2, #1
        XOR      R2, R2, R5
        LDR      R5, #1
        AND      R2, R2, R5
        RET

; R4 bits of R1 (1-16) out on SWDIO, LSB first; the host drives the line
send:   SR_LD    R1
s1:     XFER                       ; SWDIO <- SR[0] (while SWCLK is high)
        PIN_CLR  CLK
        LDR      R6, #HALF
d1:     DECR     R6
        JNZ      d1
        PIN_SET  CLK               ; the target samples
        LDR      R6, #HALF
d2:     DECR     R6
        JNZ      d2
        DECR     R4
        JNZ      s1
        RET

; R1 <- R4 bits (1-16) read from SWDIO, LSB first, right-aligned; keeps R2
recv:   MOV      R5, R4
r1:     PIN_CLR  CLK
        LDR      R6, #HALF
d3:     DECR     R6
        JNZ      d3
        XFER                       ; sample SWDIO (OE off: SR's bit goes nowhere)
        PIN_SET  CLK               ; the target puts out its next bit
        LDR      R6, #HALF
d4:     DECR     R6
        JNZ      d4
        DECR     R4
        JNZ      r1
        SR_RD    R1                ; the k bits are SR[15:16-k]
        LDR      R6, #16
        SUBS     R6, R6, R5
        JZ       r2
r3:     SHR      R1, R1, #1
        DECR     R6
        JNZ      r3
r2:     RET

; one SWCLK cycle, nobody's data (a turnaround)
clk_cycle:
        PIN_CLR  CLK
        LDR      R6, #HALF
d5:     DECR     R6
        JNZ      d5
        PIN_SET  CLK
        LDR      R6, #HALF
d6:     DECR     R6
        JNZ      d6
        RET

; 8 idle cycles (SWDIO low), so the target finishes the transfer
idle:   MOV      R2, R7
        LDR      R1, #0
        LDR      R4, #8
        CALL     send
        JR       R2
