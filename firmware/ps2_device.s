; PS/2 keyboard (device side): the chip stands in for a keyboard. Bytes from
; the UART are sent to the PS/2 host as scancodes; the host's commands are
; answered as a keyboard answers them, and every byte the host sends is also
; reported on the UART, so the probe shows what a host asks of a keyboard.
;   pins : UART RX uio[1], TX uio[0]; PS/2 CLK uio[2], DATA uio[3] (open
;          drain with pull-ups: the chip only pulls them low)
;   BIT  : UART bit time in cycles (define; 104 = 115200 at 12 MHz)
;   Q    : a quarter of the PS/2 clock period in cycles (define; 240 = 20 us
;          at 12 MHz: 12.5 kHz, inside the 10-16.7 kHz the standard allows)
; The device makes the clock in both directions, on WAIT_CMP deadlines: low
; 2Q, high 2Q. Device to host: start 0, 8 data bits LSB first, odd parity,
; stop 1, each set on DATA while CLK is high, Q before CLK falls. Host to
; device: the host pulls DATA low with CLK released (request to send); the
; device clocks in 8 data bits, parity and stop, sampling Q after each
; rising edge, and answers with an ACK (DATA low through an 11th clock).
; The host comes first: while it holds CLK low (inhibit) nothing is sent,
; and a frame it interrupts before the 11th clock is sent again whole
; afterwards. Bytes to send (replies, and UART scancodes, which thread 1
; queues as they arrive) wait in the FIFO.
; Commands: FF reset -> FA AA; EE echo -> EE; F2 read ID -> FA AB 83;
; ED set LEDs and F3 set typematic -> FA, then their argument -> FA; any
; other byte -> FA; a byte with bad parity -> FE (resend).
.include "msf.inc"
.equ BIT,  104
.equ Q,    240
.equ RXP,  1
.equ TXP,  0
.equ CLK,  0x04
.equ DAT,  0x08
.equ ARG,  0                       ; data RAM: 1 = the next host byte is an argument
.equ CUR,  1                       ; data RAM: the byte being sent, 0x100 = none
.equ NONE, 0x100

start:  LDR      R1, #0
        TMO      R1                ; no timeouts
        LDR      R1, #(1 << TXP)
        PIN_W    R1                ; CLK, DATA outputs 0 (OE pulls low); UART high
        OE_SET   (1 << TXP)        ; CLK and DATA released
        LDI      R1, BIT
        RX_DIV   R1
        TX_DIV   R1
        LDI      R1, (RX_EN | RX_CHAR | RX_LSB | (8 - 1) << 8 | RXP)
        RX_CFG   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | TXP)
        TX_CFG   R1
        LDI      R1, 64 | 4 << FIFO_N
        FIFO_CFG R1                ; the send queue: SRAM 64..95
        LDR      R2, #ARG
        LDR      R1, #0
        ST       R2, R1
        LDR      R2, #CUR
        LDI      R1, NONE
        ST       R2, R1
        LDI      R6, Q
        LDI      R1, rxq
        TSTART   R1                ; thread 1: UART bytes -> the send queue

; R0-R4 scratch, R5 deadline, R6 = Q, R7 return address
idle:   PIN_R    R1
        LDR      R2, #CLK
        AND      R2, R1, R2
        ADDS     R2, R2, R2
        JZ       inhibit           ; the host holds CLK low
        LDR      R2, #DAT
        AND      R2, R1, R2
        ADDS     R2, R2, R2
        JZ       receive           ; CLK high, DATA low: request to send
        LDR      R2, #CUR          ; a byte being sent?
        LD       R1, R2
        LDI      R3, NONE
        SUBS     R3, R1, R3
        JZ       next
        CALL     send
        JZ       idle              ; interrupted: the host first, then again
        LDR      R2, #CUR
        LDI      R1, NONE
        ST       R2, R1
        JMP      idle
next:   STAT     R1
        LDR      R2, #ST_FAVAIL
        AND      R2, R1, R2
        ADDS     R2, R2, R2
        JZ       uart
        FIFO_GET R1                ; the next byte to send
        LDR      R2, #CUR
        ST       R2, R1
uart:   JMP      idle

; ---- thread 1: scancodes from the UART into the send queue
rxq:    RX_POP   R1
        LDR      R2, #0xFF
        AND      R1, R1, R2
        FIFO_PUT R1
        JMP      rxq

inhibit:
        WAIT_EDGE 2, rise          ; until the host lets CLK go
        JMP      idle

; ---- host to device: clock in 8 data bits, parity, stop; then ACK
receive:
        CNT_R    R5
        ADDS     R5, R5, R6
        WAIT_CMP R5                ; Q after seeing the request
        LDR      R4, #10
        LDR      R3, #0
rbit:   OE_SET   CLK               ; CLK low: the host sets the bit
        ADDS     R5, R5, R6
        ADDS     R5, R5, R6
        WAIT_CMP R5
        OE_CLR   CLK               ; rising edge: the bit is valid
        ADDS     R5, R5, R6
        WAIT_CMP R5
        PIN_R    R1
        SHR      R3, R3, #1        ; LSB first: the new bit goes in at bit 9
        LDR      R2, #DAT
        AND      R1, R1, R2
        ADDS     R1, R1, R1
        JZ       r0
        LDI      R1, 0x200
        OR       R3, R3, R1
r0:     ADDS     R5, R5, R6
        WAIT_CMP R5
        DECR     R4
        JNZ      rbit
        OE_SET   DAT               ; ACK: DATA low through an 11th clock
        ADDS     R5, R5, R6
        WAIT_CMP R5
        OE_SET   CLK
        ADDS     R5, R5, R6
        ADDS     R5, R5, R6
        WAIT_CMP R5
        OE_CLR   CLK
        ADDS     R5, R5, R6
        WAIT_CMP R5
        OE_CLR   DAT
        LDR      R2, #0xFF
        AND      R1, R3, R2        ; the byte
        SHR      R4, R3, #7
        SHR      R4, R4, #1        ; its parity bit (bit 8), in bit 0
        CALL     oddpar            ; R2 <- the odd parity of R1
        XOR      R2, R2, R4
        LDR      R4, #1
        AND      R2, R2, R4
        ADDS     R2, R2, R2
        JZ       good
        LDR      R1, #0xFE         ; parity error: ask for it again
        FIFO_PUT R1
        JMP      idle
good:   MOV      R3, R1
        CALL     tx_byte           ; report the host's byte on the UART
        MOV      R1, R3
        LDR      R2, #ARG          ; the argument of ED / F3?
        LD       R4, R2
        ADDS     R4, R4, R4
        JZ       command
        LDR      R4, #0
        ST       R2, R4
        JMP      ack
command:
        LDI      R2, 0xFF
        SUBS     R2, R1, R2
        JZ       c_reset
        LDI      R2, 0xEE
        SUBS     R2, R1, R2
        JZ       c_echo
        LDI      R2, 0xF2
        SUBS     R2, R1, R2
        JZ       c_id
        LDI      R2, 0xED
        SUBS     R2, R1, R2
        JZ       c_arg
        LDI      R2, 0xF3
        SUBS     R2, R1, R2
        JZ       c_arg
ack:    LDR      R1, #0xFA
        FIFO_PUT R1
        JMP      idle
c_arg:  LDR      R2, #ARG
        LDR      R4, #1
        ST       R2, R4
        JMP      ack
c_reset:
        LDR      R1, #0xFA
        FIFO_PUT R1
        LDR      R1, #0xAA         ; self-test passed
        FIFO_PUT R1
        JMP      idle
c_echo: LDR      R1, #0xEE
        FIFO_PUT R1
        JMP      idle
c_id:   LDR      R1, #0xFA
        FIFO_PUT R1
        LDR      R1, #0xAB
        FIFO_PUT R1
        LDR      R1, #0x83
        FIFO_PUT R1
        JMP      idle

; ---- device to host: one frame of R1; Z = 1 if the host pulled CLK low
send:   MOV      R4, R7
        CALL     oddpar            ; R2 <- odd parity of R1
        MOV      R7, R4
        SHL      R2, R2, #7
        SHL      R2, R2, #2        ; parity in bit 9
        ADDS     R3, R1, R1        ; data in bits 8:1, start bit 0
        OR       R3, R3, R2
        LDI      R2, 0x400
        OR       R3, R3, R2        ; stop bit 10
        LDR      R2, #11           ; bits left
        LDR      R0, #CLK
        CNT_R    R5
sbit:   LDR      R1, #1
        AND      R1, R3, R1
        ADDS     R1, R1, R1
        JZ       s0
        OE_CLR   DAT               ; 1: release DATA
        JMP      s1
s0:     OE_SET   DAT               ; 0: pull DATA low
s1:     ADDS     R5, R5, R6
        WAIT_CMP R5                ; DATA set about Q before CLK falls
        OE_SET   CLK               ; falling edge: the host samples
        ADDS     R5, R5, R6
        ADDS     R5, R5, R6
        WAIT_CMP R5
        OE_CLR   CLK               ; rising edge
        ADDS     R5, R5, R6
        WAIT_CMP R5
        SHR      R3, R3, #1
        DECR     R2
        JZ       sdone             ; the 11th clock is done: sent
        PIN_R    R1                ; the host may hold CLK low: inhibit
        AND      R1, R1, R0
        ADDS     R1, R1, R1
        JZ       sabort            ; before the 11th clock: send it all again
        JMP      sbit
sdone:  LDR      R1, #1
        ADDS     R1, R1, R1        ; Z = 0: sent
        RET
sabort: OE_CLR   DAT
        LDR      R1, #0
        ADDS     R1, R1, R1        ; Z = 1: interrupted
        RET

; R2 <- odd parity bit of R1[7:0] (1 if R1 has an even number of ones); uses R0
oddpar: SHR      R2, R1, #4
        XOR      R2, R2, R1
        SHR      R0, R2, #2
        XOR      R2, R2, R0
        SHR      R0, R2, #1
        XOR      R2, R2, R0
        LDR      R0, #1
        AND      R2, R2, R0
        XOR      R2, R2, R0        ; odd parity: invert the even parity
        RET

; R1 = byte -> one 8N1 frame on the UART; uses R0
tx_byte:
        LDI      R0, 0x200         ; stop bit
        ADDS     R1, R1, R1
        OR       R1, R1, R0        ; start bit 0, data, stop bit
txp:    TX_PUSH  R1
        JZ       txp
        RET
