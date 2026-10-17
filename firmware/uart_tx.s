; UART transmitter, 8N1, drift-free: every bit edge is scheduled on an
; absolute cycle-counter target (WAIT_CMP), so there is no cumulative error
; however long the message is.
;   TX pin : uio[0]
;   input  : zero-terminated byte string, one byte per word, data RAM 0..
;   done   : uo_out = DONE
;   BIT    : cycles per bit (104 = 115200 baud at 12 MHz)
.include "msf.inc"
.equ BIT, 104

start:
        LDR      R0, #1             ; bit mask
        LDI      R1, 0x200          ; stop bit (bit 9)
        PIN_WI   0x01               ; line idles high
        OE_SET   0x01
        LDI      R6, BIT
        LDR      R2, #0             ; string pointer
        CNT_R    R5
        ADDS     R5, R5, R6         ; first edge one bit-time from now
next:
        LD       R3, R2
        ADDS     R7, R3, R3         ; R7 = byte << 1, Z=1 at the terminator
        JZ       done
        OR       R3, R7, R1         ; frame = stop | data << 1 | start(0)
        LDR      R4, #10
bit:
        AND      R7, R3, R0
        WAIT_CMP R5
        PIN_W    R7                 ; on the pad exactly 2 cycles after the target
        SHR      R3, R3, #1
        ADDS     R5, R5, R6
        DECR     R4
        JNZ      bit
        INCR     R2
        JMP      next
done:
        UO_WI    DONE
halt:   JMP      halt
