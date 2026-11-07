; In-line interposer, UART: sits in the wire between two devices and passes both
; directions through, so that neither can tell, until it changes exactly one thing.
; No host takes part: what to do, and to which frame, is set when it is assembled.
;   pins  : A's TX -> uio[1]; uio[2] -> B's RX; B's TX -> uio[3]; uio[4] -> A's RX;
;           reports on uio[0]
;   BIT   : the line's bit time in cycles (define; 104 = 115200 at 12 MHz)
;   ACTION: 0 pass everything; 1 flip bit K of frame N going from A to B (the frame
;           count starts at 0, bit K counts data bits from 0); 2 drop frame N (defines)
; Thread 0 is the wire: it looks at the pins and drives them again every 16 cycles, so
; an edge comes out 15 to 30 cycles late, on each direction. That loop never depends on
; what thread 1 does, since a thread's timing depends only on which threads run. At
; 104 cycles a bit a receiver has room for it; test_interpose.py measures it.
; Thread 1 reads the same line with the RX engine, counts frames, and tells thread 0
; what to change through one word of data RAM, on the cycle it has to change.
; Report: ('F', byte) for each frame from A, as read; ('A', N) when the action is done.
.include "msf.inc"
.equ BIT,    104
.equ ACTION, 0
.equ N,      2
.equ K,      3
.equ AIN,    1                     ; uio[1] -> uio[2]
.equ BIN,    3                     ; uio[3] -> uio[4]
.equ OUTS,   (1 << (AIN + 1)) | (1 << (BIN + 1))
.equ MASKW,  8                     ; data RAM: what thread 0 applies to its outputs
.equ REPORT, 0
.equ DROP,   2

start:  LDR      R1, #0
        TMO      R1                ; no timeouts
        LDR      R2, #MASKW
        ST       R2, R1
        LDR      R1, #ACTION       ; drop: OR holds the line high; flip: XOR changes it
        LDR      R2, #DROP
        SUBS     R2, R1, R2
        JNZ      s1
        LDI      R1, t_or
        LD       R1, R1
        LDI      R2, t_op
        ST       R2, R1
        NOP
        NOP
s1:     LDR      R1, #OUTS
        PIN_W    R1                ; both lines idle high before they are driven
        OE_SET   (OUTS | 1 << REPORT)
        LDI      R1, reader
        TSTART   R1
        LDR      R3, #OUTS
        LDR      R5, #MASKW
; ---- thread 0, the wire: 8 slots, 16 cycles with two threads running
wire:   PIN_R    R1
        SHL      R2, R1, #1        ; uio[1] -> uio[2], uio[3] -> uio[4]
        AND      R2, R2, R3
        LD       R4, R5
t_op:   XOR      R2, R2, R4
        PIN_W    R2
        JMP      wire
t_or:   OR       R2, R2, R4        ; (copied over t_op to drop)

; ---- thread 1: reads the line from A with the RX engine, and acts
reader: LDI      R1, BIT
        RX_DIV   R1
        TX_DIV   R1
        LDI      R1, (RX_EN | RX_CHAR | RX_LSB | (8 - 1) << 8 | AIN)
        RX_CFG   R1
        LDI      R1, 12            ; the report at 1 Mbaud, at 12 MHz
        TX_DIV   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | REPORT)
        TX_CFG   R1
        LDI      R0, 0x200
        LDR      R6, #0            ; frames so far
        LDR      R1, #ACTION
        ADDS     R1, R1, R1
        JZ       r_any             ; nothing to do: only count
        LDR      R1, #N
        ADDS     R1, R1, R1
        JNZ      r_any             ; frame 0 needs setting up before it comes
        CALL     act
r_any:  RX_POP   R1
        LDR      R2, #0xFF
        AND      R1, R1, R2
        LDH      R1, #'F'
        CALL     tx_ev
        INCR     R6
        LDR      R1, #ACTION
        LDR      R2, #DROP         ; a drop ends with its frame
        SUBS     R2, R1, R2
        JNZ      r_n
        LDR      R2, #N
        INCR     R2
        SUBS     R2, R6, R2
        JNZ      r_n
        LDR      R1, #0            ; the frame is over: let the line go again
        LDR      R2, #MASKW
        ST       R2, R1
        JMP      r_done
r_n:    LDR      R1, #ACTION
        ADDS     R1, R1, R1
        JZ       r_any
        LDR      R2, #N
        SUBS     R2, R6, R2
        JNZ      r_any
        CALL     act               ; frame N is next
        LDR      R1, #ACTION
        LDR      R2, #DROP
        SUBS     R2, R1, R2
        JZ       r_any             ; a drop waits for the frame to go by
r_done: LDI      R1, 'A' << 8 | N
        CALL     tx_ev
        JMP      r_any

; frame N is next. A drop holds the line high from now on, before its start bit. A flip
; waits for the start edge and changes data bit K for one bit time.
act:    MOV      R5, R7
        LDR      R1, #ACTION
        LDR      R2, #DROP
        SUBS     R2, R1, R2
        JNZ      a_flip
        LDR      R1, #(1 << (AIN + 1))
        LDR      R2, #MASKW
        ST       R2, R1
        JR       R5
a_flip: WAIT_EDGE AIN, fall
        CNT_R    R3
        LDI      R2, (K + 1) * BIT
        ADDS     R3, R3, R2
        WAIT_CMP R3
        LDR      R1, #(1 << (AIN + 1))
        LDR      R2, #MASKW
        ST       R2, R1
        LDI      R2, BIT
        ADDS     R3, R3, R2
        WAIT_CMP R3
        LDR      R1, #0
        LDR      R2, #MASKW
        ST       R2, R1
        JR       R5

; R1 = event -> its two bytes; keeps R3, R5, R6
tx_ev:  MOV      R4, R7
        SHR      R2, R1, #7
        SHR      R2, R2, #1
        CALL     tx_b
        LDR      R2, #0xFF
        AND      R2, R1, R2
        CALL     tx_b
        JR       R4
tx_b:   ADDS     R2, R2, R2
        OR       R2, R2, R0
        TX_PUSH  R2
        RET
