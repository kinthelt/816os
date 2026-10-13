; bgxfer.s - waits half a second, then calls K_LOAD. Run it in the
; background: the service must refuse, since only the foreground process
; gets the console's input.

.p816
.include "os816.inc"

.segment "ZEROPAGE"
wake:   .res 2

.segment "CODE"
        .a16
        .i16
start:
        jsl K_TICKS
        clc
        adc #TICK_HZ / 2
        sta wake
@wait:
        jsl K_YIELD
        jsl K_TICKS
        sec
        sbc wake
        bmi @wait
        lda #where
        jsl K_LOAD
        bcs @loaded
        lda #msg_refused
        jsl K_PUTS
        rtl
@loaded:
        lda #msg_loaded
        jsl K_PUTS
        rtl

.segment "RODATA"
msg_refused: .byte "bgxfer refused", 13, 10, 0
msg_loaded:  .byte "bgxfer loaded", 13, 10, 0
where:       .byte $00, $90, $07
