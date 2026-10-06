; boot.s - reset, hardware setup, loading the built-in programs (README section 9)

.p816
.include "hw.inc"
.include "kernel.inc"
.include "macros.inc"
.include "kvars.inc"

.import acia_init, create_process
.import app_image, app_size     ; per-slot program table, from the ROM's app table

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

        jsr acia_init
        jsr via_init
        jsr load_apps

        lda #IDLE_SLOT * 2      ; boot carries on as the idle process
        sta current_proc
        jsr timer_start
        cli
        cop $00                 ; yield: save idle's frame, start the first process

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

; ---------------------------------------------------------------------------
; load_apps: copy each slot's program image from ROM to APP_LOAD in the
; slot's bank, then create the process.
;
; MVN's banks are part of the instruction, so the copy runs from a
; three-byte stub in RAM with the destination bank patched in.

load_apps:
        A8
        lda #$54                ; MVN (operands: destination bank, source bank)
        sta mvn_stub
        stz mvn_stub+2          ; source bank 0: images are in ROM
        lda #$60                ; RTS
        sta mvn_stub+3
        A16

        ldx #$0000              ; slot * 2
@slot:
        lda app_size,x
        beq @next               ; nothing for this slot

        A8
        txa
        lsr a
        inc a                   ; slot n lives in bank n + 1
        sta mvn_stub+1
        A16

        phx
        lda app_image,x
        pha
        lda app_size,x
        dec a                   ; MVN copies A + 1 bytes
        plx                     ; X = source address
        ldy #APP_LOAD           ; Y = destination address
        phb                     ; MVN leaves the data bank at the destination
        jsr mvn_stub
        plb
        plx

        lda #APP_LOAD
        jsr create_process
@next:
        inx
        inx
        cpx #NUM_PROCS * 2
        bcc @slot
        rts
