; badload.s - runs in the foreground and calls K_LOAD with addresses outside
; banks 1-7: in bank 0, in bank 8 (an alias of bank 0) and in bank $80. The
; service must refuse each one at once, without starting a transfer.

.p816
.include "os816.inc"

.segment "CODE"
        .a16
        .i16
start:
        lda #where_0
        jsr try
        lda #where_8
        jsr try
        lda #where_80
        jsr try
        rtl

try:
        jsl K_LOAD
        bcs @loaded
        lda #msg_refused
        jsl K_PUTS
        rts
@loaded:
        lda #msg_loaded
        jsl K_PUTS
        rts

.segment "RODATA"
msg_refused: .byte "badload refused", 13, 10, 0
msg_loaded:  .byte "badload loaded", 13, 10, 0
where_0:     .byte $02, $00, $00
where_8:     .byte $00, $00, $08
where_80:    .byte $00, $00, $80
