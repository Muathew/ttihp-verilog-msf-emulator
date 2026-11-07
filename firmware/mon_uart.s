; Bus monitor, UART: listens to an 8N1 line without driving it. The RX engine
; decodes every frame while thread 0 times the same frame edge by edge (two
; readers on one wire); thread 1 reports events to the host (report.inc).
;   pins  : LINE uio[1] (input), reports on uio[0] (1 Mbaud at 12 MHz)
;   BIT   : nominal bit time in cycles (define; 104 = 115200 at 12 MHz)
;   TOL   : bit-time error worth reporting (define; cycles, BIT >> 4)
; Events (code, data):
;   'U' byte           'F' byte whose stop bit was low (framing error)
;   'B' the frame's first rising edge was more than TOL cycles off its
;       nominal place: data = signed offset in cycles (-128..127). The more
;       low bits before it, the smaller the baud error it catches.
;   'G' a low pulse shorter than half a bit: data = its length in cycles
;       (a receiver then sees the byte reported next)
; Every frame is decoded. Its timing is checked when thread 0 saw its start
; edge, i.e. after at least about half a bit of idle line; a frame whose
; start it missed is reported but not timed (at the latest 10 bits later).
.include "mon_uart.inc"
.include "report.inc"
