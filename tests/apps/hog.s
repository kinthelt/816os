; hog.s - takes the console lock, prints, and never lets go. Pausing it
; with Ctrl-Z must release the lock, or the shell could never print again.

.p816
.include "os816.inc"

.segment "CODE"
        .a16
        .i16
start:
        jsl K_CON_LOCK
        lda #msg
        jsl K_PUTS
@spin:
        jsl K_YIELD
        bra @spin

.segment "RODATA"
msg:    .byte "hogging", 13, 10, 0
