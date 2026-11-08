; nRF24L01+ radio as a transmitter: configure it over SPI, load a payload, pulse CE to
; send it, poll STATUS until the packet is acknowledged (or the retries run out),
; clear the flags.
;   pins  : CE uio[0], SCK uio[2], MOSI uio[3], MISO uio[4] (input), CSN uio[5]
;   table : script, per entry n (SPI bytes, then n bytes), or WAIT | pieces of 6,000
;           cycles, CE_PULSE, POLL; 0xFFFF = the end
;   output: data RAM [64] the STATUS the poll ended on; uo_out = DONE (TX_DS) or BAD
; Sources: Nordic nRF24L01+ product specification v1.0: SPI mode 0, MSB first, a
; command byte per transaction (CSN low to high), STATUS clocked out on MISO with it;
; W_REGISTER 0x20 | reg, W_TX_PAYLOAD 0xA0, FLUSH_TX 0xE1, NOP 0xFF; CONFIG (0x00)
; PWR_UP, EN_CRC, CRCO; EN_AA (0x01), SETUP_RETR (0x04), RF_CH (0x05), RF_SETUP
; (0x06), STATUS (0x07, TX_DS bit 5, MAX_RT bit 4, written 1 to clear), RX_ADDR_P0
; (0x0A) and TX_ADDR (0x10), 5 bytes LSB first; 1.5 ms from power-up to standby
; (Tpd2stby); a CE pulse of 10 us or more sends the payload.
.include "msf.inc"
.equ CE,   0x01
.equ SCK,  0x04
.equ CSN,  0x20
.equ WAIT, 0x8000
.equ CE_PULSE, 0x4000
.equ POLL, 0x2000
.equ END,  0xFFFF
.equ BAD,  0xEE

start:  PIN_WI   CSN                ; CSN high, CE and SCK low
        OE_SET   CE | SCK | 0x08 | CSN
        LDR      R1, #(3 | 4 << 3)  ; SR out = MOSI (3), in = MISO (4), MSB first
        SER_CFG  R1
        LDI      R6, script
next:   LD       R3, R6
        INCR     R6
        INCR     R3
        JZ       fin
        DECR     R3
        LDR      R1, #0             ; WAIT is bit 15: N
        SUBS     R1, R3, R1
        JN       wait
        LDI      R1, CE_PULSE
        SUBS     R1, R3, R1
        JZ       pulse
        LDI      R1, POLL
        SUBS     R1, R3, R1
        JZ       poll
        PIN_CLR  CSN                ; a transaction of R3 bytes
xb:     LD       R1, R6
        INCR     R6
        CALL     xfer
        DECR     R3
        JNZ      xb
        PIN_SET  CSN
        JMP      next
wait:   LDR      R1, #0xFF
        AND      R2, R3, R1
        CNT_R    R5
        LDI      R1, 6000
w_l:    ADDS     R5, R5, R1
        WAIT_CMP R5
        DECR     R2
        JNZ      w_l
        JMP      next
pulse:  CNT_R    R5                 ; CE high 15 us
        PIN_SET  CE
        LDR      R1, #180
        ADDS     R5, R5, R1
        WAIT_CMP R5
        PIN_CLR  CE
        JMP      next
poll:   LDR      R4, #0             ; NOP until TX_DS or MAX_RT, at most 250 times
pl:     PIN_CLR  CSN
        LDR      R1, #0xFF
        CALL     xfer               ; R1: STATUS
        PIN_SET  CSN
        LDR      R2, #64
        ST       R2, R1
        LDR      R2, #0x30
        AND      R2, R1, R2
        ADDS     R2, R2, R2
        JNZ      next
        INCR     R4
        LDR      R2, #250
        SUBS     R2, R4, R2
        JNZ      pl
        JMP      bad
fin:    LDR      R1, #64            ; TX_DS?
        LD       R1, R1
        LDR      R2, #0x20
        AND      R1, R1, R2
        ADDS     R1, R1, R1
        JZ       bad
        UO_WI    DONE
        JMP      halt
bad:    UO_WI    BAD
halt:   JMP      halt

; one byte, mode 0: R1 out on MOSI, the byte from MISO back in R1
xfer:   SWAP     R1, R1
        SR_LD    R1
        LDR      R2, #8
x_b:    XFER                        ; MOSI <- bit; MISO (set at the last falling edge) in
        PIN_SET  SCK
        NOP
        PIN_CLR  SCK                ; the radio moves MISO on (and the input
        NOP                         ;  synchroniser takes two cycles)
        NOP
        DECR     R2
        JNZ      x_b
        SR_RD    R1
        LDR      R2, #0xFF
        AND      R1, R1, R2
        RET

; power up (CRC 2 bytes), auto-ack on pipe 0, retries (0x3F: 1 ms apart, 15 of them),
; channel 76, 1 Mbit/s 0 dBm, both addresses E7E7E7E7E7, 1.5 ms (Tpd2stby),
; flush, a 4-byte payload, CE, poll, then clear TX_DS and MAX_RT
script: .word    2, 0x20, 0x0E
        .word    2, 0x21, 0x01
        .word    2, 0x24, 0x3F
        .word    2, 0x25, 76
        .word    2, 0x26, 0x06
        .word    6, 0x30, 0xE7, 0xE7, 0xE7, 0xE7, 0xE7
        .word    6, 0x2A, 0xE7, 0xE7, 0xE7, 0xE7, 0xE7
        .word    WAIT | 3
        .word    1, 0xE1
        .word    5, 0xA0, 0x4D, 0x53, 0x46, 0x33
        .word    CE_PULSE
        .word    POLL
        .word    2, 0x27, 0x30
        .word    END
