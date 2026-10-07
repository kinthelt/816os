; echoq.s - like apps/echo.s, but exits when it receives 'q'.

.p816
.include "os816.inc"

.segment "CODE"
        .a16
        .i16
start:
        jsl K_GETC
        bcc @idle
        cmp #'q'
        beq @quit
        jsl K_CON_LOCK
        jsl K_PUTC
        cmp #13
        bne @unlock
        lda #10
        jsl K_PUTC
@unlock:
        jsl K_CON_UNLOCK
        bra start
@idle:
        jsl K_YIELD
        bra start
@quit:
        jsl K_EXIT
