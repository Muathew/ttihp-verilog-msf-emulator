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
.equ ST_DUAL,    0x0020
.equ ST_TID,     0x0040
.equ ST_FAVAIL,  0x0080       ; FIFO not empty
.equ ST_FFULL,   0x0100

; ---- FIFO_CFG ----  base in bits 8:0 (size-aligned block), size 2 << n
.equ FIFO_N,     9            ; FIFO_CFG value = base | (n << FIFO_N)

; ---- conventional done marker on uo_out used by the demos ----
.equ DONE,       0x55
