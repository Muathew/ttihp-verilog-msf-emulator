; v3 board peer for PMOD JA: an LP-MSPM0G3519 (mcu_ext/protocol_tests, v3_test.c)
; tests what v3 added against the FPGA's core, which runs free at 12 MHz:
;   * RX mode 3, the phase-tracking Manchester receiver, at 5 cycles a bit: the
;     ratio 10BASE-T has at 50 MHz. Ethernet frames, preamble and SFD, FCS checked.
;   * the NRZI receiver with de-stuffing and resync at 4 cycles a bit: the ratio
;     USB full speed has at 48 MHz. 36-byte packets, CRC16 checked.
;   * the late guard: a late WAIT_CMP takes the guarded pin off the bus.
; The MCU draws the line signals with its SPI: MOSI at 16 Mbit/s, each line symbol
; 3 to 6 SPI bits. To place them on our clock it first needs the ratio of its clock
; to ours, so it sends a known pattern and this program times it. fpga/ps/mcu_v3
; loads this and prints what it sends the MCU.
;   UIO1  UART TX to the MCU, 115200 8N1: prompts and verdicts (the only way back)
;   UIO6  the line (the MCU's MOSI)
;   UIO7  the guarded output (the MCU's POCI pin: it reads it with its own pull-up,
;         then pull-down, so it can tell driven low from off the bus)
;   UIO2  an output the guard does not name (SCL: the LaunchPad pulls it up; not SDA,
;         whose LED could hold a released line low)
; One thread. A pass, driven from here; the MCU follows:
;   'S'             calibration: on MOSI the MCU sends 0x0000 x 4, then 0xFFFF 0xFFFF
;                   0x0000 0x0000 ...: CAL_EDGES + 1 transitions 32 SPI bits apart,
;                   the first rising
;   's' Tl Th       cycles from the first transition to the last (0: not all seen)
;   'M'             Manchester receiver armed (mode 3, RX_DIV 13107)
;   'm' st Ll Lh    st 0x81 FCS right, 0x82 FCS wrong, 0x83 no SFD, 0x84 nothing
;                   came; L = bytes after the SFD, the FCS included
;                   (M_FRAMES of these)
;   'U'             NRZI receiver armed (RX_DIV 4, de-stuffing, resync)
;   'u' st n b0 b1  st 0x81 CRC16 right, 0x82 wrong, 0x84 not all U_BYTES came;
;                   n bytes; the first two (SYNC, PID)
;                   (U_PACKETS of these)
;   'A'             UIO7 and UIO2 driven low; the guard names T0 and UIO7
;   'B' Ll Lh       T0 has been late: UIO7 off the bus, UIO2 still driven; LATE_RD
;   'C' Ll Lh       T0's LATE flag cleared: UIO7 driven again
;   'D' Ll Lh       the guard names T1 only; T0 late again: UIO7 stays driven
;                   (each guard state is held 20 ms for the MCU to look)
;   'P'             pass done; the next starts at once
; An armed receiver waits about 220 ms for the MCU, then reports 0x84 (calibration:
; asks again), so an MCU that loses its place finds the next 'S'.
; Every byte sent also goes to uo_out as an event, in mcu_protocol_target.s's
; format (code 1, the byte), for the A53.
.include "msf.inc"
.equ TXP,        1
.equ HOLDP,      2
.equ LINEP,      6
.equ GPIN,       7
.equ UART_BIT,   104                ; 12 MHz / 115200
.equ CAL_EDGES,  512
.equ M_FRAMES,   5
.equ U_PACKETS,  4
.equ U_BYTES,    36                 ; SYNC, PID, 32 data bytes, CRC16
.equ STEP,       13107              ; RX_DIV for mode 3: 65536 / 5 cycles a bit
.equ GAP,        200                ; 5 byte times without a byte ends a frame
.equ EDGE_TMO,   200                ; calibration transitions come every 24 cycles
.equ U_TMO,      200                ; a packet byte every 32 cycles
.equ TRIES,      40                 ; x 5.46 ms (TMO 0xFFFF) for the MCU to start
.equ EMIT_HOLD,  50                 ; a uo_out group stays 150 cycles (12.5 us)
.equ HOLD_OUT,   80                 ; 80 x 1000 x 3 cycles = 20 ms
.equ PINS,       (1 << GPIN) | (1 << HOLDP)

start:  PIN_SET  (1 << TXP)
        OE_SET   (1 << TXP)
        LDR      R1, #UART_BIT
        TX_DIV   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | TXP)
        TX_CFG   R1
        LDR      R6, #0             ; uo_out group toggle

; ---- calibration ----
again:  LDR      R1, #'S'
        CALL     send
        LDI      R1, 0xFFFF
        TMO      R1
        LDR      R4, #TRIES
cal_w:  WAIT_EDGE #(0x10 | LINEP)   ; the line rising: the pattern starts
        JNZ      cal_go
        DECR     R4
        JNZ      cal_w
        JMP      again               ; the MCU did not start: ask again
cal_go: CNT_R    R2                 ; the first transition (+3 cycles)
        LDI      R4, CAL_EDGES
        LDI      R1, EDGE_TMO
        TMO      R1
cal_e:  WAIT_EDGE #LINEP
        JZ       cal_bad
        DECR     R4
        JNZ      cal_e
        CNT_R    R5                 ; the last (+3 cycles, as for the first)
        SUBS     R5, R5, R2
        JMP      cal_rep
cal_bad: LDR     R5, #0
cal_rep: LDR     R1, #'s'
        CALL     send
        MOV      R1, R5
        CALL     send
        SWAP     R1, R5
        CALL     send

; ---- Manchester, mode 3 (as firmware/eth10_rx.s) ----
        LDR      R0, #M_FRAMES
man:    LDI      R1, STEP
        RX_DIV   R1
        LDI      R1, 0x8320         ; CRC-32, reflected, 8 bits per CRC_W
        CRC_POLL R1
        LDI      R1, 0xEDB8
        CRC_POLH R1
        LDI      R1, 0xFFFF
        CRC_SETL R1
        CRC_SETH R1
        LDR      R1, #(CRC_REFL | 7)
        CRC_CFG  R1
        LDI      R1, (RX_EN | RX_LSB | RX_MANPLL | (8 - 1) << 8 | LINEP)
        RX_CFG   R1
        LDR      R1, #'M'
        CALL     send
        LDR      R5, #0             ; bytes after the SFD
        CALL     first
        JZ       m_none
        LDI      R1, GAP
        TMO      R1
        LDR      R4, #0x55
m_pre:  SUBS     R2, R3, R4         ; preamble bytes, then the SFD
        JNZ      m_sfd
        RX_POP   R3
        JZ       m_nosfd
        JMP      m_pre
m_sfd:  LDR      R2, #0xD5
        SUBS     R2, R3, R2
        JNZ      m_nosfd
m_data: RX_POP   R3
        JZ       m_end              ; timed out: the frame has ended
        INCR     R5
        CRC_W    R3                 ; over the frame and its FCS
        JMP      m_data
m_end:  CRC_RDL  R1                 ; the residual: 0xDEBB20E3
        LDI      R2, 0x20E3
        SUBS     R1, R1, R2
        JNZ      m_bad
        CRC_RDH  R1
        LDI      R2, 0xDEBB
        SUBS     R1, R1, R2
        JNZ      m_bad
        LDR      R4, #0x81
        JMP      m_rep
m_bad:  LDR      R4, #0x82
        JMP      m_rep
m_nosfd: LDR     R4, #0x83
        JMP      m_rep
m_none: LDR      R4, #0x84
m_rep:  LDR      R1, #0
        RX_CFG   R1
        LDR      R1, #'m'
        CALL     send
        MOV      R1, R4
        CALL     send
        MOV      R1, R5
        CALL     send
        SWAP     R1, R5
        CALL     send
        DECR     R0
        JNZ      man

; ---- NRZI, de-stuffing, resync (as firmware/usb_fs_rx.s, a fixed length) ----
        LDR      R0, #U_PACKETS
usb:    LDR      R1, #4
        RX_DIV   R1
        LDI      R1, 0xA001         ; CRC16, reflected
        CRC_POLL R1
        LDR      R1, #0
        CRC_POLH R1
        CRC_SETH R1
        ST       R1, R1             ; data RAM 0, 1: SYNC, PID (0 until they come)
        LDR      R2, #1
        ST       R2, R1
        LDI      R1, 0xFFFF
        CRC_SETL R1
        LDR      R1, #(CRC_REFL | 7)
        CRC_CFG  R1
        LDI      R1, (RX_EN | RX_RESYNC | RX_LSB | RX_DESTUFF | RX_NRZI | (8 - 1) << 8 | LINEP)
        RX_CFG   R1
        LDR      R1, #'U'
        CALL     send
        LDR      R5, #0             ; bytes
        CALL     first
        JZ       u_short
        INCR     R5
        LDR      R1, #0
        ST       R1, R3
        LDI      R1, U_TMO
        TMO      R1
        RX_POP   R3
        JZ       u_short
        INCR     R5
        LDR      R1, #1
        ST       R1, R3
        LDR      R4, #(U_BYTES - 2)
u_data: RX_POP   R3
        JZ       u_short
        INCR     R5
        CRC_W    R3                 ; over the data and the CRC16
        DECR     R4
        JNZ      u_data
        CRC_RDL  R1
        LDI      R2, 0xB001         ; the residual of the reflected USB CRC16
        SUBS     R1, R1, R2
        JNZ      u_bad
        LDR      R4, #0x81
        JMP      u_rep
u_bad:  LDR      R4, #0x82
        JMP      u_rep
u_short: LDR     R4, #0x84
u_rep:  LDR      R1, #0
        RX_CFG   R1
        LDR      R1, #'u'
        CALL     send
        MOV      R1, R4
        CALL     send
        MOV      R1, R5
        CALL     send
        LDR      R2, #0
        LD       R1, R2
        CALL     send
        LDR      R2, #1
        LD       R1, R2
        CALL     send
        DECR     R0
        JNZ      usb

; ---- the late guard ----
        PIN_CLR  PINS
        OE_SET   PINS
        LDI      R1, (0x8000 | 0x0100 | (1 << GPIN))     ; guard T0, pin UIO7
        LATE_CLR R1
        LDR      R1, #'A'
        CALL     send
        CALL     hold
        CNT_R    R3
        WAIT_CMP R3                 ; @late on purpose: UIO7 goes off the bus
        LATE_RD  R5
        LDR      R1, #'B'
        CALL     send
        MOV      R1, R5
        CALL     send
        SWAP     R1, R5
        CALL     send
        CALL     hold
        LDR      R1, #1
        LATE_CLR R1                 ; T0's flag cleared: UIO7 back
        LATE_RD  R5
        LDR      R1, #'C'
        CALL     send
        MOV      R1, R5
        CALL     send
        SWAP     R1, R5
        CALL     send
        CALL     hold
        LDI      R1, (0x8000 | 0x0200 | (1 << GPIN))     ; guard T1 only, pin UIO7
        LATE_CLR R1
        CNT_R    R3
        WAIT_CMP R3                 ; @late again (T0, not named): UIO7 stays
        LATE_RD  R5
        LDR      R1, #'D'
        CALL     send
        MOV      R1, R5
        CALL     send
        SWAP     R1, R5
        CALL     send
        CALL     hold
        LDR      R1, #1
        LATE_CLR R1
        LDI      R1, 0x8000         ; no guard
        LATE_CLR R1
        OE_CLR   PINS
        LDR      R1, #'P'
        CALL     send
        JMP      again

; The first byte of a frame or packet into R3, waiting TRIES x 5.46 ms for the MCU:
; Z = 1 if none came. Uses R1, R4.
first:  LDI      R1, 0xFFFF
        TMO      R1
        LDR      R4, #TRIES
first_w: RX_POP  R3
        JNZ      first_ok           ; Z = 0: a byte
        DECR     R4
        JNZ      first_w            ; Z = 1 when R4 reaches 0
first_ok: RET

; About 20 ms. Uses R2, R3.
hold:   LDR      R2, #HOLD_OUT
hold_o: LDI      R3, 1000
hold_i: DECR     R3
        JNZ      hold_i
        DECR     R2
        JNZ      hold_o
        RET

; R1[7:0] to the MCU on the UART (TX_PUSH waits while a byte is in the buffer),
; and to the A53 on uo_out as event 1. Uses R2, R3, R6 (the group toggle); keeps
; R0, R1, R4, R5; leaves TMO 0, so TX_PUSH never gives up.
send:   LDR      R2, #0
        TMO      R2
        ADDS     R2, R1, R1         ; start bit 0, 8 data bits, stop bit 1
        LDI      R3, 0x200
        OR       R2, R2, R3
        TX_PUSH  R2
        SHR      R2, R1, #6         ; first group: 1 code[3:0]=1 data[7:6]
        LDR      R3, #3
        AND      R2, R2, R3
        LDR      R3, #0x44
        OR       R2, R2, R3
        LDR      R3, #0x80
        XOR      R6, R6, R3
        OR       R2, R2, R6
        OUT_W    R2
        LDR      R2, #EMIT_HOLD
send_a: DECR     R2
        JNZ      send_a
        LDR      R3, #0x3F          ; second group: data[5:0]
        AND      R2, R1, R3
        LDR      R3, #0x80
        XOR      R6, R6, R3
        OR       R2, R2, R6
        OUT_W    R2
        LDR      R2, #EMIT_HOLD
send_b: DECR     R2
        JNZ      send_b
        RET
