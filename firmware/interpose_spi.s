; In-line interposer, SPI: sits between a controller and a device, passes the bus
; through and, if asked, flips one MOSI bit. No host takes part.
;   pins  : controller -> SCK ui[2], MOSI ui[3], CS ui[5]; those come out on uo[2],
;           uo[3], uo[5] to the device. Device's MISO -> uio[7]; uio[4] -> controller.
;   FLIP  : 1 = flip bit K (0 = the first sent) of byte N of the first transfer (define)
; Thread 0 is the wire: 10 slots, 20 cycles with two threads running, 11 once thread 1
; has stopped. The three lines going to the device are read and written together, so they
; keep their relationship to the cycle. How fast it goes, half periods measured at the RTL
; (test_interpose.py), SCK at 12 MHz:
;   a controller that reads MISO back: 35 cycles, 171 kHz, for the round trip through the
;   loop twice (34 fails on some phases); MOSI alone: 12 cycles, 500 kHz; with FLIP set,
;   until the flip is done: 22 cycles, 272 kHz.
; Thread 1 counts SCK's falling edges and tells thread 0 when to change MOSI.
.include "msf.inc"
.equ FLIP,   0
.equ N,      1
.equ K,      3
.equ SCKP,   2
.equ MOSI,   0x08
.equ MASKW,  8

start:  LDR      R1, #0
        TMO      R1
        LDR      R2, #MASKW
        ST       R2, R1
        OE_SET   0x10              ; uio[4] drives the controller's MISO
        LDI      R1, counter
        TSTART   R1
        LDR      R3, #0x2C         ; SCK, MOSI, CS
        LDR      R5, #MASKW
        LDR      R6, #0x80
; ---- thread 0, the wire
wire:   IN_RD    R1
        AND      R1, R1, R3
        LD       R4, R5
        XOR      R1, R1, R4
        OUT_W    R1
        PIN_R    R2
        AND      R2, R2, R6
        SHR      R2, R2, #3        ; uio[7] -> uio[4]
        PIN_W    R2
        JMP      wire

; ---- thread 1: N * 8 + K falling edges of SCK, then MOSI is flipped until the next.
; In mode 0 MOSI changes on a falling edge, so that is one whole bit.
counter: LDR     R1, #FLIP
        ADDS     R1, R1, R1
        JZ       c_stop
        LDI      R6, N * 8 + K
        ADDS     R2, R6, R6
        JZ       c_go              ; the very first bit: it is already set up
c_lp:   WAIT_EDGE SCKP, fall, ui
        DECR     R6
        JNZ      c_lp
c_go:   LDR      R1, #MOSI
        LDR      R2, #MASKW
        ST       R2, R1
        WAIT_EDGE SCKP, fall, ui
        LDR      R1, #0
        ST       R2, R1
c_stop: TSTOP                      ; (T1 stops: the wire then runs at 11 cycles a turn)
c_h:    JMP      c_h
