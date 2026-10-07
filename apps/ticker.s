; ticker.s - prints a numbered line, sleeps, repeats.
;
; Run it more than once and each copy runs from its own bank with its own
; direct page. It finds its bank number with K_GETPID and sleeps
; bank * 100 ticks between lines.

.p816
.include "os816.inc"
.include "macros.inc"

.segment "ZEROPAGE"
pid:    .res 2
count:  .res 2
wake:   .res 2

.segment "CODE"
        .a16
        .i16
start:
        jsl K_GETPID
        sta pid
        stz count

@loop:
        jsl K_CON_LOCK          ; keep the line in one piece
        lda #msg_head
        jsl K_PUTS
        lda pid
        clc
        adc #'0'
        jsl K_PUTC
        lda #msg_mid
        jsl K_PUTS
        lda count
        jsr print_hex16
        lda #msg_crlf
        jsl K_PUTS
        jsl K_CON_UNLOCK

        inc count
        lda pid
        jsr mul100
        jsr sleep
        bra @loop

; A = A * 100
mul100:
        sta wake
        asl a                   ; *2
        clc
        adc wake                ; *3
        asl a
        asl a
        asl a                   ; *24
        clc
        adc wake                ; *25
        asl a
        asl a                   ; *100
        rts

; Sleep for A ticks, yielding the processor meanwhile.
sleep:
        sta wake
        jsl K_TICKS
        clc
        adc wake
        sta wake                ; wake-up tick (wraps at 16 bits)
@wait:
        jsl K_YIELD
        jsl K_TICKS
        sec
        sbc wake
        bmi @wait               ; signed difference: still before wake
        rts

; Print A as four hex digits.
print_hex16:
        pha
        xba
        jsr print_hex8
        pla
print_hex8:
        pha
        lsr a
        lsr a
        lsr a
        lsr a
        jsr print_nibble
        pla
print_nibble:
        and #$000F
        tax
        lda hex_digits,x        ; absolute,X: this bank's copy of the table
        jsl K_PUTC
        rts

.segment "RODATA"
msg_head:   .asciiz "ticker "
msg_mid:    .asciiz ": "
msg_crlf:   .byte 13, 10, 0
hex_digits: .byte "0123456789ABCDEF"
