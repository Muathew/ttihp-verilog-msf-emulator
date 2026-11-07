; @slow-analysis (msf_timing.py takes minutes on this one)
; Pin finder: which of eight unknown wires are a UART, an SWD port or a JTAG port?
; The first step on a board nobody documented. Connect uio[7:0] to the wires and a
; ground, load this, and read the report: no host takes part.
;
; It listens first and drives nothing: a pin that moves by itself is being driven,
; and is never driven by this program. Then it tries the rest, every ordered pair.
;   UART: during the listen, each moving pin's shortest stable run is taken for a
;         bit time, and the RX engine confirms it on the bytes that follow: past
;         the first 4 frames (it may start mid-stream), at least 2 good, none bad,
;         more than one value (a clock line reads as a stream of 0x55). So the line
;         must be busy during the listen, and still for 12 frames after it.
;   SWD : (SWCLK, SWDIO): line reset, JTAG-to-SWD, read DPIDR twice; needs ACK OK,
;         good parity and the same value both times.
;   JTAG: (TCK, TMS): reset the TAP and shift out the IDCODE twice, reading every
;         other pin at once, so TDO needs no search of its own: bit 0 set, not all
;         ones, the same both times. Then TDI: with each pin left, load BYPASS and
;         look for a pattern coming back late on TDO. 0xFF: none found.
;
; Driving unknown pins can fight whatever is on them. Each try is short, but on a
; real target put series resistors in the wires, and match the voltage.
;
; Report: 8N1 on uo_out[0] at UBIT cycles a bit (uio is all in use), byte by byte:
;   'U' pin  bit-time low  bit-time high
;   'S' swclk  swdio  id0 id1 id2 id3    (DPIDR, least significant byte first)
;   'J' tck  tms  tdo  tdi  id0 id1 id2 id3
;   'E' number of SWD and JTAG finds. Then uo_out = DONE (bit 0 is the idle level).
; Also in data RAM for a host that cannot read the pin: V_N, the number of SWD and
; JTAG finds, and from HITS on 4 words each: kind << 8 | a, b << 8 | c, id low, id
; high (for 'S' a, b = swclk, swdio; for 'J' a, b, c = tck, tms, tdo). 'U' finds are
; reported on the pin only.
;
; Defines: PINS, the wires to try (mask); LISTEN, SWD_ON, JTAG_ON (1 = do that step);
; FIRST (1 = stop at the first SWD or JTAG find); WINDOW, cycles to listen for.
.include "msf.inc"
.equ PINS,    0xFF
.equ LISTEN,  1
.equ SWD_ON,  1
.equ JTAG_ON, 1
.equ FIRST,   0
.equ UBIT,    104                  ; 115200 baud at 12 MHz
.equ WINDOW,  48000                ; 4 ms at 12 MHz
.equ MINBIT,  8                    ; a shorter run is a glitch, not a bit
.equ RING,    960                  ; capture ring: 64 words, for the run lengths;
.equ RINGN,   5                    ; the program ends below it
.equ RINGSZ,  2 << RINGN
; data RAM
.equ V_PINS,  0                    ; wires left to try
.equ V_KIND,  1                    ; the report's fields, in order: the kind,
.equ V_A,     2                    ; the pair being tried
.equ V_B,     3
.equ V_N,     4                    ; SWD and JTAG finds so far
.equ V_HIT,   6                    ; where the next find goes
.equ V_IDL,   7                    ; the id of this try
.equ V_IDH,   8
.equ V_RL,    9                    ; one read
.equ V_RH,    10
.equ V_C,     11                   ; a loop's pin
.equ V_TDO,   12                   ; pin; 0xFF for SWD
.equ V_TDI,   13                   ; pin, or 0xFF
.equ V_MTDI,  14                   ; mask: the candidate TDI, 0 = none
.equ V_ACT,   15                   ; pins that moved while listening
.equ L_SCAN,  16                   ; return addresses: CALL overwrites R7, and each
.equ L_TRY,   17                   ; routine that calls saves it in its own word
.equ L_READ,  18
.equ L_JSCAN, 20
.equ L_JWORD, 21
.equ L_JTDI,  22
.equ L_JTRY,  23
.equ L_LIS,   24
.equ L_HIT,   25
.equ V_T,     26
.equ V_BIT,   27
.equ V_OK,    28
.equ V_BAD,   29
.equ V_V0,    30
.equ V_MIX,   31
.equ S1,      32                   ; 32 samples of all pins: the first scan
.equ S2,      64                   ; and the second
.equ U_LAST,  96                   ; per pin: when it last changed, 0 = not yet
.equ U_MIN,   104                  ; per pin: shortest run, 0 = none
.equ HITS,    112                  ; 4 words each: 2 finds
.equ HITSEND, 120

start:  LDR      R1, #0
        TMO      R1                ; no timeouts
        UO_WI    0x01              ; the report line idles high
        OE_WI    0
        LDR      R2, #V_N
        ST       R2, R1
        LDR      R2, #V_HIT
        LDR      R1, #HITS
        ST       R2, R1
        LDR      R2, #V_PINS
        LDI      R1, PINS
        ST       R2, R1
        LDR      R1, #LISTEN
        ADDS     R1, R1, R1
        JZ       m_swd
        CALL     listen
m_swd:  LDR      R1, #SWD_ON
        ADDS     R1, R1, R1
        JZ       m_jtag
        LDR      R1, #'S'
        CALL     scan
m_jtag: LDR      R1, #FIRST
        ADDS     R1, R1, R1
        JZ       m_j2
        LDR      R2, #V_N          ; FIRST, and SWD found one
        LD       R1, R2
        ADDS     R1, R1, R1
        JNZ      finish
m_j2:   LDR      R1, #JTAG_ON
        ADDS     R1, R1, R1
        JZ       finish
        LDR      R1, #'J'
        CALL     scan
finish: LDR      R1, #'E'
        CALL     uo_byte
        LDR      R2, #V_N
        LD       R1, R2
        CALL     uo_byte
        UO_WI    DONE
halt:   JMP      halt

; ---- every ordered pair of the wires left; R1 = 'S' or 'J'. R0 = first pin's mask
; (SWCLK or TCK), R6 = second's (SWDIO or TMS), kept by everything below.
scan:   LDR      R2, #L_SCAN
        ST       R2, R7
        LDR      R2, #V_KIND
        ST       R2, R1
        LDR      R1, #0
        LDR      R2, #V_A
        ST       R2, R1
sa:     LDR      R2, #V_A
        CALL     pinmask
        MOV      R0, R1
        JZ       sa_n
        LDR      R1, #0
        LDR      R2, #V_B
        ST       R2, R1
sb:     LDR      R2, #V_B
        CALL     pinmask
        MOV      R6, R1
        JZ       sb_n              ; not a wire left
        SUBS     R1, R0, R6
        JZ       sb_n              ; the same wire
        LDR      R2, #V_KIND
        LD       R1, R2
        LDR      R2, #'S'
        SUBS     R2, R1, R2
        JZ       sb_s
        CALL     try_jtag
        JMP      sb_x
sb_s:   CALL     try_swd
sb_x:   OE_WI    0                 ; let go of everything between tries
        LDR      R1, #FIRST
        ADDS     R1, R1, R1
        JZ       sb_n
        LDR      R2, #V_N
        LD       R1, R2
        ADDS     R1, R1, R1
        JNZ      s_end
sb_n:   LDR      R2, #V_B
        LD       R1, R2
        INCR     R1
        ST       R2, R1
        LDR      R2, #8
        SUBS     R2, R1, R2
        JNZ      sb
sa_n:   LDR      R2, #V_A
        LD       R1, R2
        INCR     R1
        ST       R2, R1
        LDR      R2, #8
        SUBS     R2, R1, R2
        JNZ      sa
s_end:  LDR      R2, #L_SCAN
        LD       R7, R2
        RET

; R2 = the address of a pin number -> R1 = its mask; Z = 1 if it is off the list.
; Uses R2.
pinmask: LD      R2, R2
        LDR      R1, #1
pm_l:   DECR     R2
        JN       pm_x
        ADDS     R1, R1, R1
        JMP      pm_l
pm_x:   LDR      R2, #V_PINS
        LD       R2, R2
        AND      R2, R1, R2
        ADDS     R2, R2, R2
        RET

; R1 = pin 0..7 -> R1 = 1 << pin; uses R2
maskof: MOV      R2, R1
        LDR      R1, #1
mo_l:   DECR     R2
        JN       mo_x
        ADDS     R1, R1, R1
        JMP      mo_l
mo_x:   RET

; R1 = mask -> OE. OE_* take immediates, so this writes the instruction it runs
; (isa.md section 1: at least 2 instructions ahead). Uses R2, R5.
set_oe: LDI      R2, 0x9D00        ; OE_WI 0
        OR       R2, R2, R1
        LDI      R5, oe_slot
        ST       R5, R2
        NOP
        NOP
oe_slot: OE_WI   0
        RET

; ---- SWD. SWCLK idles high; the host sets SWDIO while it is high, the target
; samples on the rising edge and changes on rising edges, the host samples while it
; is low. Bits go LSB first.
try_swd: LDR     R2, #L_TRY
        ST       R2, R7
        OR       R1, R0, R6
        PIN_W    R1
        CALL     set_oe
        CALL     ones
        LDI      R3, 0xE79E        ; JTAG to SWD
        LDR      R4, #16
        CALL     s_send
        CALL     ones
        LDR      R3, #0
        LDR      R4, #8
        CALL     s_send            ; idle
        CALL     s_read            ; DPIDR
        JNZ      ts_x
        LDR      R2, #V_IDL
        ST       R2, R5            ; (s_read leaves the two halves in R3, R5)
        LDR      R2, #V_IDH
        ST       R2, R3
        CALL     s_read            ; and again: the same?
        JNZ      ts_x
        LDR      R2, #V_IDL
        LD       R1, R2
        SUBS     R4, R1, R5
        JNZ      ts_x
        LDR      R2, #V_IDH
        LD       R2, R2
        SUBS     R4, R2, R3
        JNZ      ts_x
        LDR      R1, #0xFF         ; no third pin
        LDR      R2, #V_TDO
        ST       R2, R1
        CALL     hit
ts_x:   LDR      R2, #L_TRY
        LD       R7, R2
        RET

; 64 ones on SWDIO: a line reset
ones:   OR       R2, R0, R6
        LDR      R4, #64
on1:    PIN_W    R2                ; SWCLK high, SWDIO high
        NOP
        XOR      R1, R2, R0
        PIN_W    R1                ; SWCLK falls
        NOP
        DECR     R4
        JNZ      on1
        PIN_W    R2
        RET

; R3 = bits, R4 = how many, LSB first; uses R1, R2
s_send: LDR      R1, #1
        AND      R1, R3, R1
        ADDS     R1, R1, R1
        JZ       ss0
        OR       R2, R0, R6
        JMP      ss1
ss0:    MOV      R2, R0
ss1:    PIN_W    R2                ; SWDIO, SWCLK still high
        XOR      R2, R2, R0
        PIN_W    R2                ; SWCLK falls
        NOP
        OR       R2, R2, R0
        PIN_W    R2                ; SWCLK rises: the target samples
        SHR      R3, R3, #1
        DECR     R4
        JNZ      s_send
        RET

; R4 bits -> R3, LSB first; uses R1, R2, R5
s_recv: LDR      R3, #0
        LDR      R5, #1
sr1:    LDR      R2, #0
        PIN_W    R2                ; SWCLK falls
        NOP
        PIN_R    R1                ; sampled while it is low
        AND      R1, R1, R6
        ADDS     R1, R1, R1
        JZ       sr0
        OR       R3, R3, R5
sr0:    PIN_W    R0                ; SWCLK rises: the target's next bit
        ADDS     R5, R5, R5
        DECR     R4
        JNZ      sr1
        RET

; One read of DPIDR: Z = 1 if ACK OK and the parity is right, with R5 = the low
; half and R3 = the high. Ends with the host driving SWDIO again, after 8 idle clocks.
s_read: LDR      R2, #L_READ
        ST       R2, R7
        LDR      R3, #0xA5         ; start, DP, read, A = 0, parity, stop, park
        LDR      R4, #8
        CALL     s_send
        MOV      R1, R0
        CALL     set_oe            ; the target takes the line
        LDR      R2, #0
        PIN_W    R2
        NOP
        PIN_W    R0                ; turnaround
        LDR      R4, #3
        CALL     s_recv
        LDR      R2, #1
        SUBS     R2, R3, R2
        JNZ      sr_bad
        LDR      R4, #16
        CALL     s_recv
        LDR      R2, #V_RL
        ST       R2, R3
        LDR      R4, #16
        CALL     s_recv
        LDR      R2, #V_RH
        ST       R2, R3
        LDR      R4, #1
        CALL     s_recv            ; parity
        LDR      R2, #V_RL
        LD       R1, R2
        LDR      R2, #V_RH
        LD       R2, R2
        XOR      R1, R1, R2
        XOR      R1, R1, R3        ; the 32 bits and the parity bit, folded
        SWAP     R2, R1
        XOR      R1, R1, R2
        SHR      R2, R1, #4
        XOR      R1, R1, R2
        SHR      R2, R1, #2
        XOR      R1, R1, R2
        SHR      R2, R1, #1
        XOR      R1, R1, R2
        LDR      R2, #1
        AND      R1, R1, R2        ; odd = wrong
        ADDS     R1, R1, R1
        JNZ      sr_bad
        CALL     s_tail
        LDR      R2, #V_RL
        LD       R5, R2
        LDR      R2, #V_RH
        LD       R3, R2
        LDR      R1, #0
        JMP      sr_x
sr_bad: CALL     s_tail
        LDR      R1, #1
sr_x:   LDR      R2, #L_READ
        LD       R7, R2
        ADDS     R1, R1, R1        ; Z = 1: good
        RET

; the turnaround back, then 8 idle clocks with SWDIO low. Saves its return address
; in V_T, since it calls set_oe and s_send.
s_tail: LDR      R2, #V_T
        ST       R2, R7
        LDR      R2, #0
        PIN_W    R2
        NOP
        PIN_W    R0
        OR       R1, R0, R6
        CALL     set_oe
        LDR      R3, #0
        LDR      R4, #8
        CALL     s_send
        LDR      R2, #V_T
        LD       R7, R2
        RET

; ---- a find: R0, R6 as the scan left them; V_TDO, V_TDI hold the third and fourth
; pins for JTAG. Reports it and stores it.
hit:    LDR      R2, #L_HIT
        ST       R2, R7
        LDR      R2, #V_N
        LD       R1, R2
        INCR     R1
        ST       R2, R1
        LDR      R1, #V_KIND       ; kind, a, b ...
        LDR      R2, #V_T
        ST       R2, R1
h_f:    LDR      R2, #V_T
        LD       R1, R2
        LD       R1, R1
        CALL     uo_byte
        LDR      R2, #V_T
        LD       R1, R2
        INCR     R1
        ST       R2, R1
        LDR      R2, #(V_B + 1)
        SUBS     R2, R1, R2
        JNZ      h_f
        LDR      R2, #V_KIND       ; ... tdo, tdi if it is JTAG ...
        LD       R1, R2
        LDR      R2, #'J'
        SUBS     R2, R1, R2
        JNZ      h_id
        LDR      R2, #V_TDO
        LD       R1, R2
        CALL     uo_byte
        LDR      R2, #V_TDI
        LD       R1, R2
        CALL     uo_byte
h_id:   LDR      R2, #V_IDL        ; ... the id, least significant byte first
        LD       R3, R2
        MOV      R1, R3
        CALL     uo_byte           ; (it keeps R3)
        SWAP     R1, R3
        CALL     uo_byte
        LDR      R2, #V_IDH
        LD       R3, R2
        MOV      R1, R3
        CALL     uo_byte
        SWAP     R1, R3
        CALL     uo_byte
        LDR      R2, #V_HIT        ; and in data RAM, while it fits
        LD       R3, R2
        LDR      R4, #HITSEND
        SUBS     R4, R3, R4
        JP       h_x
        LDR      R2, #V_KIND
        LD       R1, R2
        SWAP     R1, R1
        LDR      R2, #V_A
        LD       R2, R2
        OR       R1, R1, R2
        ST       R3, R1
        INCR     R3
        LDR      R2, #V_B
        LD       R1, R2
        SWAP     R1, R1
        LDR      R2, #V_TDO
        LD       R2, R2
        OR       R1, R1, R2
        ST       R3, R1
        INCR     R3
        LDR      R2, #V_IDL
        LD       R1, R2
        ST       R3, R1
        INCR     R3
        LDR      R2, #V_IDH
        LD       R1, R2
        ST       R3, R1
        INCR     R3
        LDR      R2, #V_HIT
        ST       R2, R3
h_x:    LDR      R2, #L_HIT
        LD       R7, R2
        RET

; R1[7:0] -> 8N1 on uo_out[0], on the cycle counter. Uses R1, R2, R4, R5.
uo_byte: LDR     R2, #0xFF
        AND      R1, R1, R2
        ADDS     R1, R1, R1
        LDI      R2, 0x200
        OR       R1, R1, R2        ; start bit 0, data, stop bit
        LDR      R4, #10
        CNT_R    R5
ub:     LDR      R2, #1
        AND      R2, R1, R2
        OUT_W    R2
        SHR      R1, R1, #1
        LDR      R2, #UBIT
        ADDS     R5, R5, R2
        WAIT_CMP R5
        DECR     R4
        JNZ      ub
        RET

; ---- JTAG: R0 = TCK, R6 = TMS. Two scans of the data register the TAP resets to.
try_jtag: LDR    R2, #L_TRY
        ST       R2, R7
        LDR      R1, #0
        PIN_W    R1
        LDR      R2, #V_MTDI
        ST       R2, R1
        OR       R1, R0, R6
        CALL     set_oe
        LDR      R5, #S1
        CALL     j_scan
        LDR      R5, #S2
        CALL     j_scan
        LDR      R1, #0
        LDR      R2, #V_C
        ST       R2, R1
tj_p:   LDR      R2, #V_C
        LD       R1, R2
        CALL     maskof
        OR       R2, R0, R6
        AND      R2, R2, R1
        ADDS     R2, R2, R2
        JNZ      tj_n              ; TCK or TMS itself
        CALL     j_word            ; R1 = the pin's mask
        JNZ      tj_n              ; it differs between the scans
        LDR      R2, #V_IDL
        LD       R3, R2
        LDR      R2, #1
        AND      R2, R3, R2        ; bit 0: an IDCODE has it set
        ADDS     R2, R2, R2
        JZ       tj_n
        LDR      R2, #V_IDH        ; all ones is a pull-up
        LD       R2, R2
        AND      R3, R3, R2
        LDI      R2, 0xFFFF
        SUBS     R3, R3, R2
        JZ       tj_n
        LDR      R2, #V_C          ; this is TDO: look for TDI, then report
        LD       R1, R2
        LDR      R2, #V_TDO
        ST       R2, R1
        CALL     j_tdi
        CALL     hit
        JMP      tj_x
tj_n:   LDR      R2, #V_C
        LD       R1, R2
        INCR     R1
        ST       R2, R1
        LDR      R2, #8
        SUBS     R2, R1, R2
        JNZ      tj_p
tj_x:   LDR      R2, #L_TRY
        LD       R7, R2
        RET

; R3 = TMS bits, R4 = how many, LSB first: one TCK pulse each, with V_MTDI high.
; Uses R1, R2.
j_tms:  LDR      R2, #V_MTDI
        LD       R1, R2
jt1:    LDR      R2, #1
        AND      R2, R3, R2
        ADDS     R2, R2, R2
        JZ       jt0
        OR       R2, R1, R6
        JMP      jt2
jt0:    MOV      R2, R1
jt2:    PIN_W    R2                ; TMS and TDI change while TCK is low
        NOP
        OR       R2, R2, R0
        PIN_W    R2                ; TCK rises
        NOP
        XOR      R2, R2, R0
        PIN_W    R2                ; TCK falls
        SHR      R3, R3, #1
        DECR     R4
        JNZ      jt1
        RET

; R5 = where to put 32 samples of all the pins: reset, Shift-DR, read, reset
j_scan: LDR      R2, #L_JSCAN
        ST       R2, R7
        LDR      R2, #V_T
        ST       R2, R5
        LDR      R3, #0xBF         ; 6 x 1, then 0, 1, 0, 0: reset to Shift-DR
        LDR      R4, #10
        CALL     j_tms
        LDR      R2, #V_T
        LD       R5, R2
        LDR      R4, #32
        LDR      R3, #0
js1:    PIN_R    R1                ; TDO, before this clock
        ST       R5, R1
        INCR     R5
        PIN_W    R3                ; TMS low
        NOP
        PIN_W    R0                ; TCK rises
        NOP
        PIN_W    R3                ; TCK falls
        DECR     R4
        JNZ      js1
        LDR      R3, #0x3F
        LDR      R4, #6
        CALL     j_tms             ; back to reset
        LDR      R2, #L_JSCAN
        LD       R7, R2
        RET

; R1 = a pin's mask. Its 32 bits from S1 -> V_IDL, V_IDH; Z = 1 if S2 holds the same.
; R7 is saved, since R7 is used for the mask here.
j_word: LDR      R2, #L_JWORD
        ST       R2, R7
        MOV      R7, R1
        LDR      R5, #S1
        LDR      R4, #V_IDL        ; the low half, then the high
jw2:    LDR      R3, #0
        LDR      R1, #1
jw1:    LD       R2, R5
        AND      R2, R2, R7
        ADDS     R2, R2, R2
        JZ       jw0
        OR       R3, R3, R1
jw0:    INCR     R5
        ADDS     R1, R1, R1
        JNZ      jw1
        ST       R4, R3
        INCR     R4
        LDR      R2, #(V_IDH + 1)
        SUBS     R2, R4, R2
        JNZ      jw2
        LDR      R5, #S1           ; R3 = differences in this pin
        LDR      R3, #0
        LDR      R4, #32
jw4:    LD       R2, R5
        LDR      R1, #(S2 - S1)
        ADDS     R1, R5, R1
        LD       R1, R1
        XOR      R2, R2, R1
        OR       R3, R3, R2
        INCR     R5
        DECR     R4
        JNZ      jw4
        AND      R3, R3, R7
        LDR      R2, #L_JWORD
        LD       R7, R2
        ADDS     R3, R3, R3        ; Z = 1: none (R3 <= 0x80)
        RET

; V_TDO = the TDO pin. Each pin left, as TDI -> V_TDI (0xFF: none found)
j_tdi:  LDR      R2, #L_JTDI
        ST       R2, R7
        LDR      R1, #0xFF
        LDR      R2, #V_TDI
        ST       R2, R1
        LDR      R1, #0
        LDR      R2, #V_C
        ST       R2, R1
jd_p:   LDR      R2, #V_C
        LD       R1, R2
        LDR      R2, #V_TDO
        LD       R2, R2
        SUBS     R2, R1, R2
        JZ       jd_n              ; TDO itself
        CALL     maskof
        OR       R2, R0, R6
        AND      R2, R2, R1
        ADDS     R2, R2, R2
        JNZ      jd_n              ; TCK or TMS
        LDR      R2, #V_MTDI
        ST       R2, R1
        OR       R1, R1, R0
        OR       R1, R1, R6
        CALL     set_oe
        CALL     j_try
        JNZ      jd_n
        LDR      R2, #V_C
        LD       R1, R2
        LDR      R2, #V_TDI
        ST       R2, R1
        JMP      jd_x
jd_n:   LDR      R2, #V_C
        LD       R1, R2
        INCR     R1
        ST       R2, R1
        LDR      R2, #8
        SUBS     R2, R1, R2
        JNZ      jd_p
jd_x:   LDR      R1, #0
        LDR      R2, #V_MTDI
        ST       R2, R1
        OR       R1, R0, R6
        CALL     set_oe
        LDR      R3, #0x3F
        LDR      R4, #6
        CALL     j_tms             ; back to reset
        LDR      R2, #L_JTDI
        LD       R7, R2
        RET

; The candidate is V_MTDI, held high: ones into IR select BYPASS, then a pattern
; sent through DR must come back on TDO up to 8 clocks late. Z = 1: it did. R6 holds
; TDO's mask during the shift; it is rebuilt from V_B at the end.
.equ PATTERN, 0xB2D3
j_try:  LDR      R2, #L_JTRY
        ST       R2, R7
        LDR      R3, #0x3F
        LDR      R4, #6
        CALL     j_tms             ; reset
        LDR      R3, #0x06         ; 0, 1, 1, 0, 0: to Shift-IR
        LDR      R4, #5
        CALL     j_tms
        LDR      R3, #0
        LDR      R4, #32
        CALL     j_tms             ; TDI high: ones into IR
        LDR      R3, #0x0B         ; 1, 1, 0, 1, 0, 0: Exit1-IR, Update-IR, Run-
        LDR      R4, #6            ; Test, Select-DR, Capture-DR, Shift-DR
        CALL     j_tms
        LDR      R2, #V_TDO
        LD       R1, R2
        CALL     maskof
        MOV      R6, R1            ; R6 = TDO's mask
        LDR      R2, #V_MTDI
        LD       R7, R2            ; R7 = TDI's mask
        LDI      R3, PATTERN
        LDR      R4, #16
        LDR      R1, #0            ; R1 = TDO bits read, LSB first
        LDR      R5, #1            ; their weight
jy1:    PIN_R    R2                ; TDO, before this clock
        AND      R2, R2, R6
        ADDS     R2, R2, R2
        JZ       jy2
        OR       R1, R1, R5
jy2:    ADDS     R5, R5, R5
        LDR      R2, #1
        AND      R2, R3, R2
        ADDS     R2, R2, R2
        JZ       jy3
        MOV      R2, R7
        JMP      jy4
jy3:    LDR      R2, #0
jy4:    PIN_W    R2                ; TDI changes, TCK and TMS low
        NOP
        OR       R2, R2, R0
        PIN_W    R2                ; TCK rises
        NOP
        XOR      R2, R2, R0
        PIN_W    R2                ; TCK falls
        SHR      R3, R3, #1
        DECR     R4
        JNZ      jy1
        LDI      R3, PATTERN       ; the bits read, against the pattern d clocks late
        LDI      R5, 0x7FFF
        LDR      R4, #8
jc1:    SHR      R1, R1, #1
        XOR      R2, R1, R3
        AND      R2, R2, R5
        ADDS     R2, R2, R2
        JZ       jc_ok
        SHR      R5, R5, #1
        DECR     R4
        JNZ      jc1
        LDR      R3, #1            ; no
        JMP      jc_x
jc_ok:  LDR      R3, #0
jc_x:   LDR      R2, #V_B
        LD       R1, R2
        CALL     maskof
        MOV      R6, R1            ; TMS's mask again
        LDR      R2, #L_JTRY
        LD       R7, R2
        ADDS     R3, R3, R3        ; Z = 1: found
        RET

; ---- listen: nothing driven. Record the eight pins for WINDOW cycles, work out
; which moved and each one's shortest run, then try those runs as UART bit times.
; Pins that moved are taken off the list of wires to drive.
listen: LDR      R2, #L_LIS
        ST       R2, R7
        LDR      R3, #U_LAST
        LDR      R4, #16
        LDR      R1, #0
l0:     ST       R3, R1
        INCR     R3
        DECR     R4
        JNZ      l0
        LDI      R1, RING          ; clear the ring: unwritten words have 0 ticks
        MEM_A    R1
        LDR      R2, #RINGSZ
        LDR      R1, #0
l1:     MEM_W    R1
        DECR     R2
        JNZ      l1
        LDI      R1, RING | RINGN << CAP_N
        CAP_BUF  R1
        LDR      R1, #1
        CAP_DIV  R1
        LDI      R1, 0x0100        ; a trigger that can never hold
        CAP_TRIG R1
        LDI      R1, CAP_CHG | CAP_ARM
        CAP_CTL  R1
        PIN_R    R0                ; R0 = the pins as the listen starts
        LDR      R6, #0            ; R6 = pins that moved
        CNT_R    R4
        LDI      R5, WINDOW
lq:     PIN_R    R1
        XOR      R1, R1, R0
        OR       R6, R6, R1
        CNT_R    R2
        SUBS     R2, R2, R4        ; time so far
        SUBS     R2, R2, R5
        JN       lq
        LDR      R2, #V_ACT
        ST       R2, R6
        LDR      R1, #0
        CAP_TRIG R1                ; it holds now: the engine stops at the next word
l2:     CAP_STAT R1
        LDI      R2, CS_DONE
        AND      R2, R1, R2
        ADDS     R2, R2, R2
        JZ       l2
        LDI      R2, 0x3FF
        AND      R3, R1, R2        ; R3 = the oldest word, once it has wrapped
        LDR      R1, #RINGSZ
        LDR      R2, #V_T
        ST       R2, R1            ; words to read
        LDR      R0, #1            ; R0 = time (0 in U_LAST means not yet)
        LDI      R6, 0x100         ; R6 = the last pins; 0x100: no word yet
lw:     LD       R1, R3
        INCR     R3
        LDR      R2, #RINGSZ - 1
        AND      R2, R3, R2
        ADDS     R2, R2, R2        ; Z: ran off the block's end
        JNZ      lw1
        LDR      R2, #RINGSZ
        SUBS     R3, R3, R2
lw1:    LDR      R2, #0xFF
        AND      R2, R1, R2        ; ticks
        ADDS     R5, R2, R2
        JZ       lw_n              ; never written
        ADDS     R0, R0, R2
        SWAP     R1, R1
        LDR      R2, #0xFF
        AND      R1, R1, R2        ; pins
        LDI      R2, 0x100
        SUBS     R2, R6, R2
        JNZ      lw2
        MOV      R6, R1            ; the first word
        JMP      lw_n
lw2:    XOR      R5, R1, R6        ; R5 = the pins that changed
        MOV      R6, R1
        ADDS     R1, R5, R5
        JZ       lw_n
        LDR      R7, #0            ; R7 = pin
lp:     LDR      R1, #1
        AND      R1, R5, R1
        ADDS     R1, R1, R1
        JZ       lp_n
        LDR      R1, #U_LAST
        ADDS     R1, R1, R7
        LD       R2, R1            ; when it last changed
        ST       R1, R0
        INCR     R2
        DECR     R2                ; Z: never
        JZ       lp_n
        SUBS     R2, R0, R2        ; this run
        LDR      R1, #U_MIN
        ADDS     R1, R1, R7
        LD       R4, R1
        INCR     R4
        DECR     R4                ; Z: no run yet
        JZ       lp_set
        SUBS     R4, R2, R4
        JP       lp_n
lp_set: ST       R1, R2
lp_n:   SHR      R5, R5, #1
        INCR     R7
        ADDS     R1, R5, R5
        JNZ      lp
lw_n:   LDR      R2, #V_T
        LD       R4, R2
        DECR     R4
        ST       R2, R4
        JNZ      lw

; ---- each pin's shortest run as a UART bit time: the RX engine reads what follows
        LDR      R1, #0
        LDR      R2, #V_C
        ST       R2, R1
lv:     LDR      R2, #V_C
        LD       R3, R2
        LDR      R1, #U_MIN
        ADDS     R1, R1, R3
        LD       R4, R1            ; R4 = the bit time to try
        LDR      R1, #MINBIT
        SUBS     R1, R4, R1
        JN       lv_n              ; none, or a glitch
        LDR      R2, #V_BIT
        ST       R2, R4
        RX_DIV   R4
        LDI      R1, (RX_EN | RX_CHAR | RX_LSB | (9 - 1) << 8)
        OR       R1, R1, R3
        RX_CFG   R1                ; 8 data bits + the stop bit
        SHL      R1, R4, #5
        TMO      R1                ; a frame within 32 bits, or the line went quiet
        LDR      R5, #0            ; R5 = good frames
        LDR      R6, #0            ; R6 = bad ones
        LDR      R0, #0            ; R0 = 1: more than one value
        LDR      R4, #12           ; frames to look at
lr:     RX_POP   R1
        JZ       lr_e              ; timed out
        LDR      R2, #9
        SUBS     R2, R4, R2
        JP       lr_c              ; it started mid-stream: the first 4 don't count
        LDI      R2, 0x100
        AND      R2, R1, R2
        ADDS     R2, R2, R2
        JNZ      lr_g
        INCR     R6
        JMP      lr_c
lr_g:   INCR     R5
        LDR      R2, #1
        SUBS     R2, R5, R2
        JNZ      lr_h
        MOV      R7, R1            ; R7 = the first value
        JMP      lr_c
lr_h:   SUBS     R2, R7, R1
        JZ       lr_c
        LDR      R0, #1
lr_c:   DECR     R4
        JNZ      lr
lr_e:   LDR      R1, #0
        TMO      R1
        RX_CFG   R1
        INCR     R6
        DECR     R6                ; Z: no bad frame
        JNZ      lv_n
        LDR      R1, #2
        SUBS     R1, R5, R1
        JN       lv_n
        ADDS     R0, R0, R0
        JZ       lv_n
        LDR      R1, #'U'          ; a UART
        CALL     uo_byte
        LDR      R2, #V_C
        LD       R1, R2
        CALL     uo_byte
        LDR      R2, #V_BIT
        LD       R3, R2
        MOV      R1, R3
        CALL     uo_byte
        SWAP     R1, R3
        CALL     uo_byte
lv_n:   LDR      R2, #V_C
        LD       R1, R2
        INCR     R1
        ST       R2, R1
        LDR      R2, #8
        SUBS     R2, R1, R2
        JNZ      lv

; ---- pins that moved are being driven by something: leave them alone
        LDR      R2, #V_ACT
        LD       R1, R2
        LDR      R2, #0xFF
        XOR      R1, R1, R2
        LDR      R2, #V_PINS
        LD       R3, R2
        AND      R3, R3, R1
        ST       R2, R3
        LDR      R2, #L_LIS
        LD       R7, R2
        RET
