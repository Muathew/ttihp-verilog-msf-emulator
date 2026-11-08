; HD44780 character LCD, 4-bit bus: initialise by instruction and write two lines.
;   pins  : RS uio[0], E uio[1], D4-D7 uio[4..7], push-pull (R/W tied low: the busy
;           flag is never read, every instruction is given its execution time instead)
;   table : cmds, pairs (what, the wait after it in cycles): what = NIB | n (a lone
;           nibble, during initialisation), or DATA | byte (RS = 1), or a command byte;
;           0xFFFF = the end
;   done  : uo_out = DONE
; Sources: Hitachi HD44780U data sheet (ADE-207-272): data latched on E's falling edge,
; E high at least 450 ns, the enable cycle at least 1000 ns, data set-up 195 ns; in
; 4-bit mode the high nibble first; Figure 24, initialising by instruction (4-bit):
; more than 40 ms after power-on (2.7 V; 15 ms at 4.5 V), 0011, more than 4.1 ms,
; 0011, more than 100 us, 0011, 0010, then function set, display off, clear, entry
; mode set; execution times 37 us, clear display 1.52 ms (fosc 270 kHz); Table 6 for
; the instruction codes, DDRAM addresses 0x00 (line 1) and 0x40 (line 2).
.include "msf.inc"
.equ RS,    0x01
.equ E,     0x02
.equ NIB,   0x8000
.equ DATA,  0x100
.equ END,   0xFFFF
.equ W_MIN, 600
.equ W_MAX, 60000
.equ T_EXE, 600                     ; 50 us (37 us + margin)
.equ T_CLR, 24000                   ; 2 ms (1.52 ms + margin)

start:  PIN_WI   0x00
        OE_SET   0xF3
        CNT_R    R5
        LDR      R4, #9             ; 45 ms after power-on: nine waits of 5 ms
pw:     LDI      R1, 60000
        ADDS     R5, R5, R1
        WAIT_CMP R5
        DECR     R4
        JNZ      pw
        LDI      R6, cmds
next:   LD       R3, R6
        INCR     R6
        LD       R2, R6             ; the wait after it
        INCR     R6
        INCR     R3
        JZ       fin
        DECR     R3
        LDR      R1, #0
        SUBS     R1, R3, R1         ; N: a lone nibble
        JN       lone
        LDI      R1, DATA           ; RS
        AND      R0, R3, R1
        SHR      R0, R0, #4
        SHR      R0, R0, #4         ; R0: RS (bit 0)
        LDI      R1, 0xF0
        AND      R4, R3, R1         ; the high nibble, already in D7..D4
        OR       R4, R4, R0
        PIN_W    R4
        PIN_SET  E
        NOP
        NOP
        NOP
        NOP
        NOP
        PIN_CLR  E                  ; taken (E high 6 cycles: 500 ns)
        SHL      R4, R3, #4         ; the low nibble into D7..D4
        AND      R4, R4, R1
        OR       R4, R4, R0
        PIN_W    R4
        NOP
        NOP
        NOP
        PIN_SET  E
        NOP
        NOP
        NOP
        NOP
        NOP
        PIN_CLR  E
        JMP      wait
lone:   SHL      R4, R3, #4         ; RS = 0, the nibble into D7..D4
        LDI      R1, 0xF0
        AND      R4, R4, R1
        PIN_W    R4
        PIN_SET  E
        NOP
        NOP
        NOP
        NOP
        NOP
        PIN_CLR  E
wait:   ADDS     R5, R5, R2         ; @range R2 W_MIN W_MAX
        WAIT_CMP R5
        JMP      next
fin:    UO_WI    DONE
halt:   JMP      halt

.equ T_41,  60000                   ; 5 ms (more than 4.1 ms)
.equ T_100, 2400                    ; 200 us (more than 100 us)
cmds:   .word    NIB | 0x3, T_41
        .word    NIB | 0x3, T_100
        .word    NIB | 0x3, T_100
        .word    NIB | 0x2, T_EXE
        .word    0x28, T_EXE        ; function set: 4 bits, 2 lines, 5 x 8 dots
        .word    0x08, T_EXE        ; display off
        .word    0x01, T_CLR        ; clear display
        .word    0x06, T_EXE        ; entry mode: increment, no shift
        .word    0x0C, T_EXE        ; display on, no cursor
        .word    DATA | 0x48, T_EXE, DATA | 0x45, T_EXE, DATA | 0x4C, T_EXE, DATA | 0x4C, T_EXE, DATA | 0x4F, T_EXE   ; "HELLO"
        .word    DATA | 0x2C, T_EXE, DATA | 0x20, T_EXE, DATA | 0x54, T_EXE, DATA | 0x54, T_EXE   ; ", TT"
        .word    0xC0, T_EXE        ; DDRAM address 0x40: line 2
        .word    DATA | 0x6D, T_EXE, DATA | 0x73, T_EXE, DATA | 0x66, T_EXE, DATA | 0x63, T_EXE, DATA | 0x6F, T_EXE   ; "msfco"
        .word    DATA | 0x72, T_EXE, DATA | 0x65, T_EXE, DATA | 0x20, T_EXE, DATA | 0x76, T_EXE, DATA | 0x33, T_EXE   ; "re v3"
        .word    END, 0
