; demo_table.s - the programs built into the demo ROM, one entry per slot.
;
; Each image is copied to its slot's bank at boot. Both ticker slots share
; one copy of the image in ROM.

.p816
.export app_image, app_size

.segment "APPS"
ticker:     .incbin "ticker.bin"
ticker_end:
leds:       .incbin "leds.bin"
leds_end:
echo:       .incbin "echo.bin"
echo_end:

.segment "RODATA"
;                  slot 0  slot 1  slot 2  slot 3  4  5  6
app_image:  .word  ticker, ticker, leds,   echo,   0, 0, 0
app_size:   .word  ticker_end - ticker, ticker_end - ticker
            .word  leds_end - leds, echo_end - echo, 0, 0, 0
