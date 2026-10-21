; Queue of 16-bit words in the shared data RAM, from one hardware thread to
; the other. A slot holds 0 while it is free: the producer writes a word
; into the next slot if it is free, the consumer takes a word from its next
; slot and writes the 0 back. A slot has one writer at a time (the producer
; while it is 0, the consumer while it is not), so there is nothing to lock
; and no index to publish, and a thread waiting on the queue spends only its
; own cycle slots: the other thread's timing does not change.
;   ring_clear: zero the slots (call once before starting the other thread;
;               the SRAM powers up with anything in it). Clobbers R4, R5.
;   put : R1 = word, never 0 or 0x8000; R6 = producer's slot (RING at the
;         start). A full queue drops the word and counts it in RING_DROP.
;         Clobbers R4, R5, R7.
;   get : R1 = word and Z = 0, or Z = 1 if the queue is empty; R6 =
;         consumer's slot (RING at the start). Clobbers R4, R7.
; Defaults below; .equ them again after the .include to move the queue.
.equ RING,      64            ; slots at data RAM [64..127]
.equ RING_END,  128
.equ RING_DROP, 63            ; words dropped (written by the producer only)

ring_clear:
        LDR      R4, #RING_DROP
        MEM_A    R4                ; MEM_W writes there and moves on by itself
        LDR      R5, #(RING_END - RING_DROP)
        LDR      R4, #0
rc:     MEM_W    R4
        DECR     R5
        JNZ      rc
        RET

put:    LD       R4, R6
        ADDS     R4, R4, R4        ; Z = slot free
        JNZ      put_f
        ST       R6, R1
        INCR     R6
        LDR      R4, #RING_END
        SUBS     R4, R6, R4
        JNZ      put_r
        LDR      R6, #RING
put_r:  RET
put_f:  LDR      R4, #RING_DROP
        LD       R5, R4
        INCR     R5
        ST       R4, R5
        RET

get:    LD       R1, R6
        ADDS     R4, R1, R1        ; Z = empty
        JZ       get_r
        LDR      R4, #0
        ST       R6, R4            ; the slot is free again
        INCR     R6
        LDR      R4, #RING_END
        SUBS     R4, R6, R4
        JNZ      get_n
        LDR      R6, #RING
get_n:  LDR      R4, #1
        ADDS     R4, R4, R4        ; Z = 0: got a word
get_r:  RET
