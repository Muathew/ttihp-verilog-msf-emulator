; JTAG probe (IEEE 1149.1 master) driven over the UART: the host resets the
; TAP and runs IR and DR scans of 1-255 bits; the bits shifted out of TDO
; come back on the UART. The scans start and end in Run-Test/Idle, so a host
; can read IDCODEs, count the devices on a chain, and drive boundary scan.
;   pins : UART RX uio[1], TX uio[0]; TCK uio[2], TDI uio[3], TDO uio[4]
;          (input), TMS uio[5]
;   BIT  : UART bit time in cycles (define; 104 = 115200 at 12 MHz)
;   HALF : TCK delay count (define, >= 1): TCK is high for 1 + 3 HALF
;          cycles and low for at least 10 + 3 HALF (defaults: 4 and 13, about
;          700 kHz at 12 MHz); TDO is sampled at least 8 cycles after TCK falls
; Commands, one byte then arguments:
;   'R'              TAP reset (TMS high for 5 TCKs), then Run-Test/Idle;
;                    replies 'R'
;   'I' n d0 d1 ...  IR scan of n bits (1-255): the data, LSB first, in
;                    ceil(n/8) bytes; TMS goes high with the last bit. Replies
;                    the n bits read from TDO, LSB first, in ceil(n/8) bytes
;                    (each byte as soon as its bits are in)
;   'D' n d0 d1 ...  DR scan, the same
; Other bytes, and n = 0, are ignored.
.include "msf.inc"
.equ BIT,  104
.equ HALF, 1
.equ RXP,  1
.equ TXP,  0
.equ TCK,  0x04
.equ TDI,  0x08
.equ TMS,  0x20

start:  LDR      R1, #0
        TMO      R1                ; no timeouts: the probe waits for the host
        PIN_W    R1                ; TCK, TDI, TMS low
        PIN_SET  (1 << TXP)        ; UART idles high
        OE_SET   (TCK | TDI | TMS | 1 << TXP)
        LDI      R1, BIT
        RX_DIV   R1
        TX_DIV   R1
        LDI      R1, (RX_EN | RX_CHAR | RX_LSB | (8 - 1) << 8 | RXP)
        RX_CFG   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | TXP)
        TX_CFG   R1
        LDR      R1, #(3 | 4 << 3 | 1 << 6)
        SER_CFG  R1                ; SR out = TDI, SR in = TDO, LSB first
        LDI      R0, 0x200         ; UART stop bit

; R0 stop bit, R1/R2 scratch, R3 bits left in the scan, R4 bits left in the
; byte, R5 bits in the byte / byte out, R6 delays, R7 return address
cmd:    RX_POP   R1
        LDR      R2, #0xFF
        AND      R1, R1, R2
        LDR      R2, #'R'
        SUBS     R2, R1, R2
        JZ       reset
        LDR      R2, #'I'
        SUBS     R2, R1, R2
        JZ       ir
        LDR      R2, #'D'
        SUBS     R2, R1, R2
        JZ       dr
        JMP      cmd

reset:  LDR      R3, #5
rs:     LDR      R1, #1
        CALL     tms_clk           ; Test-Logic-Reset after five
        DECR     R3
        JNZ      rs
        LDR      R1, #0
        CALL     tms_clk           ; Run-Test/Idle
        LDR      R5, #'R'
        CALL     tx_byte
        JMP      cmd

ir:     CALL     nbits
        LDR      R1, #1
        CALL     tms_clk           ; Select-DR-Scan
        LDR      R1, #1
        CALL     tms_clk           ; Select-IR-Scan
        JMP      scan
dr:     CALL     nbits
        LDR      R1, #1
        CALL     tms_clk           ; Select-DR-Scan
scan:   LDR      R1, #0
        CALL     tms_clk           ; Capture
        LDR      R1, #0
        CALL     tms_clk           ; Shift

byte:   RX_POP   R1                ; the next 8 data bits
        LDR      R2, #0xFF
        AND      R1, R1, R2
        SR_LD    R1                ; LSB first: SR[0] goes out first
        LDR      R4, #8
        LDR      R2, #8
        SUBS     R2, R3, R2
        JP       full
        MOV      R4, R3            ; the last, partial byte
full:   MOV      R5, R4
bit:    LDR      R2, #1
        SUBS     R2, R3, R2
        JNZ      shift
        PIN_SET  TMS               ; the last bit: leave Shift with it
shift:  XFER                       ; TDI <- SR[0]; SR <- {TDO, SR[15:1]}
        LDR      R6, #HALF
d1:     DECR     R6
        JNZ      d1
        PIN_SET  TCK               ; the device samples TDI and TMS
        LDR      R6, #HALF
d2:     DECR     R6
        JNZ      d2
        PIN_CLR  TCK               ; the device puts its next bit on TDO
        DECR     R3
        DECR     R4
        JNZ      bit
        SR_RD    R1                ; the k bits read are SR[15:16-k]
        SWAP     R1, R1            ; ... now bits 7:8-k
        LDR      R2, #8
        SUBS     R2, R2, R5
        JZ       aligned
al:     SHR      R1, R1, #1
        DECR     R2
        JNZ      al
aligned:
        LDR      R2, #0xFF
        AND      R5, R1, R2
        CALL     tx_byte
        ADDS     R2, R3, R3
        JNZ      byte
        LDR      R1, #1
        CALL     tms_clk           ; Exit1 -> Update
        LDR      R1, #0
        CALL     tms_clk           ; Run-Test/Idle
        JMP      cmd

; R3 <- the bit count from the host; n = 0 drops the command
nbits:  RX_POP   R3
        LDR      R2, #0xFF
        AND      R3, R3, R2
        ADDS     R2, R3, R3        ; Z if n = 0 (AND sets no flags)
        JZ       drop
        RET
drop:   JMP      cmd

; one TCK with TMS = R1 (0 or 1); TDI is left as it is
tms_clk:
        ADDS     R1, R1, R1
        JZ       tms0
        PIN_SET  TMS
        JMP      tck
tms0:   PIN_CLR  TMS
tck:    LDR      R6, #HALF
d3:     DECR     R6
        JNZ      d3
        PIN_SET  TCK
        LDR      R6, #HALF
d4:     DECR     R6
        JNZ      d4
        PIN_CLR  TCK
        RET

; R5 = byte -> one 8N1 frame on the UART
tx_byte:
        ADDS     R5, R5, R5
        OR       R5, R5, R0        ; start bit 0, data, stop bit
txp:    TX_PUSH  R5
        JZ       txp
        RET
