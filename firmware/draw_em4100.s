; EM4100 read-only RFID tag: the chip sends a tag's 64-bit frame for ever, as a
; tag does while it is in the reader's field. Drawn at random (docs/draw/).
;   pin   : data uio[0], push-pull (the modulator: high = coil not damped)
;   VER   : version/customer byte; DATA_HI, DATA_LO: the 32 data bits
;   HALF  : half a bit in cycles. RF/64 at 125 kHz is 512 us a bit, 6144
;           cycles at 12 MHz
; Sources: EM Microelectronic, EM4100 and EM4102 data sheets (Memory Array for
; Manchester and Bi-Phase encoding ICs; Code Description): 9 header bits all 1,
; 10 rows of 4 data bits D00-D93 each with an even row parity bit, 4 even column
; parity bits PC0-PC3 over the 10 rows, stop bit S0 = 0; the 64 bits repeat
; until power goes off; data rate 64 periods of the field. Manchester polarity:
; the EM4100 sheet's figure is a picture this session could not read; its text
; says only that the data stream's high is the modulator off. The EM4200 sheet
; (same maker, same code) says a 1 is high for the first half of the bit and low
; for the second, which this follows. The TX engine's Manchester is IEEE 802.3
; (1 = low then high), so the words are sent inverted.
; D00 is the most significant bit of the first nibble of VER, as the sheets'
; table reads (version bits D00-D13).
.include "msf.inc"
.equ PIN,     0
.equ HALF,    3072
.equ VER,     0x1A
.equ DATA_HI, 0x2B3C
.equ DATA_LO, 0x4D5E
.equ FR,      0                    ; RAM 0-3: the frame, first bit in bit 15 of word 0
.equ SRC,     4                    ; RAM 4-6: the ten nibbles, then the column parity
.equ NIBS,    11                   ; ten rows, then the column parity row

start:  LDI      R1, (VER << 8) | (DATA_HI >> 8)
        LDR      R6, #SRC
        ST       R6, R1
        MOV      R3, R1            ; R3: the nibbles XORed, for the column parity
        LDI      R1, ((DATA_HI & 0xFF) << 8) | (DATA_LO >> 8)
        INCR     R6
        ST       R6, R1
        XOR      R3, R3, R1
        LDI      R1, (DATA_LO & 0xFF) << 8
        INCR     R6
        ST       R6, R1
        XOR      R3, R3, R1        ; four columns of 4 bits: fold them into one
        SHL      R1, R3, #4
        XOR      R1, R3, R1        ; top nibble ^= the next
        SHL      R5, R1, #7
        ADDS     R5, R5, R5        ; << 8
        XOR      R1, R1, R5        ; top nibble ^= the two below it
        LDI      R5, 0xF000
        AND      R1, R1, R5        ; the column parity, at the top
        SHR      R1, R1, #7
        SHR      R1, R1, #1        ; to nibble 10's place, after nibble 9 in the last word
        LDI      R5, (DATA_LO & 0xFF) << 8
        OR       R1, R1, R5
        ST       R6, R1
        ; the frame, bit by bit: putk keeps R2 (bits so far), R4 (room), R6 (where)
        LDR      R2, #0
        LDR      R4, #16
        LDR      R6, #FR
        LDI      R3, 0xFF80        ; nine 1s
        LDR      R5, #9
        CALL     putk
        LDR      R0, #0            ; R0: the nibble
row:    SHR      R1, R0, #2
        LDR      R5, #SRC
        ADDS     R1, R1, R5
        LD       R3, R1            ; the word holding nibble R0
        LDR      R5, #3
        AND      R5, R0, R5        ; its place in the word (AND sets no flags)
        INCR     R5
r_sh:   DECR     R5
        JZ       r_top
        SHL      R3, R3, #4
        JMP      r_sh
r_top:  LDI      R5, 0xF000
        AND      R3, R3, R5        ; the nibble, alone at the top
        LDR      R1, #NIBS - 1
        SUBS     R1, R0, R1
        JZ       r_put             ; the column parity row ends with the stop bit, 0
        SHL      R1, R3, #1
        XOR      R1, R3, R1        ; bit 15 = b15^b14, bit 13 = b13^b12
        SHL      R5, R1, #2
        XOR      R1, R1, R5        ; bit 15: the nibble's parity
        SHR      R1, R1, #4
        LDI      R5, 0x0800
        AND      R1, R1, R5        ; even parity, after the nibble
        OR       R3, R3, R1
r_put:  LDR      R5, #5
        CALL     putk
        INCR     R0
        LDR      R1, #NIBS
        SUBS     R1, R0, R1
        JNZ      row
        ; send it for ever: Manchester, 16 bits a push, first bit first
        LDR      R1, #0
        TMO      R1                ; a push waits up to a word, 98,304 cycles
        PIN_CLR  (1 << PIN)
        OE_SET   (1 << PIN)
        LDI      R1, HALF
        TX_DIV   R1
        LDI      R1, (TX_EN | TX_MANCH | (16 - 1) << 8 | PIN)
        TX_CFG   R1
        LDI      R5, 0xFFFF
send:   LDR      R6, #FR
        LDR      R4, #4
s_w:    LD       R1, R6
        XOR      R1, R1, R5        ; EM's 1 is high then low: 802.3's 0
        TX_PUSH  R1
        INCR     R6
        DECR     R4
        JNZ      s_w
        JMP      send

; the top R5 bits of R3, first bit first, onto the frame; R1 is scratch
putk:   SHR      R1, R3, #7
        SHR      R1, R1, #7
        SHR      R1, R1, #1        ; the top bit
        ADDS     R2, R2, R2
        OR       R2, R2, R1
        ADDS     R3, R3, R3
        DECR     R4
        JNZ      pk_n
        ST       R6, R2            ; a full word
        INCR     R6
        LDR      R4, #16
pk_n:   DECR     R5
        JNZ      putk
        RET
