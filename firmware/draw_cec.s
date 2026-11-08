; HDMI-CEC device at logical address 4 (playback 1). Drawn at random (docs/draw/).
;   1. 40 04 to the TV (Image View On); the TV acknowledges it
;   2. 4F 82 10 00 broadcast (Active Source)
;   3. 48 04 to device 8, which is absent: no acknowledgement, retransmitted, given up
;   4. listen: the TV sends 04 83 (Give Physical Address); acknowledge both bytes
;   5. 4F 84 10 00 04 broadcast (Report Physical Address, 1.0.0.0, playback)
; Report: 'A' or 'N' for 1, 2, 3, the two bytes received in 4, 'A' or 'N' for 5
; ('L' instead of 'A'/'N' if a 1 we sent read as 0: lost arbitration).
;   pin    : the bus uio[0], open drain (pulled low only; the bus's pull-up)
;   report : 8N1 on uo_out[0] at 104 cycles a bit, then uo_out[1] high
; Sources: the Linux kernel's software CEC (drivers/media/cec/core/cec-pin.c,
; include/media/cec.h, cec-adap.c), which states the HDMI CEC numbers: start bit
; low 3.7 ms (3.5-3.9), whole 4.5 ms (4.3-4.7); a 0 low 1.5 ms (1.3-1.7), a 1 low
; 0.6 ms (0.4-0.8), a bit 2.4 ms (2.05-2.75); signal-free time 3 bit periods
; before a retransmission, 5 for a new initiator, 7 for the same initiator's next
; frame; the follower's acknowledgement holds the line until 1.5 ms; a directly
; addressed byte is acknowledged by a 0, a broadcast one refused by a 0; "4 attempts
; is the default" (suggested by CEC 2.0, as cec-adap.c says); most implementations
; stop sending at the first unacknowledged byte. Bits are sampled at 1.05 ms, the
; nominal time (cec-pin.c's earliest safe time is 0.85 ms). Blocks: 8 data bits
; MSB first, EOM, ACK.
.include "msf.inc"
.equ LINE, 0x01
.equ T_SLOW,  44400                ; 3.7 ms at 12 MHz
.equ T_STOT,  54000                ; 4.5 ms
.equ T_0LOW,  18000                ; 1.5 ms
.equ T_1LOW,  7200                 ; 0.6 ms
.equ T_BIT,   28800                ; 2.4 ms
.equ T_SAMPLE, 12600               ; 1.05 ms
.equ T_ACK,   18000                ; a follower's 0: low until 1.5 ms
.equ T_SMIN,  42000                ; a start bit's low: 3.5..3.9 ms
.equ T_SMAX,  46800
.equ T_SNEXT, 51600                ; and the next fall 4.3..4.7 ms after it
.equ T_NEXT,  33000                ; the next bit falls within 2.75 ms
.equ MYLA,    4
.equ TRIES,   4
.equ UBIT,    104
.equ BCAST,   8                    ; RAM: this frame is a broadcast
.equ SFTN,    9                    ; quiet bit periods wanted
.equ REPP,    10                   ; where the next report byte goes
.equ NTRY,    11
.equ FORUS,   12
.equ REP,     16                   ; RAM 16-31: the report
.equ RBUF,    32                   ; RAM 32-47: a frame received

start:  UO_WI    0x01              ; the report line idles high
        PIN_CLR  LINE              ; open drain: only ever pulled low
        OE_CLR   LINE
        LDR      R1, #0
        TMO      R1                ; the only waits that time out set TMO
        LDR      R1, #REP
        LDR      R2, #REPP
        ST       R2, R1
        ; 1. Image View On
        LDR      R1, #5            ; a new initiator
        CALL     sft
        LDI      R6, f_ivo
        CALL     send
        CALL     rep
        ; 2. Active Source
        LDR      R1, #7            ; the same initiator's next frame
        CALL     sft
        LDI      R6, f_as
        CALL     send
        CALL     rep
        ; 3. to device 8, which is not there
        LDR      R1, #7
        CALL     sft
        LDR      R1, #TRIES
        LDR      R2, #NTRY
        ST       R2, R1
try3:   LDI      R6, f_absent
        CALL     send
        LDR      R1, #'A'
        SUBS     R1, R0, R1
        JZ       done3
        LDR      R2, #NTRY
        LD       R1, R2
        DECR     R1
        ST       R2, R1
        JZ       done3             ; four attempts: give up
        LDR      R1, #3            ; a retransmission
        CALL     sft
        JMP      try3
done3:  CALL     rep
        ; 4. the TV asks for our physical address
        CALL     recv
        LDR      R1, #RBUF
        LD       R0, R1
        CALL     rep
        LDR      R1, #RBUF + 1
        LD       R0, R1
        CALL     rep
        ; 5. Report Physical Address
        LDR      R1, #5            ; the TV was the last initiator
        CALL     sft
        LDI      R6, f_rpa
        CALL     send
        CALL     rep
        ; the report
        LDR      R6, #REP
rp:     LDR      R2, #REPP
        LD       R1, R2
        SUBS     R1, R6, R1
        JZ       rp_end
        LD       R1, R6
        CALL     uo_byte
        INCR     R6
        JMP      rp
rp_end: UO_WI    0x03
halt:   JMP      halt

; the frames: how many bytes, then the bytes
f_ivo:    .word 2, 0x40, 0x04
f_as:     .word 4, 0x4F, 0x82, 0x10, 0x00
f_absent: .word 2, 0x48, 0x04
f_rpa:    .word 5, 0x4F, 0x84, 0x10, 0x00, 0x04

; R0 onto the report
rep:    LDR      R2, #REPP
        LD       R1, R2
        ST       R1, R0
        INCR     R1
        ST       R2, R1
        RET

; wait until the line has been quiet (no fall) for R1 whole bit periods
sft:    LDR      R2, #SFTN
        ST       R2, R1
sf_re:  LDR      R2, #SFTN
        LD       R4, R2
sf_l:   LDI      R1, T_BIT
        TMO      R1
        WAIT_EDGE #0x20            ; a fall: someone talks, start again
        LDR      R1, #0
        TMO      R1
        JNZ      sf_re
        DECR     R4
        JNZ      sf_l
        RET

; send the frame at R6 (count, bytes). R0 <- 'A': every byte acknowledged (a
; broadcast: nobody refused it); 'N': one was not, and the frame stops there; 'L':
; a 1 we sent read as 0. Returns at the end of the last bit period.
send:   LD       R0, R6            ; R0: bytes left
        INCR     R6
        LD       R1, R6
        LDR      R2, #0x0F
        AND      R1, R1, R2
        SUBS     R1, R1, R2        ; Z: to 15, a broadcast
        LDR      R1, #0
        JNZ      sd_dir
        LDR      R1, #1
sd_dir: LDR      R2, #BCAST
        ST       R2, R1
        CNT_R    R5
        OE_SET   LINE              ; the start bit
        LDI      R1, T_SLOW
        ADDS     R2, R5, R1
        WAIT_CMP R2
        OE_CLR   LINE
        LDI      R1, T_STOT
        ADDS     R5, R5, R1        ; R5: the next bit's start
sd_byte: LD      R3, R6            ; the byte, EOM, and the ACK bit sent as a 1
        SHL      R3, R3, #7
        SHL      R3, R3, #1
        LDR      R1, #0x40
        OR       R3, R3, R1
        LDR      R1, #1
        SUBS     R1, R0, R1        ; Z: the last byte, EOM = 1
        JNZ      sd_e0
        LDR      R1, #0x80
        OR       R3, R3, R1
sd_e0:  LDR      R4, #10
sd_bit: WAIT_CMP R5
        OE_SET   LINE
        SHR      R1, R3, #7
        SHR      R1, R1, #7
        SHR      R1, R1, #1
        ADDS     R1, R1, R1
        JZ       sd_0
        LDI      R1, T_1LOW        ; a 1: low 0.6 ms, then read the line at 1.05
        ADDS     R2, R5, R1
        WAIT_CMP R2
        OE_CLR   LINE
        LDI      R1, T_SAMPLE
        ADDS     R2, R5, R1
        WAIT_CMP R2
        PIN_R    R1
        LDR      R2, #LINE
        AND      R1, R1, R2        ; 0: someone holds the line low
        LDR      R2, #1
        SUBS     R2, R4, R2        ; Z: the ACK bit
        JZ       sd_ack
        ADDS     R1, R1, R1
        JNZ      sd_next
        LDR      R0, #'L'          ; someone else sent a 0
        JMP      sd_stop
sd_0:   LDI      R1, T_0LOW
        ADDS     R2, R5, R1
        WAIT_CMP R2
        OE_CLR   LINE
        JMP      sd_next
sd_ack: LDR      R2, #BCAST
        LD       R2, R2
        ADDS     R2, R2, R2        ; Z: directly addressed
        JZ       sd_ackd
        ADDS     R1, R1, R1        ; a broadcast: a 0 refuses it
        JNZ      sd_next
        LDR      R0, #'N'
        JMP      sd_stop
sd_ackd: ADDS    R1, R1, R1        ; directly addressed: a 0 acknowledges it
        JZ       sd_next
        LDR      R0, #'N'
        JMP      sd_stop
sd_next: SHL     R3, R3, #1
        LDI      R1, T_BIT
        ADDS     R5, R5, R1
        DECR     R4
        JNZ      sd_bit
        INCR     R6
        DECR     R0
        JNZ      sd_byte
        LDR      R0, #'A'
        JMP      sd_end
sd_stop: LDI     R1, T_BIT         ; stopped in a bit: wait out its period
        ADDS     R5, R5, R1
sd_end: WAIT_CMP R5
        RET

; receive a frame into RBUF, acknowledging it if it is for us. R0 <- the bytes
; received, or 0 if the start bit or a bit is out of time.
recv:   WAIT_EDGE #0x20            ; a fall (TMO is 0: wait as long as it takes)
        CNT_R    R5
        LDI      R1, T_SMAX
        TMO      R1
        WAIT_EDGE #0x10            ; the start bit's rise...
        LDR      R1, #0
        TMO      R1
        JZ       rv_bad
        CNT_R    R1
        SUBS     R1, R1, R5
        LDI      R2, T_SMIN
        SUBS     R1, R1, R2
        JN       rv_bad            ; ...not before 3.5 ms
        LDI      R1, T_NEXT
        TMO      R1
        WAIT_EDGE #0x20            ; the first bit's fall
        LDR      R1, #0
        TMO      R1
        JZ       rv_bad
        CNT_R    R1
        SUBS     R2, R1, R5
        MOV      R5, R1            ; R5: this bit's fall
        LDI      R1, T_SNEXT
        SUBS     R2, R2, R1
        JN       rv_bad            ; 4.3 ms at least after the start bit's fall
        LDR      R6, #RBUF
        LDR      R1, #0
        LDR      R2, #FORUS
        ST       R2, R1
rv_byte: LDR     R3, #0
        LDR      R4, #9            ; 8 data bits and EOM
rv_bit: LDI      R1, T_SAMPLE
        ADDS     R2, R5, R1
        WAIT_CMP R2
        PIN_R    R1
        LDR      R2, #LINE
        AND      R1, R1, R2        ; high at 1.05 ms: a 1
        ADDS     R3, R3, R3
        OR       R3, R3, R1
        LDI      R1, T_NEXT
        TMO      R1
        WAIT_EDGE #0x20
        LDR      R1, #0
        TMO      R1
        JZ       rv_bad
        CNT_R    R5
        DECR     R4
        JNZ      rv_bit
        ; this fall starts the ACK bit. The header says whom the frame is for.
        LDR      R1, #RBUF
        SUBS     R1, R6, R1
        JNZ      rv_ack
        SHR      R1, R3, #1
        LDR      R2, #0x0F
        AND      R1, R1, R2
        LDR      R2, #MYLA
        SUBS     R1, R1, R2
        JNZ      rv_ack            ; not ours (a broadcast: accepted by saying nothing)
        LDR      R1, #1
        LDR      R2, #FORUS
        ST       R2, R1
rv_ack: LDR      R2, #FORUS
        LD       R1, R2
        ADDS     R1, R1, R1
        JZ       rv_keep
        OE_SET   LINE              ; acknowledge: a 0, low until 1.5 ms
        LDI      R1, T_ACK
        ADDS     R2, R5, R1
        WAIT_CMP R2
        OE_CLR   LINE
rv_keep: SHR     R1, R3, #1
        ST       R6, R1
        INCR     R6
        LDR      R1, #1
        AND      R1, R3, R1
        LDR      R2, #0
        SUBS     R1, R1, R2        ; (flags from the EOM bit)
        JNZ      rv_end            ; EOM: the frame is over
        LDI      R1, T_NEXT
        TMO      R1
        WAIT_EDGE #0x20            ; the next byte's first bit
        LDR      R1, #0
        TMO      R1
        JZ       rv_bad
        CNT_R    R5
        JMP      rv_byte
rv_end: LDR      R1, #RBUF
        SUBS     R0, R6, R1
        LDI      R1, T_BIT
        ADDS     R5, R5, R1
        WAIT_CMP R5                ; the end of the ACK bit's period
        RET
rv_bad: LDR      R0, #0
        LDR      R1, #0
        TMO      R1
        RET

; R1[7:0] -> 8N1 on uo_out[0], on the cycle counter. Uses R1, R2, R4, R5.
uo_byte: LDR     R2, #0xFF
        AND      R1, R1, R2
        ADDS     R1, R1, R1
        LDI      R2, 0x200
        OR       R1, R1, R2        ; start bit 0, data, stop bit
        LDR      R4, #10
        CNT_R    R5
ub:     LDR      R2, #1
        AND      R2, R1, R2
        OUT_W    R2
        SHR      R1, R1, #1
        LDR      R2, #UBIT
        ADDS     R5, R5, R2
        WAIT_CMP R5
        DECR     R4
        JNZ      ub
        RET
