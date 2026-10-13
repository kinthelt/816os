; xfer.s - calls K_LOAD and K_SAVE itself. Receives a file into $8000 of
; its own bank, then sends 256 bytes from there back. It holds the console
; lock across the send, to check the service copes with a caller that
; already has it.

.p816
.include "os816.inc"
.include "macros.inc"

.segment "CODE"
        .a16
        .i16
start:
        jsl K_GETPID            ; the tables point into this bank
        A8
        sta where+2
        A16
        lda #where
        jsl K_LOAD
        bcc @fail
        jsl K_CON_LOCK
        lda #where
        ldx #count
        jsl K_SAVE
        php
        jsl K_CON_UNLOCK
        plp
        bcc @fail
        lda #msg_ok
        jsl K_PUTS
        rtl
@fail:
        lda #msg_fail
        jsl K_PUTS
        rtl

.segment "RODATA"
msg_ok:     .byte "xfer ok", 13, 10, 0
msg_fail:   .byte "xfer failed", 13, 10, 0

.segment "DATA"
where:      .byte $00, $80, $00 ; $8000 in this bank (bank filled in)
count:      .byte $00, $01, $00 ; 256 bytes
