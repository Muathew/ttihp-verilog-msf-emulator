; Board smoke test (Muath): LED0 (uo_out[0]) toggles every 6,000,000 cycles,
; i.e. blinks at 1 Hz with the 12 MHz pl_clk0. Needs nothing on PMOD JA.
;   python fpga/ps/loader/generate_image.py fpga/ps/loader/blink.s
start:
        UO_WI    0x00               ; clear the boot ROM's status LEDs
toggle:
        UO_TGL   0x01
        NOP                         ; 3 NOPs trim the period to exactly 6,000,000
        NOP
        NOP
        LDR      R2, #81            ; outer loop count
outer:
        LDR      R1, #0x72
        LDH      R1, #0x60          ; R1 = 0x6072 = 24690
inner:
        DECR     R1
        JNZ      inner
        DECR     R2
        JNZ      outer
        JMP      toggle
