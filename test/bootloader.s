; msfcore UART-RX bootloader (ROM-resident, IMEM words 0..127)
; STATUS: NEEDS REVIEW -- written in a hurry. The asmw: section uses an
; ADDS R3, R7, R0 placeholder + JNZ is_last; checksum/JMP 128 only fires when
; R7==0; the word count is NOT tracked (the `INCR R6` clobbers the hi byte).
; BEFORE IT CAN RUN: (1) fix the assembler 2-arg R-format unpack bug, (2) rewrite
; the last-word / boundary logic, (3) tune bit timing against the golden model
; (bit time 104-cycle base is nominal; 8N1 LSB-first, idles HIGH).
; REG MAP: R0=RX mask(2), R1=blink cnt, R2=low byte, R3/loop, R4=delay,
; R5=running sum, R6=hi byte, R7=word.  INTEGRATION: test/assembler.py
; -> src/tt_um_msf_bootrom_data.v (initial block); core runs this at PC=0.

        IN_RD R1
        LDR   R2, #1
        AND   R3, R1, R2
        ADDS  R3, R3, R0
        JZ    run_boot
        JMP   128
run_boot:
        OE_SET 1
        LDR   R1, #1
tx_on:
        PIN_W R1
        LDR   R5, #4
b2:     LDR   R4, #10
b1:     DECR  R4
        JNZ   b1
        DECR  R5
        JNZ   b2
        LDR   R1, #0
        PIN_W R1
        LDR   R1, #0
        LDR   R5, #0
BOOT:
        LDR   R0, #2
; word count = 384 (R7+1 max) but imm8 caps single loads, so it is built
; via two-byte loads if needed (M2 note: count logic is PRUNED, see header)
        LDR   R1, #0
        LDR   R7, #0
wait_st:
        PIN_R R4
        AND   R4, R4, R0
        JNZ   wait_st
        LDR   R4, #32
ws2:    DECR  R4
        JNZ   ws2
        PIN_R R4
        AND   R4, R4, R0
        JZ    got_st
        JMP   wait_st
got_st:
        LDR   R3, #8
low:
        LDR   R4, #0
        INCR  R4
low2:   LDR   R4, #89
lb:     DECR  R4
        JNZ   lb
        INCR  R4
        LDR   R4, #34
lb3:    DECR  R4
        JNZ   lb3
        PIN_R R4
        SHL   R2, R2, #1
        AND   R4, R4, R0
        OR    R2, R2, R4
        DECR  R3
        JNZ   low
warps:
        SHL   R2, R2, #2
        SHL   R2, R2, #2
        SHL   R2, R2, #2
        SHL   R2, R2, #2
        LDR   R3, #8
high:
        LDR   R6, #0
hb:     LDR   R4, #89
hb2:    DECR  R4
        JNZ   hb2
        LDR   R4, #34
hb3:    DECR  R4
        JNZ   hb3
        PIN_R R4
        SHL   R6, R6, #1
        AND   R4, R4, R0
        OR    R6, R6, R4
        DECR  R3
        JNZ   high
; -- inter-word gap (stop bit, loop-to-start) --
        LDR   R3, #212
iw:     DECR  R3
        JNZ   iw
asmw:
        MOV   R7, R2
        OR    R7, R7, R6
        ADDS  R3, R7, R0
        JNZ   is_last
        MEM_W R7
        ADDS  R5, R5, R7
        INCR  R6
        JMP   BOOT
is_last:
        JNZ   bad
        JMP   128      ; hand off to firmware (first IMEM word)
        ; NOTE: R7 init + word-boundary test rules are rough (see header)
bad:
        LDR   R1, #5
br:     LDR   R4, #1
        PIN_W R1
        LDR   R4, #40
b3:     DECR  R4
        JNZ   b3
        LDR   R4, #0
        PIN_W R4
        LDR   R4, #40
b4:     DECR  R4
        JNZ   b4
        DECR  R1
        JNZ   br
halt:
        NOP
        JMP   halt
