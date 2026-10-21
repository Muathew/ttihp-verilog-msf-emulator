; SPI target: the chip plays a 64-register device (sensor or EEPROM style)
; for an SPI controller. There is no SPI hardware in the chip: the shift
; register, level waits and exact instruction timing do it in firmware.
;   pins    : SCK uio[2], MOSI uio[3], CS uio[5] inputs (CS active low);
;             MISO uio[4], driven only while CS is low
;   data RAM: [0..63] the device's registers (bytes), [64] status byte sent
;             on MISO during the command byte
;   transfer: CS low, command byte = read (bit 7: 1 read, 0 write) and
;             address (bits 5..0, bit 6 ignored), then any number of data
;             bytes. Each data byte returns the register's current value on
;             MISO (no dummy byte); in a write the byte received replaces it.
;             The address increments after each byte (mod 64). CS high ends
;             the transfer, also in the middle of a byte.
;   MODE    : SPI mode 0..3 (assembler define, default 0)
; Timing in core clock cycles from an SCK sampling edge at the pin to the
; next MISO bit at the pin (docs/instruments.md derives them from the ISA costs,
; test_targets.py measures them): 5 inside a byte, 8 between data bytes,
; 5 / 13 after the command byte (even / odd address); CS low -> MISO
; driven: 4. Limits: SCK period >= 16 cycles (750 kHz at 12 MHz; the two
; register loads and the even-address guess must fit between the last two
; command bits), CS low -> first SCK edge >= 8, CS high >= 32.
.include "msf.inc"
.equ MODE,   0
.equ S,      ((MODE >> 1) ^ MODE ^ 1) & 1  ; SCK level of the sampling edge
.equ SCKP,   2
.equ MOSIP,  3
.equ MISOP,  4
.equ CSP,    5
.equ MISO,   1 << MISOP
.equ CS,     1 << CSP
.equ STATUS, 64
.equ POLL,   12                    ; SCK idle this long -> look at CS
.equ WNS,    (5 - S) << 4 | SCKP   ; WAIT_EDGE: SCK at the other level
.equ WS,     (4 + S) << 4 | SCKP   ; WAIT_EDGE: SCK at the sampling level
.equ STW,    0x7800 | 5 << 8 | 6 << 5 | 7   ; ST R5, R6
.equ NOPW,   0x7000

; R0 = address mask, R7 = CS mask, R5 = register on MISO, R3 = the next
; one (byte in bits 15..8, ready for the SR), R4 = bit count, R1 = scratch
; (never live across a wait: the timeout handlers use it)
start:
        PIN_WI   0x00
        OE_WI    0x00              ; all inputs, MISO released
        LDR      R1, #(MISOP | MOSIP << 3)
        SER_CFG  R1                ; SR out = MISO, SR in = MOSI, MSB first
        LDR      R0, #0x3F
        LDR      R7, #CS
idle:   OE_CLR   MISO
        LDR      R1, #STATUS
        LD       R3, R1
        SWAP     R3, R3
        SR_LD    R3
        LDR      R4, #0
        SHIFT_OUT R4               ; MISO's OUT bit = status bit 7, pin released
        TMO      R4                ; wait for CS without a timeout
        WAIT_EDGE CSP, low         ; selected (a level wait cannot miss it)
        OE_SET   MISO              ; status bit 7 on the pin
        LDR      R1, #POLL
        TMO      R1                ; from now on SCK waits time out to check CS

; ---- command byte ----
c1n:    WAIT_EDGE WNS              ; bit 1: read / write
        JZ       c1nt
c1s:    WAIT_EDGE WS
        JZ       c1st
        XFER                       ; MOSI in, next status bit out
        SR_RD    R1
        LDR      R4, #1
        AND      R1, R1, R4        ; 1 = read
        DECR     R1                ; Z = read
        LDI      R6, STW
        JNZ      c2n
        LDI      R6, NOPW
c2n:    WAIT_EDGE WNS              ; bit 2
        JZ       c2nt
c2s:    WAIT_EDGE WS
        JZ       c2st
        XFER
        LDI      R1, d_store
        ST       R1, R6            ; patch the data loop: ST in a write, NOP in a read
        LDR      R4, #4
cbn:    WAIT_EDGE WNS              ; bits 3..6
        JZ       cbnt
cbs:    WAIT_EDGE WS
        JZ       cbst
        XFER
        DECR     R4
        JNZ      cbn
c7n:    WAIT_EDGE WNS              ; bit 7
        JZ       c7nt
c7s:    WAIT_EDGE WS
        JZ       c7st
        XFER                       ; SR[4:0] = address bits 5..1
        SR_RD    R1
        SHL      R2, R1, #1
        AND      R2, R2, R0        ; even address
        LD       R3, R2
        LDR      R4, #1
c8n:    WAIT_EDGE WNS              ; bit 8: address bit 0
        JZ       c8nt
        INCR     R2
        LD       R6, R2            ; the two registers the address can still be
        SWAP     R3, R3
        SR_LD    R3                ; guess even: its first bit goes out with bit 8
c8s:    WAIT_EDGE WS
        JZ       c8st
        XFER                       ; even register's bit 7 out, address bit 0 in
        SR_RD    R1
        AND      R1, R1, R4
        ADDS     R1, R1, R1        ; Z = even address
        JNZ      c_odd
        DECR     R2
        MOV      R5, R2
; first data byte, bit 1 (one copy per guess outcome: no join on the way)
e1n:    WAIT_EDGE WNS
        JZ       e1nt
        MOV      R2, R5
        INCR     R2
        AND      R2, R2, R0        ; the register after this one
e1s:    WAIT_EDGE WS
        JZ       e1st
        XFER
        JMP      d2n
c_odd:  SWAP     R6, R6
        SR_LD    R6                ; wrong guess: the odd register instead
        SHIFT_OUT R3               ; its bit 7 (R3[0] = 0 shifts in a 0)
        MOV      R5, R2
o1n:    WAIT_EDGE WNS
        JZ       o1nt
        MOV      R2, R5
        INCR     R2
        AND      R2, R2, R0
o1s:    WAIT_EDGE WS
        JZ       o1st
        XFER
        JMP      d2n

; ---- data bytes ----
d8n:    WAIT_EDGE WNS              ; bit 8
        JZ       d8nt
d8s:    WAIT_EDGE WS
        JZ       d8st
        SHIFT_IN
        SR_RD    R6                ; the byte received for register R5
        SR_LD    R3                ; register R5 + 1
        SHIFT_OUT R4               ; first bit of it (R4 = 0)
d_store: ST      R5, R6            ; patched: ST in a write, NOP in a read
        INCR     R5
        AND      R5, R5, R0
d1n:    WAIT_EDGE WNS              ; bit 1
        JZ       d1nt
        MOV      R2, R5
        INCR     R2
        AND      R2, R2, R0        ; the register after this one
d1s:    WAIT_EDGE WS
        JZ       d1st
        XFER
d2n:    WAIT_EDGE WNS              ; bit 2
        JZ       d2nt
        LD       R3, R2
        SWAP     R3, R3
d2s:    WAIT_EDGE WS
        JZ       d2st
        XFER
        LDR      R4, #5
dbn:    WAIT_EDGE WNS              ; bits 3..7
        JZ       dbnt
dbs:    WAIT_EDGE WS
        JZ       dbst
        XFER
        DECR     R4
        JNZ      dbn
        JMP      d8n

; ---- a wait timed out (SCK idle POLL cycles): still selected -> wait
; again (level waits lose nothing), deselected -> drop the transfer ----
c1nt:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       c1n
        JMP      idle
c1st:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       c1s
        JMP      idle
c2nt:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       c2n
        JMP      idle
c2st:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       c2s
        JMP      idle
cbnt:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       cbn
        JMP      idle
cbst:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       cbs
        JMP      idle
c7nt:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       c7n
        JMP      idle
c7st:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       c7s
        JMP      idle
c8nt:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       c8n
        JMP      idle
c8st:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       c8s
        JMP      idle
e1nt:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       e1n
        JMP      idle
e1st:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       e1s
        JMP      idle
o1nt:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       o1n
        JMP      idle
o1st:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       o1s
        JMP      idle
d8nt:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       d8n
        JMP      idle
d8st:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       d8s
        JMP      idle
d1nt:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       d1n
        JMP      idle
d1st:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       d1s
        JMP      idle
d2nt:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       d2n
        JMP      idle
d2st:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       d2s
        JMP      idle
dbnt:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       dbn
        JMP      idle
dbst:   PIN_R    R1
        AND      R1, R1, R7
        ADDS     R1, R1, R1
        JZ       dbs
        JMP      idle
