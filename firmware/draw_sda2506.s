; Siemens SDA 2506-5 serial EEPROM (128 x 8), the chip as controller. Drawn at
; random (docs/draw/). The sequence: read 0x12; write 0xA7 to 0x34 (an erase, then
; a write, the sheet's reprogramming); read 0x34. Report the two bytes read.
;   pins  : CLK uio[0], DATA uio[1] (open drain both ways: the part's output is
;           open drain, and the chip releases DATA for a 1), CE# uio[2]
;   report: 8N1 on uo_out[0] at 104 cycles a bit, then uo_out[1] high
; Source: Siemens, SDA 2506-5 data sheet (Nonvolatile Memory 1-Kbit E2PROM,
; 06.94), Circuit Description, Pin Definitions and Characteristics:
; * CE = 1: the control word is clocked in on D, LSB first: a read is A0-A6 then
;   CB = 0; a write is D0-D7, A0-A6, CB = 1 (16 bits)
; * read: CE 1 -> 0 starts it; the first clock pulse loads the word, and from that
;   pulse's trailing edge D0 is on D; each further pulse shifts out the next bit;
;   CE 0 -> 1 ends it
; * reprogram: with CB = 1, D during chip enable chooses erase (D = 1) or write
;   (D = 0); after CE 1 -> 0 a start pulse on the clock begins programming at its
;   trailing edge, D held until then, and CE 0 -> 1 ends it: erase 10-20 ms,
;   write 10-20 ms. An erase can be followed by a write without entering the
;   word again (the shift register keeps it)
; * clock high 2.5-60 us, low >= 5 us; CE edge to clock edge >= 5 us; CE edge to
;   D >= 2.5 us; D held >= 2.5 us before and after a falling edge; D out 2.5 us
;   after a falling edge.
; The sheet's timing figures are pictures this session could not read, so which
; edge the part takes a bit on comes from "data hold before/after falling edge":
; the falling one. Here: 10 us high, 10 us low, D moved 5 us after each fall, a
; read bit taken 10 us after its fall, 12 ms programming.
.include "msf.inc"
.equ CLK,  0x01
.equ DATA, 0x02
.equ CE,   0x04
.equ US,   12
.equ T_H,  10 * US                 ; clock high
.equ T_HALF, 5 * US                ; D moved this long after a fall, before a rise
.equ T_CE, 10 * US                 ; between a CE edge and a clock edge, or D
.equ T_RD, 10 * US                 ; a fall, then the bit is read
.equ T_PART, 48000                 ; 4 ms: programming is three of these, 12 ms
.equ UBIT, 104
.equ RA1,  0x12
.equ WA,   0x34
.equ WD,   0xA7
.equ OUT,  0                       ; RAM 0-1: the bytes read

start:  UO_WI    0x01              ; the report line idles high
        PIN_WI   CE                ; CE = 1, the clock low; DATA's OUT stays 0
        OE_WI    (CLK | CE)        ; DATA released
        CNT_R    R5
        LDI      R1, 100 * US
        ADDS     R5, R5, R1
        WAIT_CMP R5
        ; read 0x12
        LDR      R3, #RA1          ; A0-A6, CB = 0
        LDR      R4, #8
        CALL     enter
        CALL     read
        LDR      R1, #OUT
        ST       R1, R3
        ; reprogram 0x34: the data, the address and CB = 1, then erase and write
        LDI      R3, WD | WA << 8 | 1 << 15
        LDR      R4, #16
        CALL     enter
        LDR      R6, #1            ; erase: D high during chip enable
        CALL     prog
        LDR      R6, #0            ; write: D low
        CALL     prog
        ; read 0x34
        LDR      R3, #WA
        LDR      R4, #8
        CALL     enter
        CALL     read
        LDR      R1, #OUT + 1
        ST       R1, R3
        ; the report
        LDR      R6, #OUT
        LD       R1, R6
        CALL     uo_byte
        LDR      R6, #OUT + 1
        LD       R1, R6
        CALL     uo_byte
        UO_WI    0x03
halt:   JMP      halt

; clock R4 bits of R3 in, LSB first, CE = 1. Each bit: D, 5 us, rise, 10 us, fall
; (the part takes the bit), 5 us.
enter:  CNT_R    R5
e_bit:  LDR      R1, #1
        AND      R1, R3, R1
        ADDS     R1, R1, R1
        JZ       e_0
        OE_CLR   DATA              ; a 1: released
        JMP      e_1
e_0:    OE_SET   DATA              ; a 0: pulled low
e_1:    SHR      R3, R3, #1
        LDI      R1, T_HALF
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_SET  CLK
        LDI      R1, T_H
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  CLK
        LDI      R1, T_HALF
        ADDS     R5, R5, R1
        WAIT_CMP R5
        DECR     R4
        JNZ      e_bit
        OE_CLR   DATA
        RET

; read: CE 1 -> 0, eight pulses, a bit after each fall, CE 0 -> 1. R3 <- the byte.
read:   CNT_R    R5
        LDI      R1, T_CE
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  CE                ; chip enable: the read starts
        ADDS     R5, R5, R1
        WAIT_CMP R5
        LDR      R4, #8
        LDR      R3, #0
r_bit:  PIN_SET  CLK
        LDI      R1, T_H
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  CLK               ; the part puts out the next bit
        LDI      R1, T_RD
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_R    R1
        SHR      R1, R1, #1
        LDR      R2, #1
        AND      R1, R1, R2        ; D
        SHL      R1, R1, #7
        SHR      R3, R3, #1
        OR       R3, R3, R1        ; LSB first
        DECR     R4
        JNZ      r_bit
        PIN_SET  CE                ; CE 0 -> 1: D lets go
        LDI      R1, T_CE
        ADDS     R5, R5, R1
        WAIT_CMP R5
        RET

; one programming step: D = R6 (1 erase, 0 write) through chip enable and the
; start pulse, then 12 ms with CE = 0, then CE 0 -> 1
prog:   CNT_R    R5
        ADDS     R6, R6, R6
        JZ       p_w
        OE_CLR   DATA              ; D = 1: erase
        JMP      p_ce
p_w:    OE_SET   DATA              ; D = 0: write
p_ce:   LDI      R1, T_CE
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  CE                ; chip enable, with D steady
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_SET  CLK               ; the start pulse
        LDI      R1, T_H
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  CLK               ; programming begins
        LDI      R1, T_HALF
        ADDS     R5, R5, R1
        WAIT_CMP R5
        OE_CLR   DATA
        LDR      R4, #3
        LDI      R1, T_PART
p_t:    ADDS     R5, R5, R1
        WAIT_CMP R5
        DECR     R4
        JNZ      p_t
        PIN_SET  CE                ; programming ends
        LDI      R1, T_CE
        ADDS     R5, R5, R1
        WAIT_CMP R5
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
