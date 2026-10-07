; boot.s - reset, hardware setup, starting the shell (README section 9)

.p816
.include "hw.inc"
.include "kernel.inc"
.include "macros.inc"
.include "kvars.inc"

.import acia_init, start_shell

.export reset, idle

.segment "CODE"

reset:                          ; the CPU starts in emulation mode
        sei
        cld
        clc
        xce                     ; native mode
        AXY16
        lda #KSTACK_TOP
        tcs
        lda #$0000
        tcd                     ; kernel direct page
        phk
        plb                     ; data bank 0

        ldx #$01FE              ; clear the kernel direct page and variables
@clear:
        stz a:$0000,x
        dex
        dex
        bpl @clear

        A8                      ; the copy stub used to load programs:
        lda #$54                ; MVN (operands: destination bank, source bank)
        sta mvn_stub
        lda #$60                ; RTS
        sta mvn_stub+3
        A16

        jsr acia_init
        jsr via_init
        jsr start_shell         ; the only process at boot

        lda #IDLE_SLOT * 2      ; boot carries on as the idle process
        sta current_proc
        jsr timer_start
        cli
        cop $00                 ; yield: save idle's frame, start the shell

; Runs only when no process is ready. Its context lives on the kernel stack.
idle:
        wai
        bra idle

; ---------------------------------------------------------------------------
; via_init: LEDs on port B, all VIA interrupts off. Timer 2 stays in one-shot
; mode for the ACIA transmit delay.

via_init:
        A8
        lda #$7F
        sta VIA_IER             ; disable every VIA interrupt
        sta VIA_IFR             ; and clear any pending flag
        lda #$FF
        sta VIA_DDRB            ; port B all outputs (LEDs)
        stz VIA_PORTB
        A16
        rts

; ---------------------------------------------------------------------------
; timer_start: timer 1 free-running at SLICE_COUNT, interrupt enabled
; (README section 10).

timer_start:
        A8
        lda VIA_ACR
        and #%00111111          ; clear the timer 1 mode bits, keep the rest
        ora #%01000000          ; continuous interrupts, PB7 output off
        sta VIA_ACR
        lda #<SLICE_COUNT
        sta VIA_T1CL
        lda #>SLICE_COUNT
        sta VIA_T1CH            ; load the counter and start it
        lda #$80 | VIA_INT_T1
        sta VIA_IER
        A16
        rts
