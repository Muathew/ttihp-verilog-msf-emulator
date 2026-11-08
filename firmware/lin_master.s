; LIN master node: one frame the master publishes (header and response), then one
; header a slave answers, read back through the transceiver and checksum-checked.
;   pins  : TXD uio[0] to the LIN transceiver (push-pull, high = recessive),
;           RXD uio[1] from it (the bus level)
;   table : frames (a schedule, run twice), per frame: the protected identifier, then n | MASTER and n data
;           bytes (the master publishes), or n alone (a slave publishes n bytes)
;   output: each slave response (its data bytes, then its checksum) at data RAM
;           [64 ..]; uo_out = DONE if every slave
;           response came, complete and with the right checksum, else BAD
; Sources: LIN Specification Package 2.2A (LIN Consortium, 2010) §2.3.1 (frame
; structure: a break field of at least 13 dominant bits and a break delimiter of at
; least one; the sync byte 0x55; the protected identifier, P0 = ID0^ID1^ID2^ID4,
; P1 = ~(ID1^ID3^ID4^ID5); data bytes and the checksum, each a start bit, 8 bits LSB
; first and a stop bit), §2.3.1.5 (the enhanced checksum: the inverted eight-bit sum
; with carry of the protected identifier and the data), §2.3.2 (19.2 kbit/s is a
; standard rate).
.include "msf.inc"
.equ TX,     0x01
.equ BIT,    625                    ; 19,200 bit/s at 12 MHz
.equ BRK,    13 * BIT
.equ GAP,    24000                  ; 2 ms between frames
.equ RX_TMO, 20 * BIT               ; a slave byte must start within 10 bits of the last
.equ RXCFG,  1 | RX_LSB | (7 << 8) | RX_CHAR | RX_EN    ; RXD, 8 bits, async
.equ MASTER, 0x100
.equ END,    0xFFFF
.equ BAD,    0xEE
.equ N_SAVE, 7
.equ RESULT, 6
.equ FRAME,  8                      ; the frame's bytes, built before it is sent

start:  PIN_WI   TX                 ; recessive
        OE_SET   TX
        LDI      R1, BIT
        RX_DIV   R1
        LDI      R6, frames
        LDR      R0, #64
        LDR      R1, #DONE
        LDR      R2, #RESULT        ; the result so far
        ST       R2, R1
fr:     LD       R4, R6             ; the protected identifier (END: the end)
        INCR     R6
        INCR     R4
        JZ       fin
        DECR     R4
        LD       R2, R6             ; n | MASTER
        INCR     R6
        LDR      R1, #N_SAVE
        ST       R1, R2
        ; the frame's bytes: 0x55, PID, then (master) data and the checksum
        LDR      R3, #FRAME
        LDR      R1, #0x55
        ST       R3, R1
        INCR     R3
        ST       R3, R4
        INCR     R3
        MOV      R7, R4             ; R7: the sum, from the PID (enhanced)
        LDI      R1, MASTER
        AND      R1, R2, R1
        ADDS     R1, R1, R1
        JZ       send               ; a slave publishes: the header only
        LDR      R1, #0xFF
        AND      R2, R2, R1
        LDI      R4, 256
bl:     LD       R1, R6
        INCR     R6
        ST       R3, R1
        INCR     R3
        ADDS     R7, R7, R1
        SUBS     R1, R7, R4         ; a carry: add it back in
        JN       bl_ok
        INCR     R1
        MOV      R7, R1
bl_ok:  DECR     R2
        JNZ      bl
        LDR      R1, #0xFF
        XOR      R7, R7, R1
        ST       R3, R7
        INCR     R3
send:   LDR      R1, #FRAME
        SUBS     R2, R3, R1         ; R2: how many bytes
        LDR      R4, #FRAME
        CNT_R    R5
        LDI      R1, GAP
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  TX                 ; the break field
        LDI      R1, BRK
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_SET  TX                 ; the break delimiter
        LDI      R1, BIT
        ADDS     R5, R5, R1
byte:   LD       R3, R4
        INCR     R4
        ADDS     R3, R3, R3         ; the start bit
        LDI      R1, 0x200          ; the stop bit
        OR       R3, R3, R1
        LDR      R7, #10
bit:    LDR      R1, #1
        AND      R1, R3, R1
        WAIT_CMP R5
        PIN_W    R1
        SHR      R3, R3, #1
        LDI      R1, BIT
        ADDS     R5, R5, R1
        DECR     R7
        JNZ      bit
        DECR     R2
        JNZ      byte
        ; the stop bit is on the line: a slave frame's response follows
        LDR      R1, #N_SAVE
        LD       R2, R1
        LDI      R1, MASTER
        AND      R1, R2, R1
        ADDS     R1, R1, R1
        JNZ      fr
        LDI      R1, RXCFG
        RX_CFG   R1                 ; the line is high (a stop bit): no false start
        LDI      R1, RX_TMO
        TMO      R1
        LDR      R1, #FRAME + 1
        LD       R7, R1             ; R7: the sum, from the PID
        INCR     R2                 ; n data bytes and the checksum
        LDI      R4, 256
rb:     RX_POP   R3
        JZ       fail               ; timed out
        ST       R0, R3
        INCR     R0
        ADDS     R7, R7, R3
        SUBS     R1, R7, R4
        JN       rb_ok
        INCR     R1
        MOV      R7, R1
rb_ok:  DECR     R2
        JNZ      rb
        LDR      R1, #0xFF          ; with the checksum added in, the sum is 0xFF
        SUBS     R1, R7, R1
        JZ       rx_end
fail:   LDR      R1, #BAD
        LDR      R2, #RESULT
        ST       R2, R1
rx_end: LDR      R1, #0
        RX_CFG   R1
        LDI      R1, 0xFFFF
        TMO      R1
        JMP      fr
fin:    LDR      R1, #RESULT
        LD       R1, R1
        LDR      R2, #DONE
        SUBS     R1, R1, R2
        JNZ      f_bad
        UO_WI    DONE
        JMP      halt
f_bad:  UO_WI    BAD
halt:   JMP      halt

.equ ID_A, 0x10
.equ ID_B, 0x21
.equ PID_A, ID_A | ((ID_A ^ ID_A >> 1 ^ ID_A >> 2 ^ ID_A >> 4) & 1) << 6 | (~(ID_A >> 1 ^ ID_A >> 3 ^ ID_A >> 4 ^ ID_A >> 5) & 1) << 7
.equ PID_B, ID_B | ((ID_B ^ ID_B >> 1 ^ ID_B >> 2 ^ ID_B >> 4) & 1) << 6 | (~(ID_B >> 1 ^ ID_B >> 3 ^ ID_B >> 4 ^ ID_B >> 5) & 1) << 7
; the schedule, twice round
frames: .word    PID_A, MASTER | 4, 0x01, 0x02, 0xA5, 0xFF
        .word    PID_B, 2
        .word    PID_A, MASTER | 4, 0x01, 0x02, 0xA5, 0xFF
        .word    PID_B, 2
        .word    END
