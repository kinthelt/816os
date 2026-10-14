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
chatter:    .incbin "chatter.bin"
chatter_end:
xfer:       .incbin "xfer.bin"
xfer_end:
bgxfer:     .incbin "bgxfer.bin"
bgxfer_end:
spawner:    .incbin "spawner.bin"
spawner_end:
sleeper:    .incbin "sleeper.bin"
sleeper_end:

.segment "RODATA"
app_count:  .word 10
app_names:  .word n_torture, n_echoq, n_quitter, n_crasher, n_hog, n_chatter, n_xfer, n_bgxfer, n_spawner, n_sleeper
app_image:  .word torture, echoq, quitter, crasher, hog, chatter, xfer, bgxfer, spawner, sleeper
app_size:   .word torture_end - torture, echoq_end - echoq
            .word quitter_end - quitter, crasher_end - crasher, hog_end - hog
            .word chatter_end - chatter, xfer_end - xfer, bgxfer_end - bgxfer
            .word spawner_end - spawner, sleeper_end - sleeper

n_torture:  .asciiz "torture"
n_echoq:    .asciiz "echoq"
n_quitter:  .asciiz "quitter"
n_crasher:  .asciiz "crasher"
n_hog:      .asciiz "hog"
n_chatter:  .asciiz "chatter"
n_xfer:     .asciiz "xfer"
n_bgxfer:   .asciiz "bgxfer"
n_spawner:  .asciiz "spawner"
n_sleeper:  .asciiz "sleeper"
