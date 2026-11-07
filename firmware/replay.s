; Replay: re-drives a recording from a table in SRAM, edge for edge, on the
; absolute cycle counter, so the intervals add no drift however long it runs.
;   table : SRAM TABLE on, one word per change: pins << 8 | ticks. A tick is
;           TICK_SHIFT doublings of a cycle, and the pins change that many ticks
;           after the word before. This is the capture engine's change-mode
;           format, so what it recorded can be played back as it stands. A word
;           with 0 ticks ends the table.
;   OEMASK: the uio pins to drive (define); others stay inputs. Push-pull: OE
;           has no register form, so an open-drain line is not replayed as one.
; When it ends: uo_out = DONE, or LATEFLAG if any edge came late (the table asked
; for intervals this loop cannot keep; tools/msf_capture.py refuses those).
; START: cycles the first word's pins are held before the replay begins.
; The shortest interval it keeps is TICKMIN cycles: msf_timing.py certifies that
; from the @range annotation, which tools/msf_capture.py enforces on the data.
.include "msf.inc"
.equ TABLE, 512
.equ OEMASK, 0xFF
.equ START, 2000
.equ TICK_SHIFT, 0
.equ LATEFLAG, 0xEE
.equ TICKMIN, 16

start:  LDR      R1, #0
        TMO      R1                ; no timeouts
        LDI      R3, TABLE         ; R3 = next word
        LD       R1, R3
        SWAP     R1, R1
        PIN_W    R1                ; the first word's pins, before driving them
        OE_WI    OEMASK
        INCR     R3
        CNT_R    R5
        LDI      R6, START
        ADDS     R5, R5, R6
        WAIT_CMP R5                ; the peer sees the first pins for this long
        LDR      R4, #0xFF         ; R5 = the last change, as the loop expects

loop:   LD       R1, R3            ; R1 = pins << 8 | ticks
        INCR     R3
        AND      R2, R1, R4        ; ticks
        ADDS     R7, R2, R2        ; Z: 0 ticks, the end
        JZ       done
        SHL      R2, R2, #TICK_SHIFT
        ADDS     R5, R5, R2        ; @range R2 TICKMIN 255<<TICK_SHIFT
        WAIT_CMP R5
        SWAP     R1, R1
        PIN_W    R1
        JMP      loop

done:   STAT     R1
        LDI      R2, 0x4000
        AND      R1, R1, R2
        ADDS     R1, R1, R1
        JNZ      late
        UO_WI    DONE
        JMP      halt
late:   UO_WI    LATEFLAG
halt:   JMP      halt
