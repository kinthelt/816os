; demo_table.s - the programs built into the demo ROM.
;
; The shell's `run <name>` copies an image to $0000 of the first free bank
; and starts it there. Names are lower case.

.p816
.export app_count, app_names, app_image, app_size

.segment "APPS"
ticker:     .incbin "ticker.bin"
ticker_end:
leds:       .incbin "leds.bin"
leds_end:
echo:       .incbin "echo.bin"
echo_end:

.segment "RODATA"
app_count:  .word 3
app_names:  .word n_ticker, n_leds, n_echo
app_image:  .word ticker, leds, echo
app_size:   .word ticker_end - ticker, leds_end - leds, echo_end - echo

n_ticker:   .asciiz "ticker"
n_leds:     .asciiz "leds"
n_echo:     .asciiz "echo"
