; sleeper.s - waits a second without reading input, then exits. While it
; has the keyboard, typed characters pile up in the receive ring.

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
        adc #TICK_HZ
        sta wake
@wait:
        jsl K_YIELD
        jsl K_TICKS
        sec
        sbc wake
        bmi @wait
        rtl
