; Commodore serial bus (IEC) controller: the chip is the computer, and device 8
; a disk drive. Drawn at random (docs/draw/). Standard timing, no fast loader:
;   1. ATN: LISTEN 8 (0x28), SECOND 15 (0x6F), release ATN
;   2. send "I0" (0x49 0x30), EOI on the last byte
;   3. ATN: UNLISTEN (0x3F), release the bus
;   4. ATN: TALK 8 (0x48), SECOND 15 (0x6F), the turnaround
;   5. receive until EOI (the drive's status, "00, OK,00,00" and a CR)
;   6. ATN: UNTALK (0x5F), release the bus
; then report the bytes received.
;   pins  : DATA uio[0], CLK uio[1], ATN uio[2], open drain (pulled low only;
;           the bus's pull-ups make them high). A 1 bit is DATA released (high)
;   report: 8N1 on uo_out[0] at 104 cycles a bit, then uo_out[1] high; on a
;           timeout 'E' and the step code (see `fail`) instead
; Sources: the C64 KERNAL source, serial4.0 (Commodore, R. Fairbairn and RSR,
; 1980-82, in github.com/mist64/cbmsrc): the order of every step below follows
; its ISOUR, ISOURA, LIST1, TKATN, ACPTR and DLABYE, and its bit loop sends a 1
; with DATAHI (released); J. Butterfield, "How the VIC/64 Serial Bus Works",
; COMPUTE! 38, July 1983 (handshakes, EOI: a talker silent for 200 us, which the
; listener acknowledges by holding DATA >= 60 us; bits LSB first, held >= 20 us,
; 60 us for a C64; frame acknowledged within 1 ms; devices answer ATN within 1 ms;
; the turnaround); M. Steil, "Commodore Peripheral Bus: Part 4: Standard Serial",
; pagetable.com (60 us holds, >= 100 us between bytes). The holds here are 60 us.
.include "msf.inc"
.equ DATA, 0x01
.equ CLK,  0x02
.equ ATN,  0x04
.equ US,   12                      ; cycles a microsecond at 12 MHz
.equ T_S,  60 * US                 ; CLK pulled, the bit set up on DATA
.equ T_V,  60 * US                 ; CLK released: the bit is valid
.equ T_BB, 100 * US                ; between bytes
.equ T_ATN, 1000 * US              ; after ATN, every device has pulled DATA
.equ T_EOI, 256 * US               ; a talker this slow to start the bits means EOI
.equ T_EOIACK, 60 * US             ; holding DATA, to acknowledge an EOI
.equ T_FRAME, 1000 * US            ; for the frame handshake, and a bit
.equ T_LONG, 60000                 ; 5 ms, for the waits the bus leaves open
.equ T_REL, 60 * US                ; ATN released, then CLK and DATA
.equ UBIT, 104
.equ BUF,  16                      ; RAM 16-47: the bytes received
.equ BUFEND, 48
.equ PTR,  8
.equ RET1, 9
.equ RET2, 10
.equ EOIF, 11                      ; EOI seen in this byte

start:  UO_WI    0x01              ; the report line idles high
        PIN_CLR  (DATA | CLK | ATN) ; open drain: these only ever pull low
        OE_CLR   (DATA | CLK | ATN)
        LDR      R1, #0
        TMO      R1                ; only the handshake waits time out
        ; 1. LISTEN 8, SECOND 15
        LDR      R3, #0x28
        CALL     atn_cmd
        LDR      R3, #0x6F
        CALL     atn_more
        OE_CLR   ATN
        ; 2. "I0", EOI on the 0
        LDR      R3, #0x49
        LDR      R6, #0
        CALL     send
        LDR      R3, #0x30
        LDR      R6, #1
        CALL     send
        ; 3. UNLISTEN
        LDR      R3, #0x3F
        CALL     atn_cmd
        CALL     release
        ; 4. TALK 8, SECOND 15, and the turnaround: the drive talks from here
        LDR      R3, #0x48
        CALL     atn_cmd
        LDR      R3, #0x6F
        CALL     atn_more
        OE_SET   DATA              ; we listen: DATA held
        OE_CLR   ATN
        OE_CLR   CLK
        LDI      R1, T_LONG
        CALL     wt_clo            ; the drive takes CLK
        LDR      R0, #4
        JZ       fail
        ; 5. bytes until EOI
        LDR      R1, #BUF
        LDR      R2, #PTR
        ST       R2, R1
rx:     CALL     recv
        LDR      R2, #PTR
        LD       R1, R2
        ST       R1, R3
        INCR     R1
        ST       R2, R1
        LDR      R2, #BUFEND
        SUBS     R2, R1, R2
        LDR      R0, #11
        JZ       fail              ; more than the buffer holds
        LDR      R2, #EOIF
        LD       R1, R2
        ADDS     R1, R1, R1
        JZ       rx
        ; 6. UNTALK
        OE_SET   CLK
        OE_SET   ATN
        LDR      R3, #0x5F
        CALL     atn_cmd
        CALL     release
        ; the report
        LDR      R6, #BUF
rp:     LDR      R2, #PTR
        LD       R1, R2
        SUBS     R1, R6, R1
        JZ       rp_end
        LD       R1, R6
        CALL     uo_byte
        INCR     R6
        JMP      rp
rp_end: UO_WI    0x03
halt:   JMP      halt

; a command byte R3 under ATN (LIST1): ATN pulled, then as atn_more
atn_cmd: OE_CLR  DATA
        OE_SET   ATN
; another under the same ATN (ISOURA): CLK pulled, DATA released, 1 ms for every
; device to answer by pulling DATA, then the byte
atn_more:
        LDR      R2, #RET2
        ST       R2, R7
        OE_SET   CLK
        OE_CLR   DATA
        CNT_R    R5
        LDI      R1, T_ATN
        ADDS     R5, R5, R1
        WAIT_CMP R5
        LDR      R6, #0
        CALL     send
        LDR      R2, #RET2
        LD       R7, R2
        RET

; ATN released, then 60 us later CLK and DATA (DLABYE)
release: OE_CLR  ATN
        CNT_R    R5
        LDI      R1, T_REL
        ADDS     R5, R5, R1
        WAIT_CMP R5
        OE_CLR   CLK
        OE_CLR   DATA
        RET

; send R3 as talker (ISOUR); R6 = 1: with EOI. We hold CLK, the listener DATA.
send:   LDR      R2, #RET1
        ST       R2, R7
        OE_CLR   DATA
        NOP
        NOP
        NOP
        PIN_R    R1                ; DATA still low: a listener is there
        LDR      R2, #DATA
        AND      R1, R1, R2
        ADDS     R1, R1, R1
        LDR      R0, #1
        JNZ      fail
        OE_CLR   CLK               ; ready to send
        ADDS     R6, R6, R6
        JZ       s_go
        LDI      R1, T_LONG
        CALL     wt_dhi            ; EOI: the listener is ready, and we wait...
        LDR      R0, #2
        JZ       fail
        LDI      R1, T_FRAME
        CALL     wt_dlo            ; ...until it acknowledges the EOI
        LDR      R0, #3
        JZ       fail
s_go:   LDI      R1, T_LONG
        CALL     wt_dhi            ; ready for data
        LDR      R0, #2
        JZ       fail
        OE_SET   CLK
        LDR      R4, #8
s_bit:  CNT_R    R5
        PIN_R    R1                ; DATA released by everyone between bits
        LDR      R2, #DATA
        AND      R1, R1, R2
        ADDS     R1, R1, R1
        LDR      R0, #5
        JZ       fail
        LDR      R1, #1
        AND      R1, R3, R1
        ADDS     R1, R1, R1
        JZ       s_0
        OE_CLR   DATA              ; a 1: released
        JMP      s_1
s_0:    OE_SET   DATA              ; a 0: pulled
s_1:    SHR      R3, R3, #1
        LDI      R1, T_S
        ADDS     R5, R5, R1
        WAIT_CMP R5
        OE_CLR   CLK               ; the bit is valid
        LDI      R1, T_V
        ADDS     R5, R5, R1
        WAIT_CMP R5
        OE_SET   CLK
        OE_CLR   DATA
        DECR     R4
        JNZ      s_bit
        LDI      R1, T_FRAME
        CALL     wt_dlo            ; the listener has the byte
        LDR      R0, #6
        JZ       fail
        CNT_R    R5
        LDI      R1, T_BB
        ADDS     R5, R5, R1
        WAIT_CMP R5
        LDR      R2, #RET1
        LD       R7, R2
        RET

; receive a byte into R3 as listener (ACPTR); EOIF = 1 if it is the last
recv:   LDR      R2, #RET1
        ST       R2, R7
        LDR      R1, #0
        LDR      R2, #EOIF
        ST       R2, R1
        OE_CLR   CLK
        LDI      R1, T_LONG
        CALL     wt_chi            ; the talker is ready to send
        LDR      R0, #7
        JZ       fail
r_rfd:  OE_CLR   DATA              ; ready for data
        LDI      R1, T_EOI
        CALL     wt_clo            ; the talker starts the bits...
        JNZ      r_bits
        LDR      R2, #EOIF         ; ...or keeps quiet: the last byte
        LD       R1, R2
        ADDS     R1, R1, R1
        LDR      R0, #8
        JNZ      fail              ; quiet twice: the talker has gone
        LDR      R1, #1
        ST       R2, R1
        OE_SET   DATA              ; acknowledge the EOI
        CNT_R    R5
        LDI      R1, T_EOIACK
        ADDS     R5, R5, R1
        WAIT_CMP R5
        JMP      r_rfd
r_bits: LDR      R4, #8
        LDR      R3, #0
r_bit:  LDI      R1, T_FRAME
        CALL     wt_chi            ; CLK released: the bit is valid
        LDR      R0, #9
        JZ       fail
        PIN_R    R1
        LDR      R2, #DATA
        AND      R1, R1, R2        ; DATA high: a 1
        SHL      R1, R1, #7
        SHR      R3, R3, #1
        OR       R3, R3, R1
        LDI      R1, T_FRAME
        CALL     wt_clo
        LDR      R0, #10
        JZ       fail
        DECR     R4
        JNZ      r_bit
        OE_SET   DATA              ; the frame handshake: byte taken
        LDR      R2, #EOIF
        LD       R1, R2
        ADDS     R1, R1, R1
        JZ       r_done
        CNT_R    R5                ; the last byte: release the bus (DLADLH)
        LDI      R1, T_REL
        ADDS     R5, R5, R1
        WAIT_CMP R5
        OE_CLR   CLK
        OE_CLR   DATA
r_done: LDR      R2, #RET1
        LD       R7, R2
        RET

; wait for a line, at most R1 cycles; Z = 1 on a timeout. TMO is 0 (never) after.
wt_dlo: TMO      R1
        WAIT_EDGE #0x40            ; DATA low
        LDR      R1, #0
        TMO      R1
        RET
wt_dhi: TMO      R1
        WAIT_EDGE #0x50            ; DATA high
        LDR      R1, #0
        TMO      R1
        RET
wt_clo: TMO      R1
        WAIT_EDGE #0x41            ; CLK low
        LDR      R1, #0
        TMO      R1
        RET
wt_chi: TMO      R1
        WAIT_EDGE #0x51            ; CLK high
        LDR      R1, #0
        TMO      R1
        RET

; a timeout: R0 = the step. Report 'E' and the step, let go of the bus, stop.
;  1 no device on the bus  2 listener never ready  3 no EOI acknowledgement
;  4 no turnaround  5 DATA held during a byte  6 no frame handshake
;  7 talker never ready  8 talker stopped  9, 10 a bit too slow  11 too many bytes
fail:   OE_CLR   (DATA | CLK | ATN)
        LDI      R1, 'E'
        CALL     uo_byte
        MOV      R1, R0
        CALL     uo_byte
        UO_WI    0x03
        JMP      halt

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
