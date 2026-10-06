; torture.s - checks that preemption never disturbs a process's registers.
;
; Loads distinctive values (different per slot) into A, B, X, Y, then checks
; them over and over while timer and ACIA interrupts switch processes
; underneath it. Also checks D, the data bank register, and that the 8-bit
; register widths survive. Any mismatch executes BRK, which the kernel
; records in brk_count.
;
; After ITERATIONS rounds it prints "done <slot>" and exits.

.p816
.include "os816.inc"
.include "macros.inc"

ITERATIONS = 40
CHECKS     = 200

.segment "ZEROPAGE"
iters:  .res 2          ; first, so tests can find it at offset 0
pid:    .res 2
va:     .res 2
vx:     .res 2
vy:     .res 2
dpval:  .res 2
cnt:    .res 2
va8:    .res 1
vb8:    .res 1
vx8:    .res 1
vy8:    .res 1

.segment "CODE"
        .a16
        .i16
start:
        jsl K_GETPID
        sta pid
        sta marker              ; absolute: lands in this process's own bank
        stz iters
        tdc
        sta dpval

        lda pid                 ; per-slot patterns
        xba
        ora pid                 ; pid * $0101
        eor #$A55A
        sta va
        eor #$FFFF
        sta vx
        eor #$3C3C
        sta vy
        A8
        lda va
        sta va8
        lda vx
        sta vb8
        lda vy
        sta vx8
        lda va+1
        sta vy8
        A16

round:
        ; 16-bit registers
        lda #CHECKS
        sta cnt
        ldx vx
        ldy vy
        lda va
@check16:
        cmp va
        bne fail
        cpx vx
        bne fail
        cpy vy
        bne fail
        pha
        tdc
        cmp dpval               ; Direct Register
        bne fail
        lda marker              ; data bank register
        cmp pid
        bne fail
        pla
        dec cnt
        bne @check16

        ; 8-bit registers, with a value parked in B
        lda #CHECKS
        sta cnt
        AXY8
        lda vb8
        xba
        lda va8
        ldx vx8
        ldy vy8
@check8:
        cmp va8
        bne fail
        xba
        cmp vb8
        bne fail
        xba
        cpx vx8
        bne fail
        cpy vy8
        bne fail
        dec cnt
        bne @check8
        AXY16

        inc iters
        lda iters
        cmp #ITERATIONS
        bne round

        jsl K_CON_LOCK
        lda #msg_done
        jsl K_PUTS
        lda pid
        clc
        adc #'0'
        jsl K_PUTC
        lda #msg_crlf
        jsl K_PUTS
        jsl K_CON_UNLOCK
        jsl K_EXIT

fail:
        brk $EE

.segment "RODATA"
msg_done:   .asciiz "done "
msg_crlf:   .byte 13, 10, 0

.segment "DATA"
marker:     .word $FFFF
