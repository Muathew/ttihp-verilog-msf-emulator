; CAN 2.0A node (classic CAN, 11-bit identifiers) driven over the UART: it
; sends the data frames the host asks for, with arbitration, and reports
; every valid frame on the bus, ACKing it as a receiver must.
;   pins : UART RX uio[1], TX uio[0]; CAN TXD uio[2] (to the transceiver:
;          0 dominant), RXD uio[7] (from it: the bus)
;   BIT  : UART bit time in cycles (define; 104 = 115200 at 12 MHz)
;   BT   : CAN bit time in cycles (define; 480 = 50 kbit/s at 24 MHz, 25 kbit/s
;          at 12 MHz). tools/msf_timing.py certifies every deadline from 480 up:
;          with two threads each instruction takes 2 cycles, and the longest
;          path from a sample point to the next bit start (84 instructions)
;          takes up to ~195 cycles, which must fit in BT - SP
;   SP   : sample point, cycles after the bit start (define; BT / 2)
; One bit engine runs whether the node sends or not, as in a CAN controller:
; at each bit start (a WAIT_CMP deadline) it drives TXD; at the sample point
; it reads the bus, drops stuff bits (the bit of the other level after five
; equal bits, from SOF to the end of the CRC), feeds the CRC unit (CRC-15,
; polynomial 0x4599: with the received CRC fed in too the remainder must be
; 0) and packs the bits into data RAM. A sender that reads dominant over its
; recessive in the identifier or RTR bit has lost arbitration and goes on
; as a receiver; any other mismatch while sending is a bit error. After the
; CRC: the delimiter, the ACK slot (a receiver with a good CRC drives it
; dominant; the sender reads it), the ACK delimiter and the EOF.
; Timing: hard synchronisation on the falling edge of SOF, no resync (fine
; for a frame at crystal accuracy). Not implemented: error frames and error
; counters, answers to remote frames, extended (29-bit) identifiers.
; Commands: 'T' idh idl dlc d0 .. d(n-1): send a data frame (identifier
;   idh[2:0]:idl, n = min(dlc, 8)) once the bus has been idle for 11 bits.
;   Lost arbitration: again after the other frame; no ACK or an error:
;   again, 8 tries in all. Replies 'K' (sent and ACKed) or 'N' (gave up).
; Reports: 'R' idh idl dlc d0 .. d(n-1) for each frame received with a good
;   CRC; 'E' 'C' for a CRC error.
.include "msf.inc"
.equ BIT,   104
.equ BT,    480
.equ SP,    BT >> 1
.equ TXP,   0
.equ RXP,   1
.equ CTX,   0x04                   ; uio[2]
.equ CRX,   7                      ; uio[7]: PIN_R >> 7 is the bus level
.equ TXB,   0                      ; data RAM: the frame to send, 16 bits a word, MSB first
.equ RXB,   8                      ; data RAM: the frame received, the same way
.equ FLG,   16                     ; 1 = a frame is waiting to be sent
.equ TRY,   17                     ; tries left
.equ TXING, 18                     ; 1 while this node sends (until it loses arbitration)
.equ ACKD,  19                     ; the sender saw its frame ACKed
.equ CRCOK, 20                     ; the receiver's CRC was good
.equ NBN,   21                     ; data bytes: how many (building, reporting)
.equ NBI,   22                     ; data bytes: which one next (reporting)
.equ TMP,   24

start:  LDR      R1, #0
        TMO      R1                ; thread 1 inherits "no timeout"
        LDR      R1, #(CTX | 1 << TXP)
        PIN_W    R1                ; TXD recessive, UART idle high
        OE_SET   (CTX | 1 << TXP)
        LDI      R1, BIT
        RX_DIV   R1
        TX_DIV   R1
        LDI      R1, (RX_EN | RX_CHAR | RX_LSB | (8 - 1) << 8 | RXP)
        RX_CFG   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | TXP)
        TX_CFG   R1
        LDI      R1, 96 | 4 << FIFO_N
        FIFO_CFG R1                ; UART commands: SRAM 96..127
        LDR      R1, #0
        CRC_CFG  R1                ; 1 bit per CRC_W, MSB first
        CRC_POLL R1
        LDI      R1, 0x4599 << 1
        CRC_POLH R1                ; CRC-15 left-aligned: 0x4599 << 17
        LDR      R2, #FLG
        LDR      R1, #0
        ST       R2, R1
        LDI      R1, uartq
        TSTART   R1

; ---- between frames: a command to take, a frame to send or to receive
main:   LDR      R2, #FLG
        LD       R1, R2
        ADDS     R1, R1, R1
        JNZ      pending
        STAT     R1
        LDR      R2, #ST_FAVAIL
        AND      R1, R1, R2
        ADDS     R1, R1, R1
        JZ       listen
        CALL     command           ; build the frame; FLG = 1
        JMP      main
pending:
        LDI      R1, 11 * BT
        TMO      R1
        WAIT_EDGE CRX, fall        ; 11 recessive bits: the bus is idle
        JZ       send              ; (Z = 1: timed out)
        JMP      rx_sof
listen: LDI      R1, 4 * BT
        TMO      R1
        WAIT_EDGE CRX, fall
        JZ       main              ; nothing: look at the queue again
rx_sof: CNT_R    R5                ; a SOF began a few cycles ago
        LDR      R1, #4
        SUBS     R5, R5, R1
        LDR      R2, #TXING
        LDR      R1, #0
        ST       R2, R1
        LDI      R3, 0xFFFF        ; (not sending: every bit recessive)
        CALL     finit
        LDI      R7, 0x8005        ; last = recessive, 5 equal bits allowed
        LDR      R2, #1            ; (TXD is already recessive)
        JMP      f_drv             ; the SOF's sample point next
send:   LDR      R2, #TXING
        LDR      R1, #1
        ST       R2, R1
        LDR      R2, #TXB
        LD       R3, R2            ; the first 16 bits; bit 15 is SOF
        CNT_R    R5
        LDI      R1, 64
        ADDS     R5, R5, R1        ; SOF starts in 64 cycles
        CALL     finit
        LDI      R7, 0x8005        ; last = recessive, 5 equal bits allowed
        LDR      R2, #0            ; SOF: dominant
        SHL      R3, R3, #1
        JMP      fbit

; frame state: CRC 0, no bits, length unknown
finit:  LDR      R1, #0
        CRC_SETL R1
        CRC_SETH R1
        LDR      R0, #0
        LDR      R4, #0
        LDI      R6, 0x7FFF        ; not known until the DLC
        RET

; ---- the frame. R0 bits received (shifted in), R1 the bus level, R2 the
; bit to drive next, R3 bits to send (MSB first), R4 bits received so far
; without stuff bits, R5 the next deadline (bit start, then sample point), R6 bits left to the end of the CRC,
; R7 = equal bits still allowed before a stuff bit, + 0x8000 if the last
; level was recessive (so ADDS R7, R7 gives Z exactly when a stuff bit is due)
fbit:   WAIT_CMP R5                ; bit start: drive
        INCR     R2
        DECR     R2
        JZ       f_dom
        PIN_SET  CTX
        JMP      f_drv
f_dom:  PIN_CLR  CTX
f_drv:  LDI      R1, SP
        ADDS     R5, R5, R1
        WAIT_CMP R5                ; sample point: read the bus
        PIN_R    R1
        SHR      R1, R1, #CRX      ; the bus level (uio[7])
        SUBS     R2, R2, R1        ; driven - read
        JNZ      f_mis
f_ok:   ADDS     R2, R7, R7        ; a stuff bit?
        JNZ      f_data
        INCR     R1                ; a stuff bit must differ from the last
        DECR     R1
        JZ       f_s0
        INCR     R7
        DECR     R7
        JN       f_err             ; recessive after recessive
        LDI      R7, 0x8004        ; a new run: recessive
        JMP      f_next
f_s0:   INCR     R7
        DECR     R7
        JP       f_err             ; dominant after dominant
        LDR      R7, #4            ; a new run: dominant
        JMP      f_next
f_data: INCR     R1                ; the same level as the last?
        DECR     R1
        JZ       f_d0
        INCR     R7
        DECR     R7
        JN       f_eq
        LDI      R7, 0x8004        ; recessive after dominant: a new run
        JMP      f_acc
f_d0:   INCR     R7
        DECR     R7
        JP       f_eq
        LDR      R7, #4            ; dominant after recessive: a new run
        JMP      f_acc
f_eq:   DECR     R7                ; one more equal bit
f_acc:  SHL      R0, R0, #1
        OR       R0, R0, R1
        CRC_W    R1
        INCR     R4
        DECR     R6
        LDR      R1, #15           ; 16 more bits: store them
        AND      R2, R4, R1
        INCR     R2
        DECR     R2
        JNZ      f_dlc
        SHR      R2, R4, #4
        LDR      R1, #(RXB - 1)
        ADDS     R2, R2, R1
        ST       R2, R0
        LDR      R1, #TXING        ; sending: the next 16 bits
        LD       R1, R1
        ADDS     R1, R1, R1
        JZ       f_rf1
        SHR      R2, R4, #4
        LD       R3, R2            ; (TXB = 0)
        JMP      f_dlc
f_rf1:  LDI      R3, 0xFFFF        ; not sending: recessive
f_dlc:  LDR      R2, #19           ; SOF..DLC in: the length is known
        SUBS     R2, R4, R2
        JNZ      f_next
        LDR      R1, #0x0F
        AND      R6, R0, R1        ; DLC
        LDR      R1, #8
        SUBS     R2, R6, R1
        JN       f_n
        MOV      R6, R1            ; at most 8 bytes
f_n:    SHL      R6, R6, #3
        LDR      R1, #15
        ADDS     R6, R6, R1        ; data bits + 15 CRC bits to come
f_next: LDI      R1, BT - SP
        ADDS     R5, R5, R1        ; the next bit start
        ADDS     R2, R7, R7        ; a stuff bit next?
        JZ       f_stuff
        INCR     R6                ; the CRC done?
        DECR     R6
        JZ       tail
        INCR     R3                ; the next bit to send: R3's MSB
        DECR     R3
        LDR      R2, #1
        JN       f_tb
        LDR      R2, #0
f_tb:   SHL      R3, R3, #1
        JMP      fbit
f_stuff:
        LDR      R2, #TXING        ; a sender drives the stuff bit
        LD       R2, R2
        ADDS     R2, R2, R2
        LDR      R2, #1
        JZ       fbit
        INCR     R7                ; the other level than the last
        DECR     R7
        LDR      R2, #0
        JN       fbit
        LDR      R2, #1
        JMP      fbit

; driven and read differ
f_mis:  LDR      R2, #TXING
        LD       R2, R2
        ADDS     R2, R2, R2
        JZ       f_ok              ; not sending: another node's dominant bit
        INCR     R1
        DECR     R1
        JNZ      f_err             ; read recessive over our dominant: bit error
        ADDS     R2, R7, R7        ; on a stuff bit: error
        JZ       f_err
        LDR      R2, #13           ; arbitration: identifier and RTR, bits 1-12
        SUBS     R2, R4, R2
        JP       f_err
        INCR     R4
        DECR     R4
        JZ       f_err
        LDR      R2, #TXING        ; lost arbitration: go on receiving
        ST       R2, R1            ; (R1 = 0)
        LDI      R3, 0xFFFF
        JMP      f_ok

; an error: let go of the bus; a sender counts a try; wait for idle
f_err:  PIN_SET  CTX
        LDR      R2, #TXING
        LD       R1, R2
        ADDS     R1, R1, R1
        JZ       idlewait
        CALL     tried
idlewait:
        LDI      R1, 11 * BT       ; the rest of the frame: wait for 11 idle bits
        TMO      R1
iw:     WAIT_EDGE CRX, fall
        JNZ      iw
        JMP      main

; ---- after the CRC: delimiter, ACK slot, ACK delimiter, EOF
tail:   WAIT_CMP R5                ; CRC delimiter: recessive
        PIN_SET  CTX
        CRC_RDH  R1                ; (bits 15:1 = the remainder)
        LDR      R2, #CRCOK
        INCR     R1
        DECR     R1
        LDR      R1, #1
        JZ       t_crc
        LDR      R1, #0
t_crc:  ST       R2, R1
        LDR      R2, #1
        XOR      R1, R1, R2        ; 1 = the CRC was bad
        LDR      R2, #TXING
        LD       R2, R2
        OR       R2, R2, R1        ; ACK slot: recessive if sending or the CRC was bad
        LDI      R1, BT
        ADDS     R5, R5, R1
        WAIT_CMP R5                ; ACK slot
        INCR     R2
        DECR     R2
        JZ       t_dom
        PIN_SET  CTX
        JMP      t_drv
t_dom:  PIN_CLR  CTX
t_drv:  LDI      R1, SP
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_R    R1
        SHR      R1, R1, #CRX
        LDR      R2, #1
        XOR      R1, R1, R2        ; 1 = dominant: ACKed
        LDR      R2, #ACKD
        ST       R2, R1
        LDI      R1, BT - SP
        ADDS     R5, R5, R1
        WAIT_CMP R5                ; ACK delimiter and EOF: recessive
        PIN_SET  CTX

; ---- the frame is over (the bus is in its 9 recessive bits): report it
        LDR      R1, #15           ; a partial last word: left-align and store
        AND      R2, R4, R1
        INCR     R2
        DECR     R2
        JZ       fd_tx
        LDR      R1, #16
        SUBS     R2, R1, R2        ; shift by 16 - (bits & 15)
fd_al:  SHL      R0, R0, #1
        DECR     R2
        JNZ      fd_al
        SHR      R2, R4, #4
        LDR      R1, #RXB
        ADDS     R2, R2, R1
        ST       R2, R0
fd_tx:  LDR      R2, #TXING        ; our own frame?
        LD       R1, R2
        ADDS     R1, R1, R1
        JZ       fd_rx
        LDR      R2, #ACKD
        LD       R1, R2
        ADDS     R1, R1, R1
        JZ       fd_noack
        LDR      R1, #'K'
        CALL     tx_byte
        LDR      R2, #FLG
        LDR      R1, #0
        ST       R2, R1
        JMP      main
fd_noack:
        CALL     tried
        JMP      main
fd_rx:  LDR      R2, #CRCOK
        LD       R1, R2
        ADDS     R1, R1, R1
        JNZ      fd_good
        LDR      R1, #'E'
        CALL     tx_byte
        LDR      R1, #'C'
        CALL     tx_byte
        JMP      main
fd_good:
        LDR      R1, #'R'
        CALL     tx_byte
        LDR      R1, #1            ; identifier: bits 1-11
        LDR      R2, #11
        CALL     getbits
        LDR      R2, #TMP
        ST       R2, R3
        SWAP     R1, R3            ; its high byte (tx_byte sends the low byte)
        CALL     tx_byte
        LDR      R2, #TMP
        LD       R1, R2
        CALL     tx_byte           ; its low byte
        LDR      R1, #15           ; DLC: bits 15-18
        LDR      R2, #4
        CALL     getbits
        LDR      R2, #NBN          ; n = min(DLC, 8)
        ST       R2, R3
        MOV      R1, R3
        CALL     tx_byte
        LDR      R2, #NBN
        LD       R1, R2
        LDR      R3, #8
        SUBS     R4, R1, R3
        JN       fd_n
        ST       R2, R3
fd_n:   LDR      R2, #NBI
        LDR      R1, #0
        ST       R2, R1
fd_byte:
        LDR      R2, #NBI
        LD       R4, R2            ; index
        LDR      R2, #NBN
        LD       R3, R2            ; n
        SUBS     R3, R4, R3
        JNZ      fd_more
        JMP      main              ; all bytes reported
fd_more:
        LDR      R2, #NBI
        MOV      R1, R4
        INCR     R1
        ST       R2, R1
        SHL      R1, R4, #3
        LDR      R2, #19
        ADDS     R1, R1, R2        ; data byte i: bits 19 + 8i
        LDR      R2, #8
        CALL     getbits
        MOV      R1, R3
        CALL     tx_byte
        JMP      fd_byte

; one try used: give up after the last ('N')
tried:  LDR      R2, #TRY
        LD       R1, R2
        DECR     R1
        ST       R2, R1
        JNZ      tr_ret
        LDR      R2, #FLG
        ST       R2, R1            ; (R1 = 0)
        MOV      R6, R7            ; (tx_byte keeps R6)
        LDR      R1, #'N'
        CALL     tx_byte
        MOV      R7, R6
tr_ret: RET

; R3 <- R2 bits (1-16) of the received frame from bit R1 on, MSB first
getbits:
        LDR      R3, #0
gb:     SHR      R4, R1, #4
        LDR      R5, #RXB
        ADDS     R4, R4, R5
        LD       R5, R4            ; the word holding bit R1
        LDR      R4, #15
        AND      R6, R1, R4
gbs:    INCR     R6
        DECR     R6
        JZ       gbt
        SHL      R5, R5, #1
        DECR     R6
        JMP      gbs
gbt:    SHL      R3, R3, #1
        INCR     R5                ; the bit: R5's MSB
        DECR     R5
        JP       gb0
        LDR      R4, #1
        OR       R3, R3, R4
gb0:    INCR     R1
        DECR     R2
        JNZ      gb
        RET

; ---- a 'T' command from the queue -> the frame's bits and CRC in TXB
command:
        MOV      R6, R7            ; (fget and putbits keep R6)
        CALL     fget
        LDR      R2, #'T'
        SUBS     R2, R1, R2
        JNZ      cm_ret            ; anything else is ignored
        LDR      R1, #0
        CRC_SETL R1
        CRC_SETH R1
        LDR      R0, #0
        LDR      R4, #0
        LDR      R1, #0            ; SOF
        LDR      R2, #1
        CALL     putbits
        CALL     fget              ; identifier: high 3 bits
        LDR      R2, #0x07
        AND      R1, R1, R2
        SWAP     R5, R1            ; << 8
        LDR      R2, #TMP
        ST       R2, R5
        CALL     fget              ; ... low 8 bits
        LDR      R2, #TMP
        LD       R5, R2
        OR       R1, R1, R5
        LDR      R2, #11
        CALL     putbits
        LDR      R1, #0            ; RTR, IDE, r0
        LDR      R2, #3
        CALL     putbits
        CALL     fget              ; DLC
        LDR      R2, #0x0F
        AND      R1, R1, R2
        LDR      R2, #NBN
        ST       R2, R1
        LDR      R2, #4
        CALL     putbits
        LDR      R2, #NBN          ; n = min(DLC, 8)
        LD       R1, R2
        LDR      R3, #8
        SUBS     R5, R1, R3
        JN       cm_b
        ST       R2, R3
cm_b:   LDR      R2, #NBN
        LD       R1, R2
        INCR     R1
        DECR     R1
        JZ       cm_crc
        DECR     R1
        ST       R2, R1
        CALL     fget              ; a data byte
        LDR      R2, #8
        CALL     putbits
        JMP      cm_b
cm_crc: CRC_RDH  R1
        SHR      R1, R1, #1        ; the 15-bit CRC
        LDR      R2, #15
        CALL     putbits
        LDR      R1, #15           ; a partial last word: left-align and store
        AND      R2, R4, R1
        INCR     R2
        DECR     R2
        JZ       cm_flg
        LDR      R1, #16
        SUBS     R2, R1, R2
cm_al:  SHL      R0, R0, #1
        DECR     R2
        JNZ      cm_al
        SHR      R2, R4, #4        ; (TXB = 0)
        ST       R2, R0
cm_flg: LDR      R2, #FLG
        LDR      R1, #1
        ST       R2, R1
        LDR      R2, #TRY
        LDR      R1, #8
        ST       R2, R1
cm_ret: MOV      R7, R6
        RET

; R1 <- the next queued UART byte (waits for it)
fget:   FIFO_GET R1
        JZ       fget
        LDR      R2, #0xFF
        AND      R1, R1, R2
        RET

; R2 bits (1-16) of R1, MSB first, into the frame being built: R0 the bits
; (stored every 16 to TXB), R4 the count; each goes into the CRC
putbits:
        LDR      R3, #16
        SUBS     R3, R3, R2
pb_al:  INCR     R3                ; left-align the R2 bits
        DECR     R3
        JZ       pb
        SHL      R1, R1, #1
        DECR     R3
        JMP      pb_al
pb:     INCR     R1                ; the bit: R1's MSB
        DECR     R1
        LDR      R3, #1
        JN       pb_1
        LDR      R3, #0
pb_1:   CRC_W    R3
        SHL      R0, R0, #1
        OR       R0, R0, R3
        SHL      R1, R1, #1
        INCR     R4
        LDR      R3, #15           ; 16 bits: store them
        AND      R3, R4, R3
        INCR     R3
        DECR     R3
        JNZ      pb_n
        SHR      R3, R4, #4
        DECR     R3                ; (TXB = 0)
        ST       R3, R0
pb_n:   DECR     R2
        JNZ      pb
        RET

; R1 = byte -> one 8N1 frame on the UART (the low byte); uses R0
tx_byte:
        LDI      R0, 0x200
        ADDS     R1, R1, R1
        OR       R1, R1, R0
txp:    TX_PUSH  R1
        JZ       txp
        RET

; ---- thread 1: UART bytes into the command queue
uartq:  RX_POP   R1
        FIFO_PUT R1
        JMP      uartq
