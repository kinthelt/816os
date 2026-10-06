; test_table.s - programs in the test ROM, started by the tests through
; the shell's `run` command.

.p816
.export app_count, app_names, app_image, app_size

.segment "APPS"
torture:    .incbin "torture.bin"
torture_end:
echoq:      .incbin "echoq.bin"
echoq_end:
quitter:    .incbin "quitter.bin"
quitter_end:
crasher:    .incbin "crasher.bin"
crasher_end:
hog:        .incbin "hog.bin"
hog_end:

.segment "RODATA"
app_count:  .word 5
app_names:  .word n_torture, n_echoq, n_quitter, n_crasher, n_hog
app_image:  .word torture, echoq, quitter, crasher, hog
app_size:   .word torture_end - torture, echoq_end - echoq
            .word quitter_end - quitter, crasher_end - crasher, hog_end - hog

n_torture:  .asciiz "torture"
n_echoq:    .asciiz "echoq"
n_quitter:  .asciiz "quitter"
n_crasher:  .asciiz "crasher"
n_hog:      .asciiz "hog"
