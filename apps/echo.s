; echo.s - echoes received characters back, turning CR into CR LF.

.p816
.include "os816.inc"
.include "macros.inc"

.segment "CODE"
        .a16
        .i16
start:
        jsl K_GETC
        bcc @idle
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
