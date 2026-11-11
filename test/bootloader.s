; msfcore boot ROM v2 (IMEM words 0..127) -- assembled into
; src/tt_um_msf_bootrom_data.v by `python test/assembler.py`.
;
; Reset with ui_in[0] = 1 : jump straight to firmware at 128 (same cycle as v1).
; Reset with ui_in[0] = 0 : boot loader; ui_in[1] picks the byte source
;     ui_in[1] = 0 : UART on uio[1] (RX engine), 115200 8N1 at a 12 MHz clock, or, with
;                    ui_in[3] = 1 (v4 autobaud, plan-v4 B6), at any clock: the host sends
;                    0xFF first, whose start bit is one bit long; the ROM times it with CNT
;                    and sets both dividers (bit time = cycles of the low pulse), then
;                    the frames follow back to back. With ui_in[3] = 0: 104 cycles a bit.
;     ui_in[1] = 1 : parallel -- byte on ui_in[7:0], strobe on uio[1],
;                    two-phase level handshake: byte k is valid when the
;                    strobe level == k mod 2 (first byte: strobe high); the
;                    chip acknowledges by copying that level to uo_out[7].
;                    Set the data at least one clock before the strobe.
;
; Frame  : A5 cmd addrL addrH lenL lenH (wordL wordH)*len crc8
;          crc8 = CRC-8 (poly 0x07, init 0) over cmd .. last payload byte
;          cmd 'W' writes len words at addr (0-127 data RAM, 128-1023 code)
;          cmd 'G' (len 0) jumps to addr after the ACK
; Reply  : ACK 0x06 / NAK 0x15 on UART TX = uio[0] (115200 8N1)
; uo_out : bit0 waiting for a frame, bit1 last frame OK, bit2 last frame
;          rejected (CRC), bit3 starting firmware, bit7 parallel byte ack
;
; CRC check by residue: every byte (including the received crc8) goes
; through the CRC unit; a CRC with init 0 and no final xor leaves 0.
;
; Registers: R0 word count, R1 byte, R2/R5 scratch, R3 cmd, R4 address,
;            R6 byte-routine address, R7 return address (CALL).

.equ TXCFG, 0x3980          ; uio0, NRZ, LSB-first, 10-bit words, idle 1, on
.equ RXCFG, 0x3781          ; uio1, NRZ, LSB-first, 8-bit, async char, on
.equ ACKW,  0x020C          ; start 0 | 0x06 << 1 | stop 1 << 9
.equ NAKW,  0x022A          ; start 0 | 0x15 << 1 | stop 1 << 9

; ---- boot select (identical to v1: direct boot reaches 128 at cycle 8) ----
        IN_RD    R1
        LDR      R2, #1
        AND      R3, R1, R2
        ADDS     R3, R3, R0         ; Z=1 iff ui_in[0] == 0 (R0 = 0 after reset)
        JZ       loader
        JMP      128

loader:
        LDR      R2, #0
        TMO      R2                 ; blocking ops never time out here
        LDR      R2, #104           ; 12 MHz / 115200
        TX_DIV   R2
        RX_DIV   R2
        LDI      R2, TXCFG
        TX_CFG   R2
        OE_SET   1
        LDR      R6, #par_get1
        LDR      R2, #2
        AND      R3, R1, R2
        SUBS     R3, R3, R2         ; Z=1 iff ui_in[1] == 1
        JZ       frame
        LDR      R6, #uart_get
        LDR      R2, #8
        AND      R2, R1, R2         ; ui_in[3]: autobaud
        ADDS     R2, R2, R0         ; (AND leaves Z alone; R0 = 0): Z=1 iff the strap is low
        JZ       noab
        UO_SET   0x01               ; waiting: the host may send its 0xFF now
        WAIT_EDGE 1, fall           ; start bit of 0xFF: one bit of low, then 8 ones and the stop bit
        CNT_R    R2
        WAIT_EDGE 1, rise
        CNT_R    R5
        SUBS     R5, R5, R2         ; the bit time in cycles (both edges see the same latency)
        TX_DIV   R5
        RX_DIV   R5
noab:   LDI      R2, RXCFG
        RX_CFG   R2                 ; the receiver starts at the next falling edge, so the
                                    ; rest of the 0xFF byte (all ones) goes by unseen

frame:
        UO_SET   0x01               ; waiting
        CALL     getb
        LDR      R2, #0xA5
        SUBS     R2, R1, R2
        JNZ      frame              ; hunt for the sync byte
        UO_CLR   0x07
        CRC_RDL  R2                 ; let the sync byte's CRC finish ...
        LDR      R2, #0             ; ... then reset CRC-8/0x07, init 0
        CRC_SETL R2
        CRC_SETH R2
        CRC_POLL R2
        LDH      R2, #0x07          ; R2 = 0x0700 (left-aligned poly)
        CRC_POLH R2
        LDR      R2, #7
        CRC_CFG  R2                 ; normal, 8 bits per CRC_W

        CALL     getb               ; cmd
        MOV      R3, R1
        CALL     getb               ; addr
        MOV      R4, R1
        CALL     getb
        SWAP     R1, R1
        OR       R4, R4, R1
        CALL     getb               ; len
        MOV      R0, R1
        CALL     getb
        SWAP     R1, R1
        OR       R0, R0, R1
        MEM_A    R4
        INCR     R0
wloop:
        DECR     R0
        JZ       check
        CALL     getb               ; word, little endian
        MOV      R5, R1
        CALL     getb
        SWAP     R1, R1
        OR       R5, R5, R1
        MEM_W    R5
        JMP      wloop

check:
        CALL     getb               ; crc8 (also fed: residue must be 0)
        CRC_RDL  R2                 ; wait for the CRC unit
        CRC_RDH  R2
        INCR     R2
        DECR     R2                 ; Z=1 iff residue == 0
        JNZ      bad
        LDI      R2, ACKW
        TX_PUSH  R2
        UO_SET   0x02
        LDR      R2, #0x47          ; 'G'
        SUBS     R2, R3, R2
        JNZ      frame
        TX_WAIT                     ; ACK fully on the wire
        UO_SET   0x08
        LDR      R2, #0
        RX_CFG   R2                 ; give the pins back to the firmware:
        OE_CLR   1                  ; TX released while the engine holds it high,
        TX_CFG   R2                 ; then stopped (the other way round, uio[0]
        JR       R4                 ; drove its OUT bit, 0, for one cycle)

bad:
        LDI      R2, NAKW
        TX_PUSH  R2
        UO_SET   0x04
        JMP      frame

; ---- byte sources: return the byte in R1 and feed it to the CRC ----
getb:
        JR       R6
uart_get:
        RX_POP   R1
        CRC_W    R1
        RET
par_get1:                           ; even bytes: strobe high
        WAIT_EDGE 1, high
        IN_RD    R1
        UO_SET   0x80               ; ack = 1
        LDR      R6, #par_get0
        CRC_W    R1
        RET
par_get0:                           ; odd bytes: strobe low
        WAIT_EDGE 1, low
        IN_RD    R1
        UO_CLR   0x80               ; ack = 0
        LDR      R6, #par_get1
        CRC_W    R1
        RET
