; msfcore UART-RX bootloader (ROM-resident, IMEM words 0..127)
; Power-up: ui_in[0] checked via IN_RD
;   ui_in[0] = 1: direct SRAM boot (JMP 128)
;   ui_in[0] = 0: UART RX bootloader (stream 16-bit words into SRAM)
; Termination: 0xFFFF word ends the stream -> JMP 128
; 115200 baud, 8N1, LSB-first, idle high
; Pin 1 = RX (uio_in[1])
;
; REG MAP: R0=RX mask(2), R1=temp, R2=low byte, R3=counter,
; R4=delay, R5=temp, R6=high byte, R7=assembled word
; INTEGRATION: test/assembler.py -> src/tt_um_msf_bootrom_data.v

; --- Boot select ---
; ui_in[0] = 0: UART RX bootloader (stream firmware into SRAM)
; ui_in[0] = 1: direct SRAM boot (jump to 128)
IN_RD    R1
LDR      R2, #1
AND      R3, R1, R2      ; R3 = ui_in[0]
ADDS     R3, R3, R0       ; Z=1 if ui_in[0]=0
JZ       uart_boot        ; ui_in[0]=0 (Z=1) -> UART bootloader
JMP      128              ; ui_in[0]=1 (Z=0) -> direct SRAM boot

; --- UART RX Bootloader ---
uart_boot:
LDR      R0, #2
LDR      R7, #0

rx_word:
; --- Receive low byte into R2 ---
LDR      R2, #0

; Re-synchronize: poll until the RX line is idle HIGH, so the WAIT_EDGE
; below latches this byte's start-bit falling edge (not a mid-byte edge).
poll_idle_lo:
PIN_R    R5
AND      R5, R5, R0
SUBS     R5, R5, R0      ; Z=1 if pin1 == 2 (HIGH)
JNZ      poll_idle_lo    ; keep polling while LOW

WAIT_EDGE 1
LDR      R4, #50
dly_15_lo:
DECR     R4
JNZ      dly_15_lo

; Sample-first (matches the working echo test): read the bit now, then
; delay to the next bit center. Delaying first would shift every sample by
; one bit (sampling bit 1 instead of bit 0).
LDR      R3, #8
rx_bit_lo:
PIN_R    R5
AND      R5, R5, R0
SHR      R5, R5, #1
SHL      R5, R5, #7
SHR      R2, R2, #1
OR       R2, R2, R5
LDR      R4, #31
dly_bit_lo:
DECR     R4
JNZ      dly_bit_lo
DECR     R3
JNZ      rx_bit_lo

; Skip the stop bit
LDR      R4, #30
dly_stop_lo:
DECR     R4
JNZ      dly_stop_lo

; --- Receive high byte into R6 ---
; Re-synchronize on idle before latching the high byte's start bit.
LDR      R6, #0
poll_idle_hi:
PIN_R    R5
AND      R5, R5, R0
SUBS     R5, R5, R0      ; Z=1 if pin1 == 2 (HIGH)
JNZ      poll_idle_hi    ; keep polling while LOW

WAIT_EDGE 1
LDR      R4, #50
dly_15_hi:
DECR     R4
JNZ      dly_15_hi

LDR      R3, #8
rx_bit_hi:
PIN_R    R5
AND      R5, R5, R0
SHR      R5, R5, #1
SHL      R5, R5, #7
SHR      R6, R6, #1
OR       R6, R6, R5
LDR      R4, #31
dly_bit_hi:
DECR     R4
JNZ      dly_bit_hi
DECR     R3
JNZ      rx_bit_hi

; Skip the stop bit
LDR      R4, #30
dly_stop_hi:
DECR     R4
JNZ      dly_stop_hi

; Line idles HIGH after the stop bit, so WAIT_EDGE naturally waits for the
; next word's start bit (next falling edge).
; Check for termination: 0x0000 = end of stream marker.
; word == 0 iff low byte (R2) == 0 AND high byte (R6) == 0.
; (2*X == 0 mod 2^16 iff X == 0 for X in 0..255, so ADDS X,X is a clean zero test.)
ADDS     R5, R2, R2      ; Z=1 if R2 == 0
JNZ      not_done
ADDS     R5, R6, R6      ; Z=1 if R6 == 0
JNZ      not_done
JMP      128             ; 0x0000 terminator -> hand off to firmware

not_done:
; Assemble 16-bit word: R7 = (R6 << 8) | R2
; SHL only supports 3-bit shift (0-7), so use 8 single-bit shifts:
LDR      R4, #8
sh8:
SHL      R6, R6, #1
DECR     R4
JNZ      sh8
OR       R7, R6, R2
MEM_W    R7              ; Store word into SRAM (auto-increment 128..511)
JMP      rx_word
