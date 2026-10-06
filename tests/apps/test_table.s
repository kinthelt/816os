; test_table.s - programs in the test ROM. Slot 6 is left empty so the
; scheduler has a free slot to skip.

.p816
.export app_image, app_size

.segment "APPS"
torture:    .incbin "torture.bin"
torture_end:
echoq:      .incbin "echoq.bin"
echoq_end:
quitter:    .incbin "quitter.bin"
quitter_end:
crasher:    .incbin "crasher.bin"
crasher_end:

TORTURE_SIZE = torture_end - torture

.segment "RODATA"
;                  slot 0   slot 1   slot 2   slot 3  slot 4   slot 5   6
app_image:  .word  torture, torture, torture, echoq,  quitter, crasher, 0
app_size:   .word  TORTURE_SIZE, TORTURE_SIZE, TORTURE_SIZE
            .word  echoq_end - echoq, quitter_end - quitter, crasher_end - crasher, 0
