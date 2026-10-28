; HIL helper: stream SRAM words [START, START+COUNT) out on uo_out, low byte
; then high byte. uio_out[7] toggles once per byte, and the byte is on uo_out
; in the cycle the toggle appears (the PS watches every cycle in step mode).
; The PS patches the three immediates (START's low and high byte, COUNT <=
; 255) before loading this at 128, so it can read back the RAM a test
; firmware left behind, in either bank.
.equ START, 0
.equ START_HI, 0
.equ COUNT, 0

start:
        LDR      R3, #START
        LDH      R3, #START_HI
        LDR      R4, #COUNT
        ADDS     R4, R4, R0         ; Z=1 iff COUNT == 0 (R0 = 0 after reset)
        JZ       done
loop:
        LD       R1, R3
        OUT_W    R1                 ; low byte on uo_out from the next cycle
        PIN_TGL  0x80               ; ... and the strobe one cycle after it
        SWAP     R1, R1
        OUT_W    R1                 ; high byte
        PIN_TGL  0x80
        INCR     R3
        DECR     R4
        JNZ      loop
done:   JMP      done
