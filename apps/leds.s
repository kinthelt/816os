; leds.s - bounces a light back and forth across the port B LEDs.
;
; The VIA is in bank 0 and this process's data bank is its own, so it writes
; the port with a long address.

.p816
.include "os816.inc"
.include "hw.inc"
.include "macros.inc"

STEP_TICKS = 20                 ; 100 ms per step

.segment "ZEROPAGE"
pattern: .res 2
wake:    .res 2

.segment "CODE"
        .a16
        .i16
start:
        lda #$0001
        sta pattern
@left:
        jsr show
        asl pattern
        lda pattern
        cmp #$0080
        bne @left
@right:
        jsr show
        lsr pattern
        lda pattern
        cmp #$0001
        bne @right
        bra @left

show:
        A8
        lda pattern
        sta f:VIA_PORTB
        A16
        lda #STEP_TICKS
        ; fall through

; Sleep for A ticks, yielding the processor meanwhile.
sleep:
        sta wake
        jsl K_TICKS
        clc
        adc wake
        sta wake
@wait:
        jsl K_YIELD
        jsl K_TICKS
        sec
        sbc wake
        bmi @wait
        rts
