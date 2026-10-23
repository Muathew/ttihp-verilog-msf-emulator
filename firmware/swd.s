; SWD probe (ARM ADIv5 Serial Wire Debug host) driven over the UART: line
; reset and the JTAG-to-SWD switch, then single transactions on the DP and
; APs, so a host can read DPIDR, select an AP and read or write a target's
; memory through a MEM-AP.
;   pins : UART RX uio[1], TX uio[0]; SWCLK uio[2], SWDIO uio[3] (both
;          ways; the target's pull-up holds it high when nobody drives)
;   BIT  : UART bit time in cycles (define; 104 = 115200 at 12 MHz)
;   HALF : SWCLK delay count (define, >= 1): low for 2 + 3 HALF cycles and
;          high for at least 1 + 3 HALF (about 1 MHz at 12 MHz)
; SWCLK idles high. The host sets SWDIO while SWCLK is high and the target
; samples it on the rising edge; the target changes SWDIO on rising edges
; and the host samples it while SWCLK is low (as in CMSIS-DAP).
; Commands, one byte then arguments:
;   'L'              line reset (56 ones), the JTAG-to-SWD sequence 0xE79E,
;                    line reset, 8 idle cycles; replies 'L'
;   'T' h [d0..d3]   one transaction. h = APnDP | RnW << 1 | A2 << 2 |
;                    A3 << 3; the request (start, h, parity, stop, park) is
;                    built here. A write (RnW = 0) takes the 32-bit value
;                    LSB first and sends it with its parity. Replies the ACK
;                    (1 OK, 2 WAIT, 4 FAULT, 7 no answer); after an OK read
;                    also the 4 data bytes and the parity bit as read. On any
;                    other ACK there is no data phase (a turnaround, then idle)
; Other bytes are ignored.
.include "msf.inc"
.equ BIT,   104
.equ HALF,  1
.equ RXP,   1
.equ TXP,   0
.equ CLK,   0x04
.equ DIO,   0x08
.equ DATA,  0                      ; data RAM: the 4 data bytes, then parity
.equ HDR,   8                      ; data RAM: h of the transaction

start:  LDR      R1, #0
        TMO      R1                ; no timeouts: the probe waits for the host
        LDR      R1, #(CLK | DIO | 1 << TXP)
        PIN_W    R1                ; SWCLK high, SWDIO high, UART idle high
        OE_SET   (CLK | DIO | 1 << TXP)
        LDI      R1, BIT
        RX_DIV   R1
        TX_DIV   R1
        LDI      R1, (RX_EN | RX_CHAR | RX_LSB | (8 - 1) << 8 | RXP)
        RX_CFG   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | TXP)
        TX_CFG   R1
        LDR      R1, #(3 | 3 << 3 | 1 << 6)
        SER_CFG  R1                ; SR out and in both SWDIO, LSB first
        LDI      R0, 0x200         ; UART stop bit

; R0 stop bit, R1/R2 scratch, R3 ACK, R4 bit count, R5 kept count,
; R6 delays, R7 return address
cmd:    RX_POP   R1
        LDR      R2, #0xFF
        AND      R1, R1, R2
        LDR      R2, #'L'
        SUBS     R2, R1, R2
        JZ       linereset
        LDR      R2, #'T'
        SUBS     R2, R1, R2
        JZ       trans
        JMP      cmd

; ---- line reset, JTAG-to-SWD, line reset, idle
linereset:
        LDR      R3, #7
lr1:    LDR      R1, #0xFF
        LDR      R4, #8
        CALL     send
        DECR     R3
        JNZ      lr1
        LDR      R1, #0x9E         ; 0xE79E, LSB first
        LDR      R4, #8
        CALL     send
        LDR      R1, #0xE7
        LDR      R4, #8
        CALL     send
        LDR      R3, #7
lr2:    LDR      R1, #0xFF
        LDR      R4, #8
        CALL     send
        DECR     R3
        JNZ      lr2
        LDR      R1, #0
        LDR      R4, #8
        CALL     send              ; idle
        LDR      R5, #'L'
        CALL     tx_byte
        JMP      cmd

; ---- one transaction
trans:  RX_POP   R1
        LDR      R2, #0x0F
        AND      R1, R1, R2        ; h
        LDR      R2, #HDR
        ST       R2, R1
        LDR      R2, #2
        AND      R2, R1, R2
        ADDS     R2, R2, R2        ; Z: a write, the data follows
        JNZ      req
        LDR      R3, #DATA
        LDR      R4, #4
wd:     RX_POP   R1
        LDR      R2, #0xFF
        AND      R1, R1, R2
        ST       R3, R1
        INCR     R3
        DECR     R4
        JNZ      wd
req:    LDR      R2, #HDR
        LD       R1, R2            ; h
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
        CALL     recv
        MOV      R3, R1            ; ACK
        LDR      R2, #1
        SUBS     R2, R3, R2
        JNZ      noack             ; WAIT, FAULT or no answer: no data phase
        LDR      R2, #HDR
        LD       R1, R2
        LDR      R2, #2
        AND      R2, R1, R2
        ADDS     R2, R2, R2
        JZ       write

        LDR      R3, #DATA         ; read: 32 bits and parity from the target
        LDR      R5, #4
rd:     LDR      R4, #8
        MOV      R2, R5
        CALL     recv
        MOV      R5, R2
        ST       R3, R1
        INCR     R3
        DECR     R5
        JNZ      rd
        LDR      R4, #1
        CALL     recv
        ST       R3, R1            ; parity as read
        CALL     clk_cycle         ; turnaround back to the host
        OE_SET   DIO
        CALL     idle
        LDR      R5, #1
        CALL     tx_byte           ; ACK OK
        LDR      R3, #DATA
        LDR      R4, #5
rr:     LD       R5, R3
        MOV      R2, R4
        CALL     tx_byte
        MOV      R4, R2
        INCR     R3
        DECR     R4
        JNZ      rr
        JMP      cmd

write:  CALL     clk_cycle         ; turnaround back to the host
        OE_SET   DIO
        LDR      R3, #DATA         ; 32 bits LSB first, then their parity
        LDR      R5, #4
wr:     LD       R1, R3
        LDR      R4, #8
        MOV      R2, R5
        CALL     send
        MOV      R5, R2
        INCR     R3
        DECR     R5
        JNZ      wr
        LDR      R3, #DATA         ; parity of the four bytes
        LD       R1, R3
        INCR     R3
        LD       R2, R3
        XOR      R1, R1, R2
        INCR     R3
        LD       R2, R3
        XOR      R1, R1, R2
        INCR     R3
        LD       R2, R3
        XOR      R1, R1, R2
        SHR      R2, R1, #4
        XOR      R1, R1, R2        ; fold 8 bits to 4
        CALL     parity4           ; R2 = parity
        MOV      R1, R2
        LDR      R4, #1
        CALL     send
        CALL     idle
        LDR      R5, #1
        CALL     tx_byte           ; ACK OK
        JMP      cmd

noack:  CALL     clk_cycle         ; turnaround; the target lets go
        OE_SET   DIO
        CALL     idle
        MOV      R5, R3
        CALL     tx_byte           ; the ACK as read
        JMP      cmd

; R2 <- parity of R1[3:0]; keeps R1
parity4:
        SHR      R2, R1, #2
        XOR      R2, R2, R1
        SHR      R5, R2, #1
        XOR      R2, R2, R5
        LDR      R5, #1
        AND      R2, R2, R5
        RET

; R4 bits of R1 (1-8) out on SWDIO, LSB first; the host drives the line
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

; R1 <- R4 bits (1-8) read from SWDIO, LSB first, right-aligned
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
        SWAP     R1, R1            ; ... now bits 7:8-k
        LDR      R6, #0xFF
        AND      R1, R1, R6        ; drop SR[7:0] before shifting down
        LDR      R6, #8
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

; R5 = byte -> one 8N1 frame on the UART
tx_byte:
        ADDS     R5, R5, R5
        OR       R5, R5, R0        ; start bit 0, data, stop bit
txp:    TX_PUSH  R5
        JZ       txp
        RET
