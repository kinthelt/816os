; quitter.s - prints a line and returns from its entry point with RTL,
; which should end the process.

.p816
.include "os816.inc"

.segment "CODE"
        .a16
        .i16
start:
        jsl K_CON_LOCK
        lda #msg
        jsl K_PUTS
        jsl K_CON_UNLOCK
        rtl

.segment "RODATA"
msg:    .byte "bye", 13, 10, 0
