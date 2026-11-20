; msfcore v2 firmware constants (docs/isa.md section 6).
; Include with:  .include "msf.inc"

; ---- TX_CFG ----  pin in bits 2:0, word width (n-1) << 8
.equ TX_DIFF,    0x0008       ; drive pin+1 with the complement
.equ TX_NRZ,     0x0000
.equ TX_NRZI,    0x0010       ; 0 = transition (USB)
.equ TX_MANCH,   0x0020       ; IEEE 802.3: 1 = low->high
.equ TX_STUFF,   0x0040       ; insert 0 after six 1s
.equ TX_LSB,     0x0080
.equ TX_IDLE1,   0x1000
.equ TX_EN,      0x2000
.equ TX_RELEASE, 0x4000       ; engine owns its pins only while transmitting

; ---- RX_CFG ----  pin in bits 2:0, word width (n-1) << 8
.equ RX_NRZI,    0x0010
.equ RX_MANCH,   0x0020       ; Manchester, RX_DIV = half-bit (IEEE 802.3)
.equ RX_MANPLL,  0x0030       ; Manchester, phase-tracking: RX_DIV = 65536 x bit rate / clock (v3)
.equ RX_DESTUFF, 0x0040
.equ RX_LSB,     0x0080
.equ RX_CHAR,    0x1000       ; async character (UART-style start bit)
.equ RX_EN,      0x2000
.equ RX_RESYNC,  0x4000

; ---- CRC_CFG ----  bits per CRC_W - 1 in bits 3:0
.equ CRC_REFL,   0x0010

; ---- STAT bits ----
.equ ST_TXFULL,  0x0001
.equ ST_TXBUSY,  0x0002
.equ ST_RXAVAIL, 0x0004
.equ ST_RXOVR,   0x0008
.equ ST_CRCBUSY, 0x0010
.equ ST_DUAL,    0x0020       ; T1 running
.equ ST_TID,     0x0040       ; executing thread id, bit 0
.equ ST_FAVAIL,  0x0080       ; FIFO not empty
.equ ST_FFULL,   0x0100
.equ ST_CAPRUN,  0x0200       ; capture engine recording
.equ ST_CAPDONE, 0x0400       ; capture finished
.equ ST_T2RUN,   0x0800       ; T2 running
.equ ST_T3RUN,   0x1000       ; T3 running
.equ ST_TID1,    0x2000       ; executing thread id, bit 1

; ---- FIFO_CFG ----  base in bits 9:0 (size-aligned block), size 2 << n
.equ FIFO_N,     10           ; FIFO_CFG value = base | (n << FIFO_N)

; ---- capture engine ----  CAP_BUF = base | (n << CAP_N): 2 << n words
.equ CAP_N,      10
; CAP_TRIG = mask | value << 8; CAP_CTL = post-trigger words | these:
.equ CAP_EDGE,   0x1000       ; trigger on the condition becoming true
.equ CAP_UI,     0x2000       ; record ui_in instead of uio
.equ CAP_CHG,    0x4000       ; change mode (else two samples a word)
.equ CAP_ARM,    0x8000       ; start (0: stop)
; FILTER = pins | len << FILT_LEN: those uio pins change only after holding
; a new level len cycles
.equ FILT_LEN,   8
; CAP_STAT bits (bits 9:0: next word's address)
.equ CS_RUN,     0x8000
.equ CS_TRIG,    0x4000
.equ CS_DONE,    0x2000
.equ CS_OVF,     0x1000

; ---- conventional done marker on uo_out used by the demos ----
.equ DONE,       0x55

; ---- v4 (docs/isa.md sections 3.2, 6.8, 6.9, 6.10) ----
; ESEL #m: the thread's engine set (bit 0) and port page (bits 3:1). A thread that uses the page-0 ports
; (FIFO, capture, filter, late guard) and the timed pins switches with one ESEL each way.
.equ ESEL_SET0,   0x00       ; engine set 0, port page 0 (every thread starts here)
.equ ESEL_SET1,   0x01       ; engine set 1: TX_*, RX_*, CRC_* and STAT bits 0-4 address the second set
.equ ESEL_TIMED,  0x02       ; port page 1: AT, TS, AT_STAT
.equ ESEL_OPT,    0x04       ; port page 2, engine options (+ ESEL_SET1 for set 1)
.equ ESEL_ID,     0x06       ; port page 3: CHIPID, CHIPVER, CHIPCAPS, CNT_H
; RX_CFG bit 3 selects the ui pin, TX_CFG bit 15 drives uo instead of uio
.equ RX_UI,       0x0008
.equ TX_UO,       0x8000
; TX_OPT / RX_OPT: stuffing count n in bits 3:0, kind in bit 4 (0 after n ones a 0, 1 after n equal bits the
; opposite), bit 5: TX stream (underrun is an error) / RX check the stop bit (character mode)
.equ OPT_KIND1,   0x0010
.equ OPT_STREAM,  0x0020
.equ OPT_STOPCHK, 0x0020
; RX_OPT bit 6: mode 3 fine (half-cycle sampling, a 31-centre lock, 1/32 tracking: 10BASE-T's 13.5 ns)
.equ OPT_FINE,    0x0040
; TX_EXT / RX_EXT: clock pin | bank << 3 | falling << 4 | enable | pre << 6 | gate << 8 | gate bank << 11 |
; gate active high << 12 | gate enable << 13
.equ EXT_FALL,    0x0010
.equ EXT_EN,      0x0020
.equ EXT_PRE,     0x0040
.equ EXT_GATE_HI, 0x1000
.equ EXT_GATE,    0x2000     ; | gate pin << 8
; ERR_STAT bits
.equ ERR_FRAME,   0x0001
.equ ERR_STUFF,   0x0002
.equ ERR_UNDER,   0x0004
.equ ST_ERR,      0x8000     ; STAT bit 15
