; Bus monitor, SPI: listens to CS, SCK, MOSI and MISO without driving them.
; Thread 0 samples the bus, thread 1 reports events (report.inc).
;   pins  : SCK uio[2], MOSI uio[3], CS uio[5] (active low), MISO uio[7]
;           (inputs); reports on uio[0]
;   MODE  : SPI mode 0..3 (define): bits are sampled on the edge the mode
;           samples on, MSB first
; Events (code, data), per transfer (CS low .. CS high):
;   '>' a byte on MOSI, then '<' the byte on MISO in the same 8 clocks
;   then, once CS is high again:
;   'M' SCK was not at the mode's idle level (CPOL) as CS fell: the
;       controller uses another mode (data: SCK's level)
;   'E' the transfer ended: data = bits of a byte left over (0: the
;       transfer was whole bytes). Every transfer ends with 'E'.
; Nothing is pushed while CS is low except two words per byte (two FIFO_PUTs),
; so thread 0 keeps up with SCK periods of 28 cycles inside a byte and 36
; from a byte's last sampling edge to the next byte's first (430 / 330 kHz at
; 12 MHz), and with 16 cycles from CS low to the first sampling edge (and 6
; to SCK's first edge, for the mode check). CS is looked at once SCK has
; been still for POLL cycles, so CS must stay high for longer than that.
; The reporter sends two events per byte, about 480 cycles: faster traffic
; is buffered in the FIFO (32 bytes), then dropped and counted ('O').
.include "msf.inc"
.equ MODE,   0
.equ CPOL,   MODE >> 1
.equ S,      ((MODE >> 1) ^ MODE ^ 1) & 1  ; SCK level of the sampling edge
.equ SCKP,   2
.equ MOSIP,  3
.equ CSP,    5
.equ MISOP,  7                     ; (the top pin: PIN_R >> 7 is the bit)
.equ SCK,    1 << SCKP
.equ CS,     1 << CSP
.equ POLL,   40                    ; SCK still this long: look at CS
.equ WNS,    (5 - S) << 4 | SCKP   ; WAIT_EDGE: SCK at the other level
.equ WS,     (4 + S) << 4 | SCKP   ; WAIT_EDGE: SCK at the sampling level

; R0 = the lines as CS fell, R2 = MISO bits of this byte (in its low 8
; bits), R3 = bits left in it, SR = its MOSI bits (low 8 bits),
; R1 / R4 / R5 scratch
start:  LDR      R1, #(7 | MOSIP << 3)
        SER_CFG  R1                ; SR in = MOSI, MSB first
        LDI      R1, 64 | 5 << FIFO_N
        FIFO_CFG R1                ; FIFO: SRAM 64..127, empty
        LDI      R1, report
        TSTART   R1
idle:   LDR      R4, #0
        TMO      R4
        WAIT_EDGE CSP, low         ; (a level: cannot be missed)
        PIN_R    R0                ; the lines as CS fell
        LDR      R1, #POLL
        TMO      R1
        LDR      R3, #8
; ---- every bit: wait for the sampling edge, sample MOSI and MISO
bit:    WAIT_EDGE WNS
        JNZ      ba
        CALL     cschk
        JMP      bit
ba:     WAIT_EDGE WS
        JNZ      bb
        CALL     cschk
        JMP      ba
bb:     SHIFT_IN
        PIN_R    R1
        SHR      R1, R1, #MISOP
        SHL      R2, R2, #1
        OR       R2, R2, R1
        DECR     R3
        JNZ      bit
; ---- a byte: '>' MOSI then '<' MISO (a full FIFO drops and counts them)
        SR_RD    R1
        LDH      R1, #'>'
        LDH      R2, #'<'
        FIFO_PUT R1
        FIFO_PUT R2
        LDR      R3, #8
        JMP      bit

; ---- SCK still for POLL cycles: return if CS is still low, else the
; transfer ended: 'M', 'E'
cschk:  PIN_R    R1
        LDR      R4, #CS
        AND      R1, R1, R4
        ADDS     R1, R1, R1
        JNZ      ended
        RET
ended:  LDR      R4, #SCK
        AND      R1, R0, R4
        LDR      R4, #(CPOL << SCKP)
        SUBS     R1, R1, R4
        JZ       e1                ; SCK was at its idle level
        LDR      R1, #(1 - CPOL)
        LDH      R1, #'M'
        FIFO_PUT R1
e1:     LDR      R1, #8
        SUBS     R1, R1, R3        ; bits left over
        LDH      R1, #'E'
        FIFO_PUT R1
        JMP      idle

; ---- thread 1: the words are events already
xlat:   LDR      R2, #0
        RET

.include "report.inc"
