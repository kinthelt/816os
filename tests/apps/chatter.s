; chatter.s - prints a line over and over without taking the console lock.
; Its output must still never land inside an XMODEM transfer.

.p816
.include "os816.inc"

.segment "CODE"
        .a16
        .i16
start:
        lda #msg
        jsl K_PUTS
        jsl K_YIELD
        bra start

.segment "RODATA"
msg:    .byte "chatter", 13, 10, 0
