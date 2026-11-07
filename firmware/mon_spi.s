; Bus monitor, SPI: listens to CS, SCK, MOSI and MISO without driving them.
; Thread 0 samples the bus, thread 1 reports events (report.inc).
;   pins  : SCK uio[2], MOSI uio[3], CS uio[5] (active low), MISO uio[7]
;           (inputs); reports on uio[0]
;   MODE  : SPI mode 0..3 (define): bits are sampled on the edge the mode
;           samples on, MSB first
; Events (code, data), per transfer (CS low .. CS high):
;   '>' a byte on MOSI, then '<' the byte on MISO in the same 8 clocks
;   then, once CS is high again:
;   'M' SCK was not at the mode's idle level (CPOL) as CS fell: the
;       controller uses another mode (data: SCK's level)
;   'E' the transfer ended: data = bits of a byte left over (0: the
;       transfer was whole bytes). Every transfer ends with 'E'.
; Nothing is pushed while CS is low except two words per byte (two FIFO_PUTs),
; so thread 0 keeps up with SCK periods of 28 cycles inside a byte and 36
; from a byte's last sampling edge to the next byte's first (430 / 330 kHz at
; 12 MHz), and with 16 cycles from CS low to the first sampling edge (and 6
; to SCK's first edge, for the mode check). CS is looked at once SCK has
; been still for POLL cycles, so CS must stay high for longer than that.
; The reporter sends two events per byte, about 480 cycles: faster traffic
; is buffered in the FIFO (32 bytes), then dropped and counted ('O').
.include "mon_spi.inc"
.include "report.inc"
