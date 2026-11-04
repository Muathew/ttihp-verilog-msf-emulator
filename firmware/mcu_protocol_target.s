; Protocol-test peer for PMOD JA: the core is the target and an LP-MSPM0G3519
; (mcu_ext/protocol_tests) the controller. fpga/ps/mcu_test loads this and
; prints what it reports.
;   UIO0 UART RX, UIO1 UART TX
;   UIO2 I2C SCL, UIO3 I2C SDA (open drain, external pull-ups)
;   UIO4 SPI SCK, UIO5 SPI CS, UIO6 SPI MOSI, UIO7 SPI MISO
;   UART: 115200 8N1 byte echo
;   I2C : 7-bit address 0x50, 32 registers, register 0 = 0xA5; pointer write,
;         repeated START, read, clock stretching
;   SPI : mode 0, MSB first, one byte a frame: returns the next byte of
;         55 AA 12 34 DE AD BE EF and checks MOSI carries the same byte. Five
;         ms with no clock starts again from the first byte.
;
; Four threads run, so every thread gets one instruction per 4 cycles whatever
; the others do (isa.md section 7). At a 12 MHz core:
;   SPI: SCK period >= 32 cycles (375 kHz), and >= 110 cycles (10 us) from a
;        byte's last rising SCK edge to the next byte's first. The MCU uses
;        250 kHz with a gap between frames. 1 MHz can not work: 12 cycles a
;        bit is 3 instructions and the bit loop needs 8.
;   I2C: 100 kHz, with any SDA hold after SCL falls (tested from 1 cycle up).
;
; ---- what the host sees ----
; uo_out carries events, oldest first, 12 bits each in two groups:
;   bit 7   toggles with every group, so a new group is seen even if it has
;           the same value as the last
;   bit 6   1 = first group
;   bits 5:0  first group: code[3:0] data[7:6]; second group: data[5:0]
; A group stays on the pins at least 12 us. There is no way back from the host
; while the board has the pins, so the core never waits for it: a host that
; is not reading misses events, and EV_SENT lets it count how many.
;   EV_UART     byte received and echoed
;   EV_SPI_OK   MOSI byte, it matched
;   EV_SPI_BAD  MOSI byte, it did not match; EV_SPI_EXP follows with the expected
;   EV_SPI_RST  silence ended a burst early: data = bits missing << 4 | byte
;               index. The next frame is byte 0 again.
;   EV_I2C_ADR  address byte for us: ACK given
;   EV_I2C_ADX  address byte for someone else: no ACK
;   EV_I2C_PTR  pointer byte        EV_I2C_WR  data byte written
;   EV_I2C_RD   read byte sent, the controller said NACK (the last byte)
;   EV_I2C_RDA  read byte sent, the controller said ACK (wants another)
;   EV_BUS      BUS_STOP, BUS_RSTART (seen inside a byte), BUS_WATCHDOG (below),
;               BUS_BOOT | FW_VERSION once at start
;   EV_LA       logic analyser bytes, below
; About every 0.2 s, when nothing else is queued, three more:
;   EV_PINS     the pins now
;   EV_ACT      pins that moved since the last EV_ACT (checked every 3 us)
;   EV_SENT     events taken from the queue so far, modulo 256
;
; I2C watchdog: both lines not high and neither moved between two heartbeats
; means a hung transfer (a controller that ACKed its last byte and then cannot
; make a STOP past our next bit, say). T0 restarts the I2C thread, which lets
; go of the lines.
;
; Logic analyser: the capture engine records the raw pins from the first UART
; start bit (the first thing an MCU pass does) in change mode, a sample every
; LA_DIV cycles, without touching any thread's timing. Once LA_POST more words
; are in, T0 sends LA_PRE + 1 + LA_POST words as EV_LA bytes, pins then ticks
; since the last word. Once per boot.
.include "msf.inc"

.equ UART_RX, 0
.equ UART_TX, 1
.equ I2C_SCLP, 2
.equ I2C_SDAP, 3
.equ SPI_SCKP, 4
.equ SPI_CSP, 5
.equ SPI_MOSIP, 6
.equ SPI_MISOP, 7
.equ UART_BIT, 104
.equ I2C_ADDR, 0x50
.equ I2C_SCL, 1 << I2C_SCLP
.equ I2C_SDA, 1 << I2C_SDAP
.equ I2C_LINES, I2C_SCL | I2C_SDA
.equ I2C_HOLD, I2C_SCL
.equ I2C_REG_MASK, 0x1F            ; registers 0..31; RAM_SENT and the FIFO are above
.equ SPI_MISO, 1 << SPI_MISOP
.equ RAM_SENT, 32
.equ FW_VERSION, 2

; Inputs ignore pulses under 3 cycles (250 ns): bench wires ring, and one extra
; SCK or SCL edge loses a whole byte. Every input arrives 3 cycles later for it.
.equ FILTER_PINS, 0x7D             ; all but UART TX and MISO
.equ FILTER_LEN, 3

; Times, as defines so the tests need not simulate them at full length.
.equ BOOT_HOLD, 0                  ; T0 waits this * 8 cycles (0 = 65536: 44 ms)
.equ HEARTBEAT, 0                  ; idle passes (36 cycles each) a beat (0 = 65536)
.equ EMIT_HOLD, 16                 ; group time: 24 + 8 * this cycles at least
.equ SPI_QUIET, 0xFFFF             ; cycles of silence that end an SPI burst (5.5 ms)
.equ LA_DIV, 4                     ; a sample every 333 ns
.equ LA_BASE, 512                  ; the upper SRAM bank; the image ends below it
.equ LA_N, 8                       ; 2 << 8 = 512 words
.equ LA_MASK, (2 << LA_N) - 1
.equ LA_PRE, 8
.equ LA_POST, 500

; Event codes, the high byte of a queued word. fpga/ps/mcu_test/src/main.c and
; test_targets.py know these numbers.
.equ EV_SENT, 0
.equ EV_UART, 1
.equ EV_SPI_OK, 2
.equ EV_SPI_BAD, 3
.equ EV_SPI_EXP, 4
.equ EV_SPI_RST, 5
.equ EV_I2C_ADR, 6
.equ EV_I2C_ADX, 7
.equ EV_I2C_PTR, 8
.equ EV_I2C_WR, 9
.equ EV_I2C_RD, 10
.equ EV_I2C_RDA, 11
.equ EV_BUS, 12
.equ EV_LA, 13
.equ EV_PINS, 14
.equ EV_ACT, 15
.equ BUS_STOP, 0
.equ BUS_RSTART, 1
.equ BUS_WATCHDOG, 2
.equ BUS_BOOT, 0x80

; ---- T0: set everything up, then report ----
; R0 pins at the last look, R4 pins that moved, R5 passes to the next beat,
; R6 strobe, R7 link; emit uses R2 and R3.
start:
        LDR      R1, #0
        TMO      R1                ; waits are for ever unless a thread says not
                                   ; (the boot ROM leaves 0, a direct boot 0xFFFF)
        PIN_WI   0x00
        OE_WI    0x00
        LDR      R1, #(SPI_MISOP | SPI_MOSIP << 3)
        SER_CFG  R1
        LDI      R1, (FILTER_PINS | FILTER_LEN << FILT_LEN)
        FILTER   R1
        LDR      R2, #0
        LDR      R1, #0xA5
        ST       R2, R1            ; register 0
        LDR      R2, #RAM_SENT
        LDR      R1, #0
        ST       R2, R1
        LDI      R1, (LA_BASE | LA_N << CAP_N)
        CAP_BUF  R1
        LDR      R1, #LA_DIV
        CAP_DIV  R1
        LDI      R1, (1 << UART_RX)
        CAP_TRIG R1                ; UART RX low ...
        LDI      R1, (LA_POST | CAP_EDGE | CAP_CHG | CAP_ARM)
        CAP_CTL  R1                ; ... as an edge
        LDI      R1, uart_target
        TSTART   #1, R1
        LDI      R1, i2c_target
        TSTART   #2, R1
        LDI      R1, spi_target
        TSTART   #3, R1
        ; Leave the boot ROM's "starting" on uo_out long enough for the host to
        ; see it and to take the pins, before the first event.
        LDI      R5, BOOT_HOLD
t0_hold:
        DECR     R5
        JNZ      t0_hold
        UO_WI    0x00
        LDR      R6, #0
        LDI      R1, (EV_BUS << 8 | BUS_BOOT | FW_VERSION)
        CALL     emit
        LDR      R4, #0
        PIN_R    R0
        LDI      R5, HEARTBEAT     ; a whole interval before the first beat: its
                                   ; watchdog needs that long to know a line is stuck
t0_main:
        FIFO_GET R1
        JZ       t0_idle
        LDR      R3, #RAM_SENT
        LD       R2, R3
        INCR     R2
        ST       R3, R2
        CALL     emit
        JMP      t0_main
t0_idle:
        PIN_R    R1
        XOR      R2, R1, R0
        OR       R4, R4, R2
        MOV      R0, R1
        DECR     R5
        JNZ      t0_main

; A heartbeat. A finished capture goes out first: the host prints only at an
; EV_SENT with no traffic since the one before, and it is done printing well
; before the next beat, so it is never busy while the capture comes.
t0_beat:
        CAP_STAT R2
        LDI      R3, CS_DONE
        AND      R2, R2, R3
        ADDS     R2, R2, R2
        JZ       t0_beat_send
        CAP_TPOS R0                ; R0 = offset in the buffer, R5 = words left
        LDR      R2, #LA_PRE
        SUBS     R0, R0, R2
        LDI      R3, LA_MASK
        AND      R0, R0, R3
        LDI      R5, (LA_PRE + 1 + LA_POST)
t0_la_word:
        LDI      R3, LA_BASE
        OR       R3, R0, R3
        LD       R1, R3
        SWAP     R1, R1
        LDH      R1, #EV_LA        ; pins
        CALL     emit
        LDI      R3, LA_BASE
        OR       R3, R0, R3
        LD       R1, R3
        LDH      R1, #EV_LA        ; ticks
        CALL     emit
        INCR     R0
        LDI      R3, LA_MASK
        AND      R0, R0, R3
        DECR     R5
        JNZ      t0_la_word
        LDR      R1, #0
        CAP_CTL  R1                ; clears "done": this was the only dump
        PIN_R    R0
t0_beat_send:
        MOV      R1, R0
        LDH      R1, #EV_PINS
        CALL     emit
        MOV      R1, R4
        LDH      R1, #EV_ACT
        CALL     emit
        LDR      R3, #RAM_SENT
        LD       R1, R3
        LDH      R1, #EV_SENT
        CALL     emit
        LDI      R5, HEARTBEAT
        LDR      R2, #I2C_LINES
        AND      R3, R4, R2
        LDR      R4, #0
        ADDS     R3, R3, R3
        JNZ      t0_main           ; the I2C lines moved: not hung
        AND      R3, R0, R2
        SUBS     R3, R3, R2
        JZ       t0_main           ; both high: idle
        OE_CLR   I2C_LINES
        LDI      R3, i2c_target
        TSTART   #2, R3
        LDI      R1, (EV_BUS << 8 | BUS_WATCHDOG)
        CALL     emit
        JMP      t0_main

; R1 = code << 8 | data -> two groups on uo_out; keeps R1, uses R2 and R3
emit:
        SHR      R2, R1, #6
        LDR      R3, #0x3F
        AND      R2, R2, R3
        LDR      R3, #0x40
        OR       R2, R2, R3
        LDR      R3, #0x80
        XOR      R6, R6, R3
        OR       R2, R2, R6
        OUT_W    R2
        LDR      R2, #EMIT_HOLD
emit_a:
        DECR     R2
        JNZ      emit_a
        LDR      R3, #0x3F
        AND      R2, R1, R3
        LDR      R3, #0x80
        XOR      R6, R6, R3
        OR       R2, R2, R6
        OUT_W    R2
        LDR      R2, #EMIT_HOLD
emit_b:
        DECR     R2
        JNZ      emit_b
        RET

; ---- T1: UART byte echo; the RX and TX engines do the bits ----
uart_target:
        PIN_SET  (1 << UART_TX)
        OE_SET   (1 << UART_TX)
        LDR      R1, #UART_BIT
        RX_DIV   R1
        TX_DIV   R1
        LDI      R1, (RX_EN | RX_CHAR | RX_LSB | (8 - 1) << 8 | UART_RX)
        RX_CFG   R1
        LDI      R1, (TX_EN | TX_IDLE1 | TX_LSB | (10 - 1) << 8 | UART_TX)
        TX_CFG   R1
        LDI      R2, 0x200
uart_echo:
        RX_POP   R1
        MOV      R3, R1
        ADDS     R1, R1, R1
        OR       R1, R1, R2
        TX_PUSH  R1
        LDH      R3, #EV_UART
        FIFO_PUT R3
        JMP      uart_echo

; ---- T2: I2C target ----
; R0 = SCL mask, R5 = SCL | SDA mask, R3 = register pointer, R4 = bits to go,
; R1 = byte being built, R6 = both lines at this bit's sample, R2 = scratch,
; R7 = link. Not the shift register: the SPI thread owns it.
i2c_target:
        LDR      R0, #I2C_SCL
        LDR      R5, #I2C_LINES
        LDR      R3, #0
i2c_idle:
        OE_CLR   I2C_LINES
i2c_wait_start:
        WAIT_EDGE I2C_SDAP, fall
        PIN_R    R1
        AND      R1, R1, R0
        ADDS     R1, R1, R1
        JZ       i2c_wait_start
i2c_start:
        WAIT_EDGE I2C_SCLP, low
        CALL     i2c_byte          ; the address byte
        OE_SET   I2C_HOLD
        MOV      R2, R1
        LDR      R1, #(I2C_ADDR << 1)
        SUBS     R1, R2, R1
        JZ       i2c_write
        DECR     R1
        JZ       i2c_read
        LDH      R2, #EV_I2C_ADX
        FIFO_PUT R2
        JMP      i2c_idle

; One byte in, MSB first, SDA sampled while SCL is high; SCL is low on entry and
; on return. After each sample both lines are watched for a change: if the SCL
; bit of (now - sample) is set then SCL fell, whatever SDA did at the same time,
; and the bit is done; otherwise SDA moved while SCL stayed high, a START or a
; STOP, and the byte is abandoned (no return). The test is on that bit because a
; difference's sign says nothing here: SDA is the higher pin, and SDA rising as
; SCL falls gives a positive difference.
i2c_byte:
        LDR      R1, #0
        LDR      R4, #8
i2c_bit:
        WAIT_EDGE I2C_SCLP, high
        PIN_R    R6
        AND      R6, R6, R5
        SHL      R1, R1, #1
        SHR      R2, R6, #I2C_SDAP
        OR       R1, R1, R2
i2c_fall:
        PIN_R    R2
        AND      R2, R2, R5
        SUBS     R2, R2, R6
        JZ       i2c_fall
        AND      R2, R2, R0
        ADDS     R2, R2, R2
        JZ       i2c_sda_change
        DECR     R4
        JNZ      i2c_bit
        RET

; Pointer and data write transactions.
i2c_write:
        OE_SET   I2C_SDA
        LDH      R2, #EV_I2C_ADR
        FIFO_PUT R2
        OE_CLR   I2C_HOLD
        WAIT_EDGE I2C_SCLP, high
        WAIT_EDGE I2C_SCLP, low
        OE_CLR   I2C_SDA
        CALL     i2c_byte
        OE_SET   I2C_LINES
        MOV      R3, R1
        LDH      R1, #EV_I2C_PTR
        FIFO_PUT R1
        LDR      R1, #I2C_REG_MASK
        AND      R3, R3, R1
        OE_CLR   I2C_HOLD
i2c_write_ack:
        WAIT_EDGE I2C_SCLP, high
        WAIT_EDGE I2C_SCLP, low
        OE_CLR   I2C_SDA
        CALL     i2c_byte
        OE_SET   I2C_LINES
        MOV      R2, R1
        ST       R3, R2
        LDH      R2, #EV_I2C_WR
        FIFO_PUT R2
        INCR     R3
        LDR      R1, #I2C_REG_MASK
        AND      R3, R3, R1
        OE_CLR   I2C_HOLD
        JMP      i2c_write_ack

; Register read.  A repeated START after the pointer byte reads from the pointer.
i2c_read:
        OE_SET   I2C_SDA
        LDH      R2, #EV_I2C_ADR
        FIFO_PUT R2
        LD       R2, R3
        SWAP     R2, R2
        OE_CLR   I2C_HOLD
        WAIT_EDGE I2C_SCLP, high
        WAIT_EDGE I2C_SCLP, low
i2c_tx_byte:
        LDR      R4, #8
i2c_tx_bit:
        MOV      R1, R2
        INCR     R1
        JN       i2c_tx_one
        OE_SET   I2C_SDA
        JMP      i2c_tx_clock
i2c_tx_one:
        OE_CLR   I2C_SDA
        NOP
i2c_tx_clock:
        OE_CLR   I2C_HOLD          ; held only before a byte after the controller's ACK
        SHL      R2, R2, #1
        WAIT_EDGE I2C_SCLP, high
        WAIT_EDGE I2C_SCLP, low
        DECR     R4
        JNZ      i2c_tx_bit
        OE_CLR   I2C_SDA           ; the controller's ACK bit
        WAIT_EDGE I2C_SCLP, high
        PIN_R    R6                ; SDA low = ACK
        WAIT_EDGE I2C_SCLP, low
        OE_SET   I2C_HOLD
        LD       R1, R3            ; the byte just sent
        INCR     R3
        LDR      R2, #I2C_REG_MASK
        AND      R3, R3, R2
        LDR      R2, #I2C_SDA
        AND      R6, R6, R2
        ADDS     R6, R6, R6
        JZ       i2c_tx_more
        LDH      R1, #EV_I2C_RD
        FIFO_PUT R1
        JMP      i2c_idle
i2c_tx_more:
        LDH      R1, #EV_I2C_RDA
        FIFO_PUT R1
        LD       R2, R3
        SWAP     R2, R2
        JMP      i2c_tx_byte

; SDA moved while SCL stayed high.
i2c_sda_change:
        SHR      R1, R6, #I2C_SDAP ; SDA before the change
        ADDS     R1, R1, R1
        JNZ      i2c_restart
        LDI      R1, (EV_BUS << 8 | BUS_STOP)
        FIFO_PUT R1
        JMP      i2c_idle
i2c_restart:
        LDI      R1, (EV_BUS << 8 | BUS_RSTART)
        FIFO_PUT R1
        JMP      i2c_start

; ---- T3: SPI target ----
; R0 = 0 (never written), R2 = this byte, R3 = MOSI byte, R4 = bits to go,
; R5 = byte index, R6 = table, R7 = the byte's address, R1 = scratch.
; Waits for SCK low and then high for every bit, so any SCK slower than the
; limit works. MISO is loaded before the wait for CS, and stays driven between
; frames: this is the only device on the bus.
spi_target:
        LDI      R1, SPI_QUIET
        TMO      R1                ; a wait that times out means silence
        LDI      R6, spi_bytes
        LDR      R5, #0
        OE_SET   SPI_MISO
spi_load:
        ADDS     R7, R6, R5
        LD       R2, R7
        SWAP     R1, R2
        SR_LD    R1
        SHIFT_OUT R0               ; bit 7 on MISO before the first clock
        LDR      R4, #8
spi_select:
        WAIT_EDGE SPI_CSP, low
        JZ       spi_silence
spi_bit:
        WAIT_EDGE SPI_SCKP, low
        WAIT_EDGE SPI_SCKP, high
        JZ       spi_silence
        XFER                       ; MOSI in, next bit on MISO
        DECR     R4
        JNZ      spi_bit
        SR_RD    R1
        LDR      R3, #0xFF
        AND      R3, R1, R3        ; the MOSI byte
        INCR     R5                ; the next byte's first bit goes out first:
        LDR      R1, #7            ; it is what limits the gap between frames
        AND      R5, R5, R1
        ADDS     R7, R6, R5
        LD       R1, R7
        SWAP     R1, R1
        SR_LD    R1
        SHIFT_OUT R0
        SUBS     R1, R3, R2
        JZ       spi_ok
        LDH      R3, #EV_SPI_BAD
        FIFO_PUT R3
        LDH      R2, #EV_SPI_EXP
        FIFO_PUT R2
        JMP      spi_next
spi_ok:
        LDH      R3, #EV_SPI_OK
        FIFO_PUT R3
spi_next:
        LD       R2, R7
        LDR      R4, #8
        JMP      spi_select

; Silence on CS or SCK. At a byte boundary of byte 0 there is nothing to say;
; otherwise the burst stopped short: report it and line up on byte 0 again, so
; one bad burst does not spoil the next.
spi_silence:
        SHL      R1, R4, #4
        OR       R1, R1, R5
        LDR      R3, #0x80
        SUBS     R3, R1, R3
        JZ       spi_select
        LDH      R1, #EV_SPI_RST
        FIFO_PUT R1
        LDR      R5, #0
        JMP      spi_load

spi_bytes:
        .word 0x0055, 0x00AA, 0x0012, 0x0034
        .word 0x00DE, 0x00AD, 0x00BE, 0x00EF
