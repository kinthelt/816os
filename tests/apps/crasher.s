; crasher.s - takes the console lock, prints, then hits BRK while still
; holding it. The kernel should kill it and release the lock.

.p816
.include "os816.inc"

.segment "CODE"
        .a16
        .i16
start:
        jsl K_CON_LOCK
        lda #msg
        jsl K_PUTS
crash:
        brk $42

.segment "RODATA"
msg:    .byte "crashing", 13, 10, 0
