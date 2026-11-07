; The watching thread of the I2C bus monitor, and its xlat: see mon_i2c.s, which
; also describes the events. flight_i2c.s uses the same code.
.include "msf.inc"
.equ SDAP,   5
.equ SCLP,   6
.equ SDA,    1 << SDAP
.equ SCL,    1 << SCLP
.equ HMIN,   48
.equ LMIN,   56
.equ HWAIT,  HMIN - 12             ; timed fall wait: catches every high to HMIN + 4
.equ HFLOOR, 16                    ; shorter highs end before that wait starts
.equ LSLACK, 10
.equ WMIN,   10                    ; a rise wait shorter than this did not wait
.equ GAPW,   10                    ; nor a fall wait in the watching loop
.equ GAPC,   2                     ; ... then the fall was at least this early
.equ MOVC,   6                     ; a fall seen by a look: at least this early
.equ QW,     13                    ; a fall wait after queuing: did it wait?
.equ MARK,   9                     ; SR seed (and bits per byte): a byte's 9
                                   ; bits shift in under it, so its word is
                                   ; 0x1200 | data << 1 | ACK
.equ LATER,  0x1000                ; a fall pushed back this far reads as fine

; R0 = when SCL rose, R1 = when the rise wait began (then scratch),
; R2 = when SCL last fell + LMIN - 1, R3 = bits left in the byte,
; R4 = SDA | SCL (also the low-phase timeout), R5 = both lines as SCL rose,
; R7 = HWAIT, then when SCL fell + LMIN - 1
start:  LDR      R1, #(7 | SDAP << 3)
        SER_CFG  R1                ; SR in = SDA
        LDI      R1, 64 | 5 << FIFO_N
        FIFO_CFG R1                ; FIFO: SRAM 64..127, empty
        LDI      R1, report
        TSTART   R1
        LDR      R1, #HWAIT
        TMO      R1                ; (only the bit loop below changes it)

; ---- bus free: wait for a START (SDA falls while SCL is high)
idle:   WAIT_EDGE SDAP, low        ; (a level: a START that came while 'P'
        JZ       idle              ; was being queued is not missed)
        PIN_R    R1
        LDR      R4, #SCL
        AND      R1, R1, R4
        ADDS     R1, R1, R1
        JNZ      st
iw:     WAIT_EDGE SDAP, high       ; SDA low with SCL low: not a START
        JZ       iw
        JMP      idle
st:     LDI      R1, 'S' << 8
        FIFO_PUT R1
st1:    LDR      R3, #MARK         ; bits left in this byte
        SR_LD    R3
sw:     WAIT_EDGE SCLP, low        ; SCL falls after the START (maybe while
        JZ       sw                ; 'S' was being queued: do not check the
        CNT_R    R2                ; first low)
        LDI      R4, LATER
        SUBS     R2, R2, R4
        LDR      R4, #(SDA | SCL)

; ---- SCL low: wait for it to rise. The low was short when R0 - R2 < 0
; (lengths are even: for an even LMIN that is < LMIN, and it is never true
; for a legal low)
rnext:  LDR      R7, #HWAIT
        TMO      R4                ; 96: a longer low goes round rlong
        CNT_R    R1
rise:   WAIT_EDGE SCLP, high
        JZ       rlong
        CNT_R    R0
rise1:  SHIFT_IN                   ; SDA
        PIN_R    R5                ; both lines as SCL rose
        AND      R5, R5, R4
        TMO      R7
        WAIT_EDGE SCLP, low        ; every high that may be short ends in here
        JZ       hlong
        CNT_R    R7                ; SCL fell, exactly
        SUBS     R5, R7, R0        ; the high
        LDR      R4, #(HMIN - 1)
        SUBS     R4, R5, R4
        JN       hshort
        LDR      R4, #(SDA | SCL)
        LDR      R5, #(LMIN - 1)
        ADDS     R7, R7, R5
        JMP      fallen
rlong:  CNT_R    R2                ; SCL low for 96 cycles, so fine: push the
        SUBS     R2, R2, R4        ; last fall back with the time, and the
        CNT_R    R1                ; counter cannot wrap under the low. (If
        WAIT_EDGE SCLP, high       ; SCL rose as the wait timed out, this one
        JZ       rlong             ; does not wait: R1 just before it shows
        CNT_R    R0                ; that, and the high is not checked)
        JMP      rise1

; ---- SCL high for longer than HMIN: look at both lines, wait for the fall
hlong:  PIN_R    R1
        AND      R1, R1, R4
        SUBS     R7, R1, R5
        JNZ      moved             ; SCL fell, or SDA moved (START / STOP)
        CNT_R    R7
        WAIT_EDGE SCLP, low
        JZ       hlong
        CNT_R    R1                ; SCL fell: at R1 if the wait waited; if
        SUBS     R7, R1, R7        ; not (R1 = R7 + 8) since the look, so at
        LDR      R5, #GAPW         ; the earliest R1 - 8 - GAPC
        SUBS     R7, R7, R5
        LDR      R5, #(LMIN - 1)
        JP       hl_x
        LDR      R5, #(LMIN - 1 - 8 - GAPC)
hl_x:   ADDS     R7, R1, R5
        JMP      fallen
moved:  CNT_R    R7
        LDR      R5, #SCL
        SUBS     R5, R1, R5
        JP       sdachg            ; SCL still high: SDA moved
        LDR      R5, #(LMIN - 1 - MOVC)
        ADDS     R7, R7, R5        ; SCL fell at the earliest R7 - MOVC

; ---- SCL fell at R7 - (LMIN - 1), after a legal high: check the low
fallen: SUBS     R1, R0, R2        ; the low - (LMIN - 1)
        JN       lshort
        MOV      R2, R7
fnext:  DECR     R3
        JNZ      rnext
bq0:    LDR      R5, #0            ; (no short phase on it)
bq:     SR_RD    R1                ; the byte's 9th bit (ACK): its word, to
        LDR      R3, #MARK         ; be queued after the next rise (rise_qb);
        SR_LD    R3                ; SR = the next byte's seed (MARK = 9 =
        JMP      rnext_qb          ; its bits)

; ---- a short high: R5 = its length (HFLOOR and below: SCL fell before
; the timed wait began, so for the next low it fell at the earliest at R0)
hshort: LDR      R4, #(HFLOOR + 1)
        SUBS     R4, R5, R4
        JP       hs1
        MOV      R7, R0
hs1:    LDR      R4, #(LMIN - 1)
        ADDS     R7, R7, R4
        SUBS     R4, R0, R1        ; too short, if SCL rose while this thread
        LDR      R1, #WMIN         ; waited for it (else it is not known)
        SUBS     R4, R4, R1
        LDR      R4, #(SDA | SCL)
        JN       fallen
        SUBS     R1, R0, R2        ; the low before it - (LMIN - 1)
        JMP      tword

; ---- a short phase: R1 = the low - (LMIN - 1) (short if < 0), R5 = the
; high (127 = fine). Both wait for the next rise, where they are queued as
; one word (rise_q), so that this fall stays short
lshort: LDR      R5, #127
tword:  MOV      R2, R7            ; this fall
        DECR     R3
        JNZ      rnext_q
        JMP      bq                ; the byte's 9th bit: flagged in its word

; ---- the byte after its first short phase: queue the timing word (R1,
; R5 as above) once SCL rose and SDA is sampled, then decode the rest of
; the byte without timing it (qrise), so that a master that is too fast
; throughout keeps up and the reports do not flood
rnext_q: TMO     R4
rise_q: WAIT_EDGE SCLP, high
        JZ       rise_q
        SHIFT_IN                   ; SDA
        INCR     R1
        DECR     R1                ; N = the low was short
        LDR      R7, #(LMIN - 1)
        JN       q_l
        LDR      R1, #(127 - (LMIN - 1))
q_l:    ADDS     R1, R1, R7        ; the low (127 = fine)
        LDH      R1, #1
        SHL      R1, R1, #7
        OR       R1, R1, R5        ; 0x8000 | low << 7 | high
        FIFO_PUT R1
        LDR      R7, #HWAIT
        TMO      R7
        JMP      qline
qrise:  WAIT_EDGE SCLP, high
        JZ       qrise
        SHIFT_IN                   ; SDA
qline:  PIN_R    R5                ; both lines as SCL rose
        AND      R5, R5, R4
qhl:    WAIT_EDGE SCLP, low        ; between waits, look for START / STOP
        JNZ      qfell
        PIN_R    R1
        AND      R1, R1, R4
        SUBS     R7, R1, R5
        JZ       qhl
        LDR      R7, #SCL
        SUBS     R7, R1, R7
        JP       sdachg            ; SCL still high: SDA moved
qfell:  DECR     R3
        JNZ      qrise
        CNT_R    R2                ; the byte's 9th bit: timing resumes,
        LDI      R7, LATER         ; the low after it unchecked
        SUBS     R2, R2, R7
        JMP      bq0

; ---- the bit after a byte's 9th bit: queue the byte's word (R1) once SCL
; rose and SDA is sampled, with a flag if that 9th bit had a short phase
; (R5 != 0: bit 10 if the low (R5 = 127), else bit 11 for the high: one
; word per bit, so its value is not reported). This bit's high is not
; checked; if SCL fell
; before the timed wait below began, for the next low it fell at the
; earliest at R0 (and if it fell while queuing, there is no wait at all)
rnext_qb: TMO    R4
rise_qb: WAIT_EDGE SCLP, high
        JZ       rlong_qb
        CNT_R    R0
        SHIFT_IN                   ; SDA
        ADDS     R7, R5, R5
        JZ       q_p
        LDR      R7, #127
        SUBS     R7, R5, R7
        LDR      R5, #0
        LDH      R5, #(0x800 >> 8)
        JNZ      q_f
        LDH      R5, #(0x400 >> 8)
q_f:    OR       R1, R1, R5
q_p:    FIFO_PUT R1
        PIN_R    R5                ; both lines (SDA as SCL rose: a START /
        AND      R5, R5, R4        ; STOP comes later than this)
        LDR      R7, #SCL
        SUBS     R7, R5, R7
        JN       q_e               ; SCL fell already
        CNT_R    R1
        LDR      R7, #HWAIT
        TMO      R7
        WAIT_EDGE SCLP, low
        JZ       hlong
        CNT_R    R7                ; SCL fell: at R7 if this wait waited
        SUBS     R1, R7, R1
        LDR      R5, #QW
        SUBS     R1, R1, R5
        JP       q_x
q_e:    MOV      R7, R0
q_x:    LDR      R5, #(LMIN - 1)
        ADDS     R7, R7, R5
        JMP      fallen
rlong_qb: CNT_R  R2
        SUBS     R2, R2, R4
        JMP      rise_qb

; ---- SDA moved while SCL was high: START (it fell) or STOP (it rose).
; R1 = the lines now. Inside a byte, 'X' with the bits seen first, in the
; same word (bit 4: a STOP follows, else a START), so that thread 0 is back
; in time for the next byte's first bit after a START
sdachg: LDR      R4, #SDA
        AND      R2, R1, R4        ; SDA now: 0 = it fell = START
        LDR      R1, #9
        SUBS     R1, R1, R3        ; bits of the byte before this SCL high
        JZ       edge              ; none: between bytes, as it should be
        LDH      R1, #'X'
        ADDS     R2, R2, R2
        JZ       xs
        LDR      R4, #0x10
        OR       R1, R1, R4
xs:     FIFO_PUT R1
        ADDS     R2, R2, R2
        JZ       st1
        JMP      idle
edge:   ADDS     R2, R2, R2
        JZ       st                ; it fell: (repeated) START
        LDI      R1, 'P' << 8
        FIFO_PUT R1
        JMP      idle

; ---- thread 1: queued words -> events
; 0x8000 | low << 7 | high     ->  'L', low  and / or  'H', high (127 = fine)
; 'X' << 8 | 0x10 | n         ->  'X', n  then  'P' (no 0x10: 'S')
; MARK << 9 | data << 1 | ACK  ->  'A' / 'N', data; bit 10 / 11 set: then
;                                 'L' / 'H' 0 (on the 9th bit, not measured)
; anything else is an event already
xlat:   LDR      R2, #0
        SUBS     R4, R1, R2        ; N = a timing word
        JN       xl_t
        SHR      R4, R1, #7
        SHR      R4, R4, #5        ; word >> 12: 1 = a byte
        DECR     R4
        JZ       xl_b
        SHR      R4, R1, #7
        SHR      R4, R4, #1        ; code
        LDR      R5, #'X'
        SUBS     R5, R4, R5
        JNZ      xl_r              ; an event already
        LDI      R2, 'S' << 8
        LDR      R5, #0x10
        AND      R4, R1, R5
        ADDS     R4, R4, R4
        JZ       xl_x
        LDI      R2, 'P' << 8
xl_x:   LDR      R4, #0x0F
        AND      R1, R1, R4
        LDH      R1, #'X'
        RET
xl_t:   LDR      R5, #127
        AND      R2, R1, R5        ; high
        SHR      R1, R1, #7
        AND      R1, R1, R5        ; low
        SUBS     R4, R2, R5
        JZ       xl_l              ; high fine: 'L' alone
        LDH      R2, #'H'
        SUBS     R4, R1, R5
        JZ       xl_h              ; low fine: 'H' alone
        LDH      R1, #'L'          ; 'L' then 'H'
        RET
xl_h:   MOV      R1, R2
        LDR      R2, #0
        RET
xl_l:   LDH      R1, #'L'
        LDR      R2, #0
        RET
xl_b:   SHR      R5, R1, #7
        SHR      R5, R5, #3
        LDR      R4, #3
        AND      R5, R5, R4        ; (word >> 10) & 3: the flags
        SHR      R2, R1, #1
        LDR      R4, #0xFF
        AND      R2, R2, R4        ; data
        LDR      R4, #1
        AND      R1, R1, R4        ; ACK bit (0 = acknowledged)
        LDI      R4, 'A' << 8
        ADDS     R1, R1, R1
        JZ       xl_a
        LDI      R4, 'N' << 8
xl_a:   OR       R1, R2, R4
        LDR      R2, #0
        ADDS     R5, R5, R5        ; word >> 10: 0, or 1 (bit 10), or 2
        JZ       xl_r
        LDI      R2, 'L' << 8
        LDR      R4, #2
        SUBS     R4, R5, R4
        JZ       xl_r
        LDI      R2, 'H' << 8
xl_r:   RET
