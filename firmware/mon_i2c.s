; Bus monitor, I2C: listens to SDA and SCL without driving them. Thread 0
; captures the bus, thread 1 formats and reports events (report.inc).
;   pins  : SDA uio[5], SCL uio[6] (inputs), reports on uio[0]
;   HMIN  : shortest legal SCL high, cycles (define; 48 = 4.0 us at 12 MHz)
;   LMIN  : shortest legal SCL low, cycles (define; 56 = 4.7 us at 12 MHz)
;           (standard mode; thread 0 runs at half rate beside the reporter,
;           so fast mode needs a faster core)
; Events (code, data):
;   'S' START   'P' STOP   'A' byte, acknowledged   'N' byte, not acknowledged
;   'X' a START or STOP inside a byte: data = bits of the byte seen before it
;   'H' SCL high shorter than HMIN: data = its length in cycles (+-1; HFLOOR
;       for anything shorter than that)
;   'L' SCL low shorter than LMIN: data = its length, -1 to +LSLACK (and
;       about 38 to 54 for a low shorter than thread 0's turnaround)
;   'L' / 'H' with data 0: that phase was short on a byte's 9th bit, which
;       has no room for its length (follows the byte)
; A legal bus never raises 'H' or 'L': lengths are counted from the cycles
; the level waits on SCL released, and wherever an edge may have come
; before its wait began, from the earliest it can have been, so a phase is
; reported only when even its longest possible length is too short.
;  - after SCL rises, thread 0 waits for the fall with a timeout a little
;    over HMIN, so every high that may be too short is timed exactly;
;  - a longer high is watched for START / STOP too: thread 0 alternates a
;    look at both lines with a timed wait for the fall. A fall during a
;    wait is exact; one between a look and the wait counts from the look,
;    so the low after it may read up to LSLACK cycles long;
;  - nothing is queued at a fall, so thread 0 is back in time for the next
;    edge: a byte's word is pushed into the FIFO after the next rise, which
;    is why a byte's first bit's high is not timed (the low after it is,
;    whenever that high lasts 36 cycles or more: every legal high does);
;  - only the first short phase of each byte is reported: that word too is
;    queued after the next rise, and the rest of the byte is decoded
;    without timing it, so a master that is too fast throughout still
;    decodes and the reports do not flood.
; Every byte decodes on any bus whose SCL lows are at least 16 cycles and
; whose bits are at least 72 (6 us), however far out of spec (the legal
; minimums are 56 and 104); test/test_monitors.py checks these claims.
.include "mon_i2c.inc"
.include "report.inc"
