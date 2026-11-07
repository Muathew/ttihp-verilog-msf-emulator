; The watching thread of the UART bus monitor, and its xlat: see mon_uart.s, which
; also describes the events. flight_uart.s uses the same code.
.include "msf.inc"
.equ LINE, 1
.equ BIT,  104
.equ TOL,  BIT >> 4

start:  LDI      R1, 10 * BIT
        TMO      R1                ; edge waits give up after a frame time
        LDI      R1, BIT
        RX_DIV   R1
        LDI      R1, (RX_EN | RX_CHAR | RX_LSB | (9 - 1) << 8 | LINE)
        RX_CFG   R1                ; 8 data bits + the stop bit
        LDI      R1, 64 | 5 << FIFO_N
        FIFO_CFG R1                ; FIFO: SRAM 64..127, empty
        LDI      R1, report
        TSTART   R1
fr:     WAIT_EDGE LINE, fall       ; start bit (or a glitch)
        JZ       nofr
        CNT_R    R3                ; t0
        WAIT_EDGE LINE, rise
        JZ       rxu               ; low for a whole frame: a break
        CNT_R    R2
        SUBS     R2, R2, R3        ; how long the line was low
        LDI      R4, BIT >> 1
        SUBS     R5, R2, R4
        JN       glitch
        ADDS     R2, R2, R4        ; offset = low time - n * BIT, n rounded:
        LDI      R4, BIT
fb:     SUBS     R2, R2, R4        ; take off whole bits ...
        JP       fb
        ADDS     R2, R2, R4
        LDI      R4, BIT >> 1
        SUBS     R2, R2, R4        ; ... the offset, in [-BIT/2, BIT/2)
        LDR      R0, #0            ; no 'B' event unless:
        LDI      R4, TOL + 1
        SUBS     R5, R2, R4
        JP       late
        LDI      R4, -TOL
        SUBS     R5, R2, R4
        JP       rx                ; within TOL either way
late:   LDI      R4, 127           ; clip to a signed byte
        SUBS     R5, R2, R4
        JN       clip1
        MOV      R2, R4
clip1:  LDI      R4, -128
        SUBS     R5, R2, R4
        JP       clip2
        MOV      R2, R4
clip2:  LDR      R4, #0xFF
        AND      R2, R2, R4
        LDI      R0, 'B' << 8
        OR       R0, R0, R2        ; the event, sent once the frame is known good
rx:     RX_POP   R1                ; 9 bits, stop bit in bit 8
        MOV      R2, R1
        CNT_R    R5
        SUBS     R5, R5, R3
        LDI      R4, 9 * BIT       ; a frame ends 9.5 bits after its start
        SUBS     R5, R5, R4        ; N: t0 was not this frame's start edge
        JN       byte
        MOV      R1, R0
        ADDS     R0, R0, R0        ; Z = no 'B' event
        JZ       byte
        FIFO_PUT R1
byte:   LDR      R4, #0xFF
        AND      R1, R2, R4        ; data bits
        SHR      R5, R2, #7
        SHR      R5, R5, #1        ; stop bit
        LDI      R4, 'U' << 8
        ADDS     R5, R5, R5        ; Z = stop bit low
        JNZ      ok
        LDI      R4, 'F' << 8
ok:     OR       R1, R1, R4
        FIFO_PUT R1
        JMP      fr
glitch: LDI      R1, 'G' << 8
        OR       R1, R1, R2
        FIFO_PUT R1
        JMP      rxu               ; then the byte it makes a receiver see
nofr:   STAT     R1                ; no start edge for a frame time: did the
        LDR      R4, #ST_RXAVAIL   ; RX engine get a frame whose start this
        AND      R1, R1, R4        ; thread missed (busy with the last one)?
        ADDS     R1, R1, R1
        JZ       fr
rxu:    RX_POP   R1                ; report it, untimed
        MOV      R2, R1
        JMP      byte

xlat:   LDR      R2, #0            ; events are pushed ready to send
        RET
