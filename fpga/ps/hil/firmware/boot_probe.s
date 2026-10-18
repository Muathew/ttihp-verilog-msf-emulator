; HIL boot probe (the FW of test/test_boot.py): once started it drives 0x42
; on uio[7:1] and stops, so a W + G load is visible at the pins.
start:
        OE_SET   0xFE
        LDR      R1, #0x42
        PIN_W    R1
done:   JMP      done
