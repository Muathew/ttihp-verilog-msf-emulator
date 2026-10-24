; 1-Wire master (Maxim/Dallas) driven over the UART: reset and presence,
; byte writes and reads, and the SEARCH ROM algorithm that finds every
; device on the bus, so a host can enumerate and talk to 1-Wire parts.
;   pins : UART RX uio[1], TX uio[0]; 1-Wire DQ uio[3] (open drain with a
;          pull-up: the chip only pulls it low)
;   BIT  : UART bit time in cycles (define; 104 = 115200 at 12 MHz)
;   US   : cycles per microsecond (define; 12 at 12 MHz)
; Slots, on WAIT_CMP deadlines from the slot's falling edge (standard speed):
;   reset    low 480 us, then sample presence at 70 us, slot ends at 480 us
;   write 1  low 6 us, slot 70 us; write 0: low 60 us, slot 70 us
;   read     low 6 us, sample at 15 us, slot 70 us
; Commands, one byte then arguments:
;   'R'          reset; replies 'P' if a device answered with a presence
;                pulse, else 'N'
;   'W' n b..    write n bytes (LSB first); replies 'W'
;   'r' n        read n bytes; replies them
;   'S'          SEARCH ROM: replies 'D' and the 8 ROM bytes of each device
;                found (LSB first, family code first), then 'E' (or 'N' if
;                nothing answered the reset)
; Other bytes are ignored.
.include "msf.inc"
.equ BIT,  104
.equ US,   12
.equ RXP,  1
.equ TXP,  0
.equ DQ,   0x08
.equ DQP,  3
.equ ROM,  0                       ; data RAM: the ROM being searched, 64 bits, 1 per word
.equ LAST, 64                      ; data RAM: the last discrepancy (0: none)
.equ DONE_, 65                     ; data RAM: the last device was found
.equ NEXTZ,66                      ; data RAM: the zero-discrepancy of this pass
.equ CNT_, 67                      ; data RAM: a loop count

start:  LDR      R1, #0
        TMO      R1                ; no timeouts
        LDR      R1, #(1 << TXP)
        PIN_W    R1                ; DQ output 0 (OE pulls low), UART high
        OE_SET   (1 << TXP)        ; DQ released
        LDI      R1, BIT
        RX_DIV   R1
        TX_DIV   R1
        LDI      R1, (RX_EN | RX_CHAR | RX_LSB | (8 - 1) << 8 | RXP)
        RX_CFG   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | TXP)
        TX_CFG   R1

cmd:    RX_POP   R1
        LDR      R2, #0xFF
        AND      R1, R1, R2
        LDR      R2, #'R'
        SUBS     R2, R1, R2
        JZ       c_reset
        LDR      R2, #'W'
        SUBS     R2, R1, R2
        JZ       c_write
        LDR      R2, #'r'
        SUBS     R2, R1, R2
        JZ       c_read
        LDR      R2, #'S'
        SUBS     R2, R1, R2
        JZ       c_search
        JMP      cmd

c_reset:
        CALL     reset
        LDR      R1, #'P'
        JNZ      cr_tx             ; (Z = 0: presence)
        LDR      R1, #'N'
cr_tx:  CALL     tx_byte
        JMP      cmd

c_write:
        RX_POP   R4                ; n
        LDR      R2, #0xFF
        AND      R4, R4, R2
cw:     INCR     R4
        DECR     R4
        JZ       cw_done
        RX_POP   R1
        CALL     wbyte
        DECR     R4
        JMP      cw
cw_done:
        LDR      R1, #'W'
        CALL     tx_byte
        JMP      cmd

c_read: RX_POP   R4
        LDR      R2, #0xFF
        AND      R4, R4, R2
crd:    INCR     R4
        DECR     R4
        JZ       cmd
        CALL     rbyte
        CALL     tx_byte
        DECR     R4
        JMP      crd

; ---- SEARCH ROM (Maxim application note 187): one pass per device
c_search:
        LDR      R2, #LAST
        LDR      R1, #0
        ST       R2, R1
        LDR      R2, #DONE_
        ST       R2, R1
cs_pass:
        LDR      R2, #DONE_
        LD       R1, R2
        ADDS     R1, R1, R1
        JNZ      cs_end
        CALL     reset
        JZ       cs_none           ; nothing on the bus
        LDI      R1, 0xF0
        CALL     wbyte             ; SEARCH ROM
        LDR      R2, #NEXTZ
        LDR      R1, #0
        ST       R2, R1
        LDR      R4, #1            ; bit number 1-64
cs_bit: CALL     rbit              ; the bit
        MOV      R3, R1
        CALL     rbit              ; its complement
        AND      R2, R3, R1        ; both 1: no device answers
        ADDS     R2, R2, R2
        JNZ      cs_none
        SUBS     R2, R3, R1        ; differ: all devices agree on this bit
        JZ       cs_disc
        MOV      R1, R3            ; the bit
        JMP      cs_set
cs_disc:                           ; both 0: a discrepancy
        LDR      R2, #LAST
        LD       R3, R2
        SUBS     R2, R4, R3        ; bit vs the last discrepancy
        JZ       cs_one            ; at it: take 1 this time
        JN       cs_prev           ; before it: as the ROM found last time
        LDR      R1, #0            ; after it: take 0
        JMP      cs_mark
cs_prev:
        MOV      R2, R4
        DECR     R2                ; (ROM + bit - 1)
        LD       R1, R2
        INCR     R1
        DECR     R1
        JNZ      cs_set            ; was 1: no new branch here
cs_mark:
        LDR      R2, #NEXTZ        ; 0 taken at a discrepancy: remember it
        ST       R2, R4
        JMP      cs_set
cs_one: LDR      R1, #1
cs_set: MOV      R2, R4            ; ROM[bit - 1] = the bit; write it to select
        DECR     R2
        ST       R2, R1
        CALL     wbit
        INCR     R4
        LDR      R2, #65
        SUBS     R2, R4, R2
        JNZ      cs_bit
        LDR      R2, #NEXTZ        ; the pass is done: one device found
        LD       R1, R2
        LDR      R2, #LAST
        ST       R2, R1
        ADDS     R1, R1, R1
        JNZ      cs_rep
        LDR      R2, #DONE_
        LDR      R1, #1
        ST       R2, R1
cs_rep: LDR      R1, #'D'
        CALL     tx_byte
        LDR      R5, #0            ; 8 bytes from the 64 ROM bits, LSB first
cs_byte:
        LDR      R3, #0
        LDR      R4, #8
cs_bb:  SHR      R3, R3, #1
        LD       R1, R5
        INCR     R1
        DECR     R1
        JZ       cs_b0
        LDR      R1, #0x80
        OR       R3, R3, R1
cs_b0:  INCR     R5
        DECR     R4
        JNZ      cs_bb
        MOV      R1, R3
        CALL     tx_byte
        LDR      R2, #64
        SUBS     R2, R5, R2
        JNZ      cs_byte
        JMP      cs_pass
cs_none:
        LDR      R1, #'N'          ; nothing answered
        CALL     tx_byte
        JMP      cmd
cs_end: LDR      R1, #'E'
        CALL     tx_byte
        JMP      cmd

; ---- slots. R5 is the deadline; each keeps R4 and R6
; reset: Z = 0 if a presence pulse was seen
reset:  OE_SET   DQ                ; low 480 us
        CNT_R    R5
        LDI      R2, 480 * US
        ADDS     R5, R5, R2
        WAIT_CMP R5
        OE_CLR   DQ
        LDI      R2, 70 * US
        ADDS     R5, R5, R2
        WAIT_CMP R5                ; sample the presence pulse
        PIN_R    R1
        LDI      R2, 410 * US
        ADDS     R5, R5, R2
        WAIT_CMP R5                ; the rest of the 480 us
        LDR      R2, #DQ
        AND      R1, R1, R2
        LDR      R2, #DQ
        XOR      R1, R1, R2        ; DQ low (presence) -> nonzero
        ADDS     R1, R1, R1
        RET

; write R1's bit 0
wbit:   OE_SET   DQ
        CNT_R    R5
        INCR     R1
        DECR     R1
        LDI      R2, 6 * US        ; a 1: release after 6 us
        JNZ      wb_low
        LDI      R2, 60 * US       ; a 0: after 60 us
wb_low: ADDS     R5, R5, R2
        WAIT_CMP R5
        OE_CLR   DQ
        LDI      R1, 70 * US       ; the slot ends 70 us after the fall
        SUBS     R2, R1, R2
        ADDS     R5, R5, R2
        WAIT_CMP R5
        RET

; R1 <- one bit read
rbit:   OE_SET   DQ
        CNT_R    R5
        LDI      R2, 6 * US
        ADDS     R5, R5, R2
        WAIT_CMP R5
        OE_CLR   DQ                ; release: the device holds a 0
        LDI      R2, 9 * US
        ADDS     R5, R5, R2
        WAIT_CMP R5                ; 15 us into the slot
        PIN_R    R1
        LDI      R2, 55 * US
        ADDS     R5, R5, R2
        WAIT_CMP R5                ; slot end, 70 us
        SHR      R1, R1, #DQP
        LDR      R2, #1
        AND      R1, R1, R2
        RET

; write the byte in R1, LSB first (keeps R4)
wbyte:  MOV      R6, R7
        MOV      R3, R1
        LDR      R2, #8
        LDR      R0, #0
wy:     LDR      R1, #1
        AND      R1, R3, R1
        SHR      R3, R3, #1
        MOV      R0, R2
        CALL     wbit
        MOV      R2, R0
        DECR     R2
        JNZ      wy
        MOV      R7, R6
        RET

; R1 <- a byte read, LSB first (keeps R4)
rbyte:  MOV      R6, R7
        LDR      R3, #0
        LDR      R0, #8
ry:     CALL     rbit
        SHR      R3, R3, #1
        INCR     R1
        DECR     R1
        JZ       ry0
        LDR      R1, #0x80
        OR       R3, R3, R1
ry0:    DECR     R0
        JNZ      ry
        MOV      R1, R3
        MOV      R7, R6
        RET

; R1 = byte -> one 8N1 frame on the UART (the low byte); uses R0
tx_byte:
        MOV      R2, R0
        LDI      R0, 0x200
        ADDS     R1, R1, R1
        OR       R1, R1, R0
txp:    TX_PUSH  R1
        JZ       txp
        MOV      R0, R2
        RET
