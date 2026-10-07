; console.s - ACIA driver and console services
;
; Receive is interrupt-driven into a 256-byte ring at RX_BUF. Transmit writes
; a byte, then starts VIA timer 2 as a one-character delay, because the
; W65C51N's transmit-empty status bit can't be trusted. The next character
; waits for timer 2 to run out instead of sleeping after each send, so the
; caller can do other work in between.

.p816
.include "hw.inc"
.include "kernel.inc"
.include "macros.inc"
.include "kvars.inc"

.export acia_init, acia_rx_service, putc_raw
.export k_putc, k_getc, k_puts, k_con_lock, k_con_unlock

.segment "CODE"

; ---------------------------------------------------------------------------
; acia_init: reset the ACIA and enable receive interrupts.
; A 16-bit, D = 0, data bank 0, interrupts disabled.

acia_init:
        A8
        sta ACIA_STATUS         ; programmed reset (value ignored)
        lda #ACIA_CTRL_VALUE
        sta ACIA_CTRL
        lda #ACIA_CMD_VALUE
        sta ACIA_CMD
        lda ACIA_STATUS         ; clear anything pending
        lda ACIA_DATA
        stz rx_head
        stz rx_tail
        stz tx_pending
        A16
        rts

; ---------------------------------------------------------------------------
; acia_rx_service: called from the IRQ handler when the ACIA's status had
; its IRQ bit set.
; In: A = the status byte. A, X 8-bit, D = 0, data bank 0.
; If the ring is full, the byte is dropped.
;
; Ctrl-Z never reaches the ring. It pauses the foreground program and gives
; the keyboard back to the shell. If the shell already has the keyboard, it
; is ignored.

acia_rx_service:
        .a8
        .i8
        and #ACIA_ST_RDRF
        beq @done
        lda ACIA_DATA
        cmp #KEY_PAUSE
        beq @pause
        ldx rx_head
        sta RX_BUF,x            ; a full ring never reads this slot, so writing it is safe
        inx
        cpx rx_tail
        beq @done               ; full: don't advance head
        stx rx_head
@done:
        rts

@pause:                         ; slot numbers are below $100, so 8-bit is enough
        ldx fg_proc
        beq @done               ; the shell has the keyboard already
        lda #PS_PAUSED
        sta proc_state,x
        stz fg_proc
        inx                     ; drop the console lock if it holds it, so the
        inx                     ; shell can't wait forever on a paused process
        cpx con_owner
        bne @no_lock
        stz con_owner
        stz con_lock
@no_lock:
        lda #1
        sta resched             ; it may be the running process: switch away
        rts

; ---------------------------------------------------------------------------
; putc_raw: send one character.
; In: A 8-bit = character. Data bank 0; any D, any index width.
; Clobbers A and B. Interrupts are only off for the few instructions
; that check the timer and start the next send.

putc_raw:
        .a8
        xba                     ; keep the character in B
@wait:
        php
        sei
        lda tx_pending
        beq @ready              ; nothing sent yet
        lda VIA_IFR
        and #VIA_INT_T2
        bne @ready              ; previous character's delay has run out
        plp
        bra @wait
@ready:
        xba
        sta ACIA_DATA
        lda #<TX_DELAY
        sta VIA_T2CL
        lda #>TX_DELAY
        sta VIA_T2CH            ; start the delay; clears the T2 flag
        lda #1
        sta tx_pending
        plp
        rts

; ---------------------------------------------------------------------------
; K_PUTC: A low byte = character.

k_putc:
        SVC_ENTER
        pha
        A8
        jsr putc_raw
        A16
        pla
        SVC_EXIT

; ---------------------------------------------------------------------------
; K_GETC: carry set and A = character if one is waiting, else carry clear
; and A = 0. Only the foreground process receives input; any other caller
; always gets carry clear.

k_getc:
        SVC_ENTER
        lda current_proc
        cmp fg_proc
        bne @not_fg             ; only the foreground process gets input
        php
        sei                     ; another process could be reading the ring too
        AXY8
        ldx rx_tail
        cpx rx_head
        beq @empty
        lda RX_BUF,x
        inx
        stx rx_tail
        plp
        .a16
        .i16
        and #$00FF
        SVC_SEC
        SVC_EXIT
@empty:
        .a8
        .i8
        plp
        .a16
        .i16
@not_fg:
        lda #$0000
        SVC_CLC
        SVC_EXIT

; ---------------------------------------------------------------------------
; K_PUTS: A = 16-bit address of a zero-terminated string in the caller's
; data bank.
;
; The 24-bit pointer is built on the stack and D is pointed at it, so the
; service keeps no state outside the caller's stack. Another process can
; be in K_PUTS at the same time.

k_puts:
        SVC_ENTER
        tax
        A8
        lda SVC_DBR,s           ; caller's bank
        pha
        A16
        phx                     ; pointer is now at 1,s (low, high, bank)
        tsc
        tcd                     ; and at [$01] through D
        ldy #$0000
@loop:
        A8
        lda [$01],y
        beq @done
        jsr putc_raw
        iny
        bra @loop
@done:
        A16
        pla                     ; drop the pointer
        A8
        pla
        A16
        txa                     ; give the caller back its A
        SVC_EXIT

; ---------------------------------------------------------------------------
; K_CON_LOCK: take the console lock, yielding while someone else has it.
; TSB sets the bit and tests its old value in one instruction (README
; section 13). Interrupts are off from the TSB until the owner is recorded,
; so Ctrl-Z can never pause a process that holds the lock unrecorded.

k_con_lock:
        SVC_ENTER
        pha
@try:
        php
        sei
        A8
        lda #$01
        tsb con_lock
        bne @held
        A16
        lda current_proc
        inc a
        inc a
        sta con_owner
        plp
        pla
        SVC_EXIT
@held:
        A16
        plp
        cop $00                 ; held elsewhere: yield and try again
        bra @try

; ---------------------------------------------------------------------------
; K_CON_UNLOCK: release the console lock if the caller holds it.

k_con_unlock:
        SVC_ENTER
        pha
        php
        sei
        lda current_proc
        inc a
        inc a
        cmp con_owner
        bne @not_owner
        stz con_owner
        A8
        lda #$01
        trb con_lock
        A16
@not_owner:
        plp
        pla
        SVC_EXIT
