; UART -> SPI bridge across the two threads (a serial-to-SPI adapter).
; Thread 0 receives UART bytes (RX engine) and pushes them into the FIFO;
; thread 1 takes them off the queue and runs SPI transfers as the
; controller: a byte n announces a transfer of n bytes (0 is ignored). CS
; goes low, each byte is shifted out on MOSI as soon as it arrives while
; MISO is shifted in, each byte read back is sent on the UART TX, and CS
; goes high after the n-th byte. The FIFO is the only link between the
; threads: neither waits for the other except for data.
;   pins : UART RX uio[1], TX uio[0]; SPI SCK uio[2], MOSI uio[3],
;          MISO uio[4] (input), CS uio[5] (active low)
;   BIT  : UART bit time, cycles (define; 104 = 115200 at 12 MHz)
;   MODE : SPI mode 0..3 (define)
;   SH, SL: SCK high / low delay counts (defines, >= 1): SCK is high for
;          4 + 4 SH cycles and low for 10 + 4 SL (mode 0; thread 1 runs at
;          half rate beside thread 0). Defaults 1, 1: 22 cycles a bit. They
;          set the clock exactly, also out of a device's spec on purpose
.include "msf.inc"
.equ BIT,   104
.equ MODE,  0
.equ SH,    1
.equ SL,    1
.equ CPOL,  MODE >> 1
.equ CPHA,  MODE & 1
.equ RXP,   1
.equ TXP,   0
.equ SCK,   0x04
.equ MOSI,  0x08
.equ CS,    0x20

; ---- thread 0: UART bytes -> FIFO (with the stop bit in bit 8)
start:  LDI      R1, BIT
        RX_DIV   R1
        LDI      R1, (RX_EN | RX_CHAR | RX_LSB | (9 - 1) << 8 | RXP)
        RX_CFG   R1                ; 8 data bits + the stop bit
        LDR      R1, #0
        TMO      R1                ; no timeouts; T1 inherits this at TSTART
        LDI      R1, 64 | 5 << FIFO_N
        FIFO_CFG R1                ; FIFO: SRAM 64..127, empty
        LDI      R1, spi
        TSTART   R1
rx:     RX_POP   R1
        FIFO_PUT R1
        JMP      rx

; ---- thread 1: FIFO -> SPI transfers, MISO bytes -> UART TX
; R3 = bytes left in the transfer, R0 = UART stop bit
spi:    LDR      R1, #(CS | CPOL << 2)
        PIN_W    R1                ; CS high, SCK at its idle level
        OE_SET   (SCK | MOSI | CS | 1 << TXP)
        LDI      R1, BIT
        TX_DIV   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | TXP)
        TX_CFG   R1
        LDR      R1, #(3 | 4 << 3) ; SR out = MOSI, SR in = MISO, MSB first
        SER_CFG  R1
        LDI      R0, 0x200         ; UART stop bit
len:    FIFO_GET R1
        JZ       len
        LDR      R4, #0xFF
        AND      R3, R1, R4        ; n
        ADDS     R3, R3, R3
        JZ       len
        AND      R3, R1, R4
        PIN_CLR  CS
byte:   FIFO_GET R1
        JZ       byte              ; (wait for the UART)
        SWAP     R1, R1            ; byte -> SR[15:8] (MSB first)
        SR_LD    R1
        LDR      R4, #8
        LDR      R5, #CPHA
        ADDS     R5, R5, R5
        JNZ      cpha1
; one bit: MOSI changes and MISO is sampled (XFER) at the start of the
; phase before the sampling edge, the SCK phases last SH / SL counts
cpha0:  XFER                       ; MOSI <- bit, MISO sampled
        LDR      R2, #SL
c0l:    DECR     R2
        JNZ      c0l
        PIN_TGL  SCK               ; leading edge: the device samples MOSI
        LDR      R2, #SH
c0h:    DECR     R2
        JNZ      c0h
        PIN_TGL  SCK               ; trailing edge: the device shifts MISO
        DECR     R4
        JNZ      cpha0
        JMP      done
cpha1:  PIN_TGL  SCK               ; leading edge: the device shifts MISO
        LDR      R2, #SH
c1h:    DECR     R2
        JNZ      c1h
        XFER                       ; MOSI <- bit, MISO sampled
        PIN_TGL  SCK               ; trailing edge: the device samples MOSI
        LDR      R2, #SL
c1l:    DECR     R2
        JNZ      c1l
        DECR     R4
        JNZ      cpha1
done:   SR_RD    R5                ; the byte read, in SR[7:0]
        LDR      R4, #0xFF
        AND      R5, R5, R4
        ADDS     R5, R5, R5
        OR       R5, R5, R0        ; 8N1 frame: start bit 0, stop bit
tx:     TX_PUSH  R5
        JZ       tx
        DECR     R3
        JNZ      byte
        NOP
        PIN_SET  CS
        JMP      len
