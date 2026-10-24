; SPI flash: the chip answers an SPI controller as a small 25xx-style serial
; NOR flash, so a host (a microcontroller booting from flash, a flash
; programmer) can be run and watched against it. There is no SPI hardware:
; the shift register, level waits and exact instruction timing do it.
;   pins    : SCK uio[2], MOSI uio[3], CS uio[5] inputs (CS active low);
;             MISO uio[4], driven only while CS is low
;   storage : 512 bytes, one byte a word in SRAM 512-1023, mirrored over the
;             whole 24-bit address space (address bits 23..9 are ignored)
;   MODE    : SPI mode 0..3 (assembler define, default 0)
;   ID0-2   : the JEDEC ID (defines; default EF 40 09: the capacity code 09
;             says 2^9 bytes)
; Commands (MSB first, as on any 25xx part):
;   9F, 9E  JEDEC ID: ID0 ID1 ID2, repeated while CS stays low
;   05      read status: bit 1 WEL, bit 0 WIP; repeated, always current
;   06, 04  write enable / disable (WEL)
;   03      READ a2 a1 a0, then data from a on; the address wraps at 512
;   0B      FAST_READ: the same after one dummy byte
;   02      page program a2 a1 a0 d..: with WEL set, each byte is ANDed into
;           the store (programming only clears bits) as it arrives; the
;           address wraps inside its 256-byte page. CS high clears WEL.
;   20, 52, D8 (sector, 32K, 64K block erase, with an address), 60, C7
;           (chip erase): with WEL set, at CS high every byte becomes FF
;           (any of these covers the 512 bytes). WIP is 1 until it is done:
;           the erase runs while CS is high and pauses while it is low, so a
;           host polling 05 sees WIP fall as from a real part. Then WEL
;           clears. Other commands are not refused while WIP is 1.
;   others  ignored until CS goes high
; Limits (core clock cycles; test_targets.py measures them): SCK period >=
; 28 (430 kHz at 12 MHz), CS low -> first SCK edge >= 16 (>= 32 while an
; erase is running), CS high >= 64.
.include "msf.inc"
.equ MODE,   0
.equ S,      ((MODE >> 1) ^ MODE ^ 1) & 1  ; SCK level of the sampling edge
.equ SCKP,   2
.equ MOSIP,  3
.equ MISOP,  4
.equ CSP,    5
.equ MISO,   1 << MISOP
.equ CS,     1 << CSP
.equ ID0,    0xEF
.equ ID1,    0x40
.equ ID2,    0x09
.equ STATUS, 64                    ; data RAM: the status register
.equ ERP,    65                    ; data RAM: the next word to erase, 0 = none
.equ PPW,    66                    ; data RAM: nonzero once a page program wrote
.equ ERQ,    67                    ; data RAM: an erase was asked for (WEL set)
.equ FLASH,  0x200                 ; the store: SRAM 512..1023
.equ WEL,    0x02
.equ WIP,    0x01
.equ POLL,   24                    ; SCK idle this long -> look at CS. Longer than
                                   ; half the fastest SCK, so no wait inside a
                                   ; byte times out: a timeout's CS check (7
                                   ; cycles) on the READ path would miss an edge
.equ WNS,    (5 - S) << 4 | SCKP   ; WAIT_EDGE: SCK at the other level
.equ WS,     (4 + S) << 4 | SCKP   ; WAIT_EDGE: SCK at the sampling level

; R0 = FLASH, R6 = CS mask; R5 = the store address (FLASH | 9 bits); R3 =
; the next byte to send (in bits 15..8); R1 = the byte received; R2 scratch;
; R4 = bit count; R7 = return address
start:  PIN_WI   0x00
        OE_WI    0x00              ; all inputs, MISO released
        LDR      R1, #(MISOP | MOSIP << 3)
        SER_CFG  R1                ; SR out = MISO, SR in = MOSI, MSB first
        LDI      R0, FLASH
        LDR      R6, #CS
        LDR      R2, #0
        LDR      R1, #STATUS
        ST       R1, R2
        LDR      R1, #ERP
        ST       R1, R2
        LDR      R1, #PPW
        ST       R1, R2
        LDR      R1, #ERQ
        ST       R1, R2

idle:   OE_CLR   MISO
        LDR      R4, #0
        SR_LD    R4                ; zeros on MISO during the command byte
        SHIFT_OUT R4
        LDR      R1, #ERP
        LD       R2, R1
        INCR     R2
        DECR     R2
        JNZ      erase             ; an erase to go on with
        TMO      R4                ; wait for CS without a timeout
        WAIT_EDGE CSP, low
sel:    OE_SET   MISO
        LDR      R1, #POLL
        TMO      R1                ; from now on SCK waits time out to check CS

; ---- the command byte: bits 1-7, the reply guess, bit 8 ----
        LDR      R4, #7
cbn:    WAIT_EDGE WNS
        JZ       cbnt
cbs:    WAIT_EDGE WS
        JZ       cbst
        XFER
        DECR     R4
        JNZ      cbn
        SR_RD    R1                ; R1[6:0] = command bits 7..1
        LDR      R2, #0x7F
        AND      R1, R1, R2
        LDR      R2, #(0x05 >> 1)  ; 05 (or 04): status; else (9F, 9E) the ID
        SUBS     R2, R1, R2
        JZ       g_sr
        LDI      R3, ID0 << 8
        JMP      g_ld
g_sr:   LDR      R2, #STATUS
        LD       R3, R2
        SWAP     R3, R3
g_ld:   OR       R3, R3, R1        ; {reply, 0, command bits 7..1}: the next
        SR_LD    R3                ; XFER sends the reply's bit 7 with bit 8
c8n:    WAIT_EDGE WNS
        JZ       c8nt
c8s:    WAIT_EDGE WS
        JZ       c8st
        XFER
        SR_RD    R1
        LDR      R2, #0xFF
        AND      R1, R1, R2        ; the command
        LDR      R2, #0x0F
        AND      R2, R1, R2
        LDI      R3, cmdtab
        ADDS     R2, R2, R3
        LD       R2, R2
        JR       R2                ; by the low nibble; each entry checks the rest

; ---- the commands (R1 = the command; the reply's bit 7 is on MISO) ----
c_5:    LDR      R2, #0x05
        SUBS     R2, R1, R2
        JNZ      c_ign
sr_l:   LDR      R2, #STATUS       ; read status, again and again
        LD       R3, R2
        SWAP     R3, R3
        CALL     xbyte
        SR_LD    R3
        SHIFT_OUT R4
        JMP      sr_l

c_e:    LDR      R2, #0x9E
        JMP      c_idc
c_f:    LDR      R2, #0x9F
c_idc:  SUBS     R2, R1, R2
        JNZ      c_ign
id_l:   LDI      R3, ID1 << 8      ; JEDEC ID: ID0 is going out
        CALL     xbyte
        SR_LD    R3
        SHIFT_OUT R4
        LDI      R3, ID2 << 8
        CALL     xbyte
        SR_LD    R3
        SHIFT_OUT R4
        LDI      R3, ID0 << 8
        CALL     xbyte
        SR_LD    R3
        SHIFT_OUT R4
        JMP      id_l

c_3:    LDR      R2, #0x03
        SUBS     R2, R1, R2
        JNZ      c_ign
        CALL     addr
        LD       R3, R5
        SWAP     R3, R3
rd_l:   SR_LD    R3                ; this byte: bit 7 out now
        SHIFT_OUT R4
        INCR     R5
        OR       R5, R5, R0        ; the next address, wrapping at 512
        LD       R3, R5
        SWAP     R3, R3
        CALL     xbyte
        JMP      rd_l

c_b:    LDR      R2, #0x0B
        SUBS     R2, R1, R2
        JNZ      c_ign
        CALL     addr
        LD       R3, R5
        SWAP     R3, R3
        CALL     xbyte             ; the dummy byte
        JMP      rd_l

c_2:    LDR      R2, #0x02
        SUBS     R2, R1, R2
        JZ       pp
        LDR      R2, #0x52         ; 32K block erase
        SUBS     R2, R1, R2
        JZ       c_er
        JMP      c_ign
pp:     CALL     addr
        LDR      R2, #STATUS
        LD       R2, R2
        LDR      R3, #WEL
        AND      R2, R2, R3
        ADDS     R2, R2, R2
        JZ       c_ign             ; not write-enabled: ignored
        LDR      R2, #PPW
        ST       R2, R2            ; (nonzero): CS high will clear WEL
pp_l:   CALL     xbyte
        LD       R2, R5
        AND      R2, R2, R1        ; programming only clears bits
        ST       R5, R2
        INCR     R5
        LDR      R2, #0xFF
        AND      R2, R5, R2
        ADDS     R2, R2, R2
        JNZ      pp_l
        LDI      R2, 0x100         ; wrap inside the 256-byte page
        SUBS     R5, R5, R2
        JMP      pp_l

c_6:    LDR      R2, #0x06
        SUBS     R2, R1, R2
        JNZ      c_ign
        LDR      R2, #STATUS
        LD       R3, R2
        LDR      R1, #WEL
        OR       R3, R3, R1
        ST       R2, R3
        JMP      c_ign

c_4:    LDR      R2, #0x04
        SUBS     R2, R1, R2
        JNZ      c_ign
        LDR      R2, #STATUS
        LD       R3, R2
        LDR      R1, #(0xFF ^ WEL)
        AND      R3, R3, R1
        ST       R2, R3
        JMP      c_ign

c_0:    LDR      R2, #0x20         ; sector erase
        SUBS     R2, R1, R2
        JZ       c_er
        LDR      R2, #0x60         ; chip erase
        SUBS     R2, R1, R2
        JZ       c_er
        JMP      c_ign
c_8:    LDR      R2, #0xD8         ; 64K block erase
        SUBS     R2, R1, R2
        JZ       c_er
        JMP      c_ign
c_7:    LDR      R2, #0xC7         ; chip erase
        SUBS     R2, R1, R2
        JNZ      c_ign
c_er:   LDR      R2, #STATUS
        LD       R2, R2
        LDR      R3, #WEL
        AND      R2, R2, R3
        LDR      R3, #ERQ
        ST       R3, R2            ; nonzero if write-enabled
c_ign:  LDR      R1, #0            ; the rest of the transfer does not matter
        TMO      R1
        WAIT_EDGE CSP, high

; ---- CS high: finish what the command started, then wait for the next ----
endcs:  OE_CLR   MISO
        LDR      R1, #PPW
        LD       R2, R1
        INCR     R2
        DECR     R2
        JZ       ec_er
        LDR      R2, #0
        ST       R1, R2
        LDR      R1, #STATUS       ; a page program ended: WEL off
        LD       R3, R1
        LDR      R2, #(0xFF ^ WEL)
        AND      R3, R3, R2
        ST       R1, R3
ec_er:  LDR      R1, #ERQ
        LD       R2, R1
        INCR     R2
        DECR     R2
        JZ       idle
        LDR      R2, #0
        ST       R1, R2
        LDR      R1, #STATUS
        LDR      R2, #(WEL | WIP)  ; busy until the erase is done
        ST       R1, R2
        LDI      R2, FLASH + 511
        LDR      R1, #ERP
        ST       R1, R2            ; erase from the top down
        JMP      idle

; ---- the erase, a word at a time while CS is high (R2 = the next word) ----
erase:  LDR      R3, #0xFF
er:     ST       R2, R3
        DECR     R2
        AND      R1, R2, R0
        ADDS     R1, R1, R1        ; Z: below the store, all done
        JZ       er_done
        PIN_R    R1
        AND      R1, R1, R6
        ADDS     R1, R1, R1        ; Z: CS low, a command is coming
        JNZ      er
        LDR      R1, #ERP
        ST       R1, R2            ; go on from here at the next CS high
        LDR      R4, #0
        SR_LD    R4
        SHIFT_OUT R4
        JMP      sel
er_done:
        LDR      R2, #0
        LDR      R1, #ERP
        ST       R1, R2
        LDR      R1, #STATUS       ; WIP and WEL off
        ST       R1, R2
        JMP      idle

; ---- R5 = FLASH | the low 9 bits of the 3-byte address ----
addr:   MOV      R3, R7
        CALL     xbyte             ; a23..a16: not used
        CALL     xbyte             ; a15..a8: bit 8
        LDR      R2, #1
        AND      R5, R1, R2
        SWAP     R5, R5
        OR       R5, R5, R0
        CALL     xbyte             ; a7..a0
        OR       R5, R5, R1
        MOV      R7, R3
        RET

; ---- one byte. In: its bit 7 on MISO, its bits 6..0 in SR[15:9]. Out:
; R1 = the byte received, R4 = 0. CS high on the way ends the transfer. ----
xbyte:  LDR      R4, #7
xbn:    WAIT_EDGE WNS
        JZ       xbnt
xbs:    WAIT_EDGE WS
        JZ       xbst
        XFER
        DECR     R4
        JNZ      xbn
xb8n:   WAIT_EDGE WNS
        JZ       xb8nt
xb8s:   WAIT_EDGE WS
        JZ       xb8st
        SHIFT_IN                   ; bit 8 in, MISO unchanged
        SR_RD    R1
        LDR      R2, #0xFF
        AND      R1, R1, R2
        RET

; ---- a wait timed out (SCK idle POLL cycles): still selected -> wait
; again (level waits lose nothing), deselected -> end the transfer ----
cbnt:   PIN_R    R1
        AND      R1, R1, R6
        ADDS     R1, R1, R1
        JZ       cbn
        JMP      endcs
cbst:   PIN_R    R1
        AND      R1, R1, R6
        ADDS     R1, R1, R1
        JZ       cbs
        JMP      endcs
c8nt:   PIN_R    R1
        AND      R1, R1, R6
        ADDS     R1, R1, R1
        JZ       c8n
        JMP      endcs
c8st:   PIN_R    R1
        AND      R1, R1, R6
        ADDS     R1, R1, R1
        JZ       c8s
        JMP      endcs
xbnt:   PIN_R    R1
        AND      R1, R1, R6
        ADDS     R1, R1, R1
        JZ       xbn
        JMP      endcs
xbst:   PIN_R    R1
        AND      R1, R1, R6
        ADDS     R1, R1, R1
        JZ       xbs
        JMP      endcs
xb8nt:  PIN_R    R1
        AND      R1, R1, R6
        ADDS     R1, R1, R1
        JZ       xb8n
        JMP      endcs
xb8st:  PIN_R    R1
        AND      R1, R1, R6
        ADDS     R1, R1, R1
        JZ       xb8s
        JMP      endcs

; the command's low nibble -> its entry
cmdtab: .WORD    c_0, c_ign, c_2, c_3, c_4, c_5, c_6, c_7
        .WORD    c_8, c_ign, c_ign, c_b, c_ign, c_ign, c_e, c_f
