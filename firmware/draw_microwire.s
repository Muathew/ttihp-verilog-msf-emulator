; Microwire controller for a 93C46 EEPROM in 16-bit mode (64 words, 6-bit
; addresses). Drawn at random (docs/draw/). The sequence: EWEN; WRITE word 5 =
; 0xBEEF and wait for ready with the status check on SO; READ word 5; READ word 6;
; EWDS. Then it reports the two words read, high byte first.
;   pins  : CS uio[0], SK uio[1], SI uio[2] (to the device), SO uio[3] (from it)
;   HALF  : half an SK period in cycles: 24 is 250 kHz at 12 MHz
;   report: 8N1 on uo_out[0] at 104 cycles a bit, then uo_out[1] high. A word read
;           with no dummy 0 before it, or no ready within READY_TMO cycles, sends 'E'
;           and the step number instead
; Sources: Microchip 93AA46/93LC46/93C46 data sheet, ST M93C46 data sheet
; (instruction set for x16 organisation: start bit 1, opcode, A5-A0, D15-D0 MSB
; first; SI is taken on the rising edge of SK and SO changes after it; READ puts
; a dummy 0 on SO after A0, then D15-D0; after WRITE's last bit, CS low starts the
; self-timed write; with CS high again SO is low while busy and high when ready,
; the status check; EWEN = 1 00 11xxxx, EWDS = 1 00 00xxxx; SK up to 1 MHz at
; low voltage, tCSL >= 250 ns, tPD <= 1 us). The status is read a half period
; (2 us) after CS rises, past tSV.
.include "msf.inc"
.equ CS,   0x01
.equ SK,   0x02
.equ SI,   0x04
.equ SO_PIN, 3
.equ HALF, 24
.equ UBIT, 104
.equ READY_TMO, 60000              ; 5 ms: twice the longest write time
.equ EWEN,  (0b10011 << 4)         ; 9-bit instructions: start, opcode, address
.equ EWDS,  (0b10000 << 4)
.equ WRITE, (0b101 << 6)
.equ READ,  (0b110 << 6)
.equ WADDR, 5
.equ WDATA, 0xBEEF
.equ RADDR2, 6
.equ OUT,  0                       ; RAM 0-1: the words read
.equ RAM_ARG, 8                    ; rd's destination, and a saved return address
.equ RAM_RET, 9

start:  UO_WI    0x01              ; the report line idles high
        PIN_WI   0
        OE_SET   (CS | SK | SI)
        LDR      R0, #HALF
        ; EWEN
        CALL     cs_on
        LDI      R3, EWEN << 7
        LDR      R4, #9
        CALL     xfer
        CALL     cs_off
        ; WRITE word 5
        CALL     cs_on
        LDI      R3, (WRITE | WADDR) << 7
        LDR      R4, #9
        CALL     xfer
        LDI      R3, WDATA
        LDR      R4, #16
        CALL     xfer
        CALL     cs_off            ; the write starts
        PIN_SET  CS                ; the status check
        CNT_R    R5
        ADDS     R5, R5, R0
        WAIT_CMP R5                ; past tSV
        LDI      R1, READY_TMO
        TMO      R1
        WAIT_EDGE #(0x50 | SO_PIN) ; SO high: ready
        LDR      R6, #2
        JZ       fail
        LDI      R1, 0xFFFF
        TMO      R1
        CALL     cs_off
        ; READ word 5, READ word 6
        LDR      R1, #WADDR
        LDR      R2, #OUT
        CALL     rd
        LDR      R1, #RADDR2
        LDR      R2, #OUT + 1
        CALL     rd
        ; EWDS
        CALL     cs_on
        LDI      R3, EWDS << 7
        LDR      R4, #9
        CALL     xfer
        CALL     cs_off
        ; the report
        LDR      R6, #OUT
        LD       R3, R6
        CALL     word
        LDR      R6, #OUT + 1
        LD       R3, R6
        CALL     word
        UO_WI    0x03              ; done
halt:   JMP      halt

; READ word R1 into RAM[R2]. Uses everything.
rd:     LDR      R6, #RAM_ARG
        ST       R6, R2
        LDR      R6, #RAM_RET
        ST       R6, R7
        LDI      R3, READ
        OR       R3, R3, R1
        SHL      R3, R3, #7        ; READ | address, at the top
        CALL     cs_on
        LDR      R4, #9
        CALL     xfer
        LDR      R6, #1
        AND      R1, R2, R6        ; the dummy bit, read during A0
        LDR      R6, #3
        ADDS     R1, R1, R1
        JNZ      fail
        LDR      R3, #0
        LDR      R4, #16
        CALL     xfer
        LDR      R6, #RAM_ARG
        LD       R1, R6
        ST       R1, R2
        CALL     cs_off
        LDR      R6, #RAM_RET
        LD       R7, R6
        RET

; CS high, SK low. Each routine starts its own deadlines from CNT: the bus is
; static, so the time between routines only stretches a phase
cs_on:  PIN_WI   CS
        RET
; a half period after the last fall, CS low, held for a whole period
cs_off: CNT_R    R5
        ADDS     R5, R5, R0
        WAIT_CMP R5
        PIN_WI   0
        ADDS     R5, R5, R0
        ADDS     R5, R5, R0
        WAIT_CMP R5
        RET

; R4 bits of R3, first at bit 15, onto SI, CS high. R2 <- what SO showed a half
; period after each rising edge, the last in bit 0. Uses R1, R5, R6.
xfer:   LDR      R2, #0
        CNT_R    R5
x_bit:  SHR      R1, R3, #7
        SHR      R1, R1, #7
        SHR      R1, R1, #1
        SHL      R1, R1, #2        ; the bit, on SI
        LDR      R6, #CS
        OR       R1, R1, R6
        PIN_W    R1                ; SK low, SI = the bit, after the last fall
        ADDS     R3, R3, R3
        ADDS     R5, R5, R0
        WAIT_CMP R5
        PIN_SET  SK                ; the device takes SI and moves SO
        ADDS     R5, R5, R0
        WAIT_CMP R5
        PIN_R    R1                ; SO, a half period after the rise
        PIN_CLR  SK
        SHR      R1, R1, #SO_PIN
        LDR      R6, #1
        AND      R1, R1, R6
        ADDS     R2, R2, R2
        OR       R2, R2, R1
        DECR     R4
        JNZ      x_bit
        RET

; R6 = the step that failed: 'E', step, then done
fail:   MOV      R3, R6
        LDI      R1, 'E'
        CALL     uo_byte
        MOV      R1, R3
        CALL     uo_byte
        UO_WI    0x03
        JMP      halt

; R3 = a word: its high byte, then its low byte
word:   LDR      R6, #RAM_RET
        ST       R6, R7
        SWAP     R1, R3
        CALL     uo_byte
        MOV      R1, R3
        CALL     uo_byte
        LDR      R6, #RAM_RET
        LD       R7, R6
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
