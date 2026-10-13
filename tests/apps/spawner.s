; spawner.s - calls K_EXEC itself. The test puts a program at $07:0000
; first. Starting bank 7 must work; starting its own bank or bank 0 must
; be refused.

.p816
.include "os816.inc"

.segment "CODE"
        .a16
        .i16
start:
        lda #7
        jsl K_EXEC
        bcc @wrong
        jsl K_GETPID            ; its own bank: in use
        jsl K_EXEC
        bcs @wrong
        lda #0                  ; the shell's bank
        jsl K_EXEC
        bcs @wrong
        lda #msg_ok
        bra @say
@wrong:
        lda #msg_wrong
@say:
        jsl K_CON_LOCK          ; the program it started may be printing too
        jsl K_PUTS
        jsl K_CON_UNLOCK
        rtl

.segment "RODATA"
msg_ok:     .byte "spawner ok", 13, 10, 0
msg_wrong:  .byte "spawner wrong", 13, 10, 0
