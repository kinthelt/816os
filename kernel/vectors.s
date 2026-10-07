; vectors.s - interrupt vectors (W65C816S datasheet Table 5-3)
;
; Native-mode vectors are at $FFE4-$FFEF, emulation-mode ones at $FFF4-$FFFF.
; The CPU only runs in emulation mode for the first few instructions after
; reset, with interrupts disabled, so the emulation vectors other than RESET
; just return.

.p816
.import reset, irq_native, cop_native, brk_native

.segment "CODE"

unused_vector:
        rti

.segment "VECTORS"              ; starts at $FFE0

        .word 0                 ; $FFE0 reserved
        .word 0                 ; $FFE2 reserved
        .word cop_native        ; $FFE4 COP
        .word brk_native        ; $FFE6 BRK
        .word unused_vector     ; $FFE8 ABORT
        .word unused_vector     ; $FFEA NMI
        .word 0                 ; $FFEC reserved
        .word irq_native        ; $FFEE IRQ

        .word 0                 ; $FFF0 reserved
        .word 0                 ; $FFF2 reserved
        .word unused_vector     ; $FFF4 COP
        .word 0                 ; $FFF6 reserved
        .word unused_vector     ; $FFF8 ABORT
        .word unused_vector     ; $FFFA NMI
        .word reset             ; $FFFC RESET
        .word unused_vector     ; $FFFE IRQ/BRK
