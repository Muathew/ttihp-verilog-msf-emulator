; 1-Wire presence responder (the device side of Maxim's reset and presence), on the v4
; timed pins (isa.md 6.8): each reset pulse from the master is answered with a presence
; pulse that starts exactly PDLY us after the master releases the line and lasts PLEN us.
; The edges are made by the hardware on their cycle (AT), not by an instruction that has to
; run on that cycle, so the reply has no jitter whatever the phase of the master's edge.
; A low shorter than RSTMIN us is a slot, not a reset, and gets no answer.
; Both timed writes are deadlines for tools/msf_timing.py (d >= 0 when the write issues,
; or the chip refuses it), and test_ow_presence.py checks them on the RTL.
;   pins : DQ uio[3], open drain with a pull-up (the chip only pulls it low)
;   US   : cycles per microsecond (define; 12 at 12 MHz)
; The length test is 16-bit: a low of RSTMIN * US to RSTMIN * US + 32767 cycles (240 us to
; about 2.97 ms at 12 MHz) is a reset, a longer one is taken as a slot; Maxim's reset is
; 480 to 960 us.
.include "msf.inc"
.equ US,     12
.equ DQ,     0x08
.equ RISE,   0x13                   ; WAIT_EDGE: rising edge on uio[3]
.equ FALL,   0x23                   ; WAIT_EDGE: falling edge on uio[3]
.equ PDLY,   30                     ; us from the master's release to the presence pulse
.equ PLEN,   120                    ; us, the presence pulse (Maxim: 60 to 240)
.equ RSTMIN, 240                    ; us: a shorter low is a slot

start:  LDR      R1, #0
        TMO      R1                 ; no timeouts
        PIN_W    R1                 ; DQ output 0: OE pulls the line low; released now
        ESEL     #2                 ; port page 1: AT and TS
idle:   WAIT_EDGE #FALL             ; the master pulls the line low
        TS       R1                 ; when (the synchronised edge)
        WAIT_EDGE #RISE             ; ... and releases it
        TS       R2
        SUBS     R3, R2, R1         ; how long the line was low
        LDI      R4, (RSTMIN * US)
        SUBS     R3, R3, R4
        JN       idle               ; shorter: a slot, not a reset
        LDI      R4, (PDLY * US)
        ADDS     R5, R2, R4
        AT       R5
        OE_SET   DQ                 ; presence: low from release + PDLY (a timed write)
        WAIT_CMP R5                 ; the event is applied: the slot is empty again, so
        LDI      R4, (PLEN * US)    ; the next AT is not refused (isa.md 6.8)
        ADDS     R5, R5, R4
        AT       R5
        OE_CLR   DQ                 ; released after PLEN (a timed write)
        WAIT_CMP R5                 ; the release is applied before the next reset is seen
        JMP      idle
