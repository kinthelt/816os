; sched.s - interrupt entry, context switching, process creation and exit
;
; README sections 6-8. A stopped process's registers sit on its own stack
; as a 13-byte frame:
;
;   S+1     data bank register
;   S+2,3   Direct Register
;   S+4,5   Y
;   S+6,7   X
;   S+8,9   A (all 16 bits)
;   S+10    status flags        \
;   S+11,12 return address       } pushed by the interrupt
;   S+13    program bank        /
;
; saved_stack holds S for each stopped process. Switching is just reloading S.

.p816
.include "hw.inc"
.include "kernel.inc"
.include "macros.inc"
.include "kvars.inc"

.import acia_rx_service

.export irq_native, cop_native, brk_native
.export create_process, k_exit, k_yield

FRAME_P   = 10                  ; frame offsets, see above
FRAME_PC  = 11
FRAME_PBR = 13

.segment "CODE"

; ---------------------------------------------------------------------------
; Native-mode IRQ (vector $00FFEE). Services the ACIA, and on a timer 1 tick
; switches to the next ready process.
;
; On entry the data bank register and Direct Register still belong to the
; interrupted process. Only the program bank register has been cleared.

irq_native:
        AXY16
        pha
        phx
        phy
        phd
        phb
        lda #$0000
        tcd                     ; kernel direct page
        phk
        plb                     ; data bank 0

        AXY8
        lda ACIA_STATUS         ; reading the status clears the ACIA's IRQ bit
        bpl @no_acia
        jsr acia_rx_service
@no_acia:
        lda VIA_IFR
        and #VIA_INT_T1
        beq @same_process       ; not a timer tick: back to the same process

        lda VIA_T1CL            ; acknowledge timer 1
        AXY16
        inc ticks
        bne switch_out
        inc ticks+2
        bra switch_out

@same_process:
        AXY16
restore_context:
        plb
        pld
        ply
        plx
        pla
        rti                     ; flags, return address, program bank

; ---------------------------------------------------------------------------
; COP (vector $00FFE4): yield. The rest of the time slice goes to the next
; ready process. Timer 1 keeps running, so ticks stay evenly spaced.

cop_native:
        AXY16
        pha
        phx
        phy
        phd
        phb
        lda #$0000
        tcd
        phk
        plb
        ; fall through

; Park the current process's stack pointer, then pick the next one.
; A, X, Y 16-bit, D = 0, data bank 0, interrupts disabled.
switch_out:
        ldx current_proc
        tsc
        sta saved_stack,x

; Round-robin from the slot after X, wrapping, ending with X's own slot.
; If no slot is ready, run idle. X = slot * 2 on entry.
pick_next:
        ldy #NUM_PROCS
@next:
        inx
        inx
        cpx #NUM_PROCS * 2
        bcc @check
        ldx #0
@check:
        lda proc_state,x
        bne @found
        dey
        bne @next
        ldx #IDLE_SLOT * 2
@found:
        stx current_proc
        lda saved_stack,x
        tcs
        bra restore_context

; ---------------------------------------------------------------------------
; Native BRK (vector $00FFE6): kill the process and record where it stopped.

brk_native:
        AXY16
        pha
        phx
        phy
        phd
        phb
        lda #$0000
        tcd
        phk
        plb

        lda current_proc
        sta last_brk_slot
        lda FRAME_PC,s
        sta last_brk_pc
        A8
        lda FRAME_PBR,s
        sta last_brk_pc+2
        A16
        inc brk_count
        bra kill_current

; ---------------------------------------------------------------------------
; K_EXIT: end the calling process. Also reached when a process returns from
; its entry point with RTL (create_process leaves k_exit's address there).

k_exit:
        sei
        AXY16
        pea $0000
        pld
        phk
        plb

; Free the current slot and switch away. Nothing on its stack is kept.
kill_current:
        ldx current_proc
        stz proc_state,x

        txa                     ; drop the console lock if this process held it
        inc a
        inc a
        cmp con_owner
        bne @no_lock
        stz con_owner
        A8
        stz con_lock
        A16
@no_lock:
        jmp pick_next

; ---------------------------------------------------------------------------
; K_YIELD

k_yield:
        cop $00
        rtl

; ---------------------------------------------------------------------------
; create_process: build a process's initial frame on its stack so the
; scheduler can start it like any other stopped process.
;
; In:  X = slot * 2 (0-12)
;      A = entry address within the slot's bank
; Call with interrupts disabled (the stack pointer briefly points at the new
; process's stack), A/X/Y 16-bit, D = 0, data bank 0.
; The process starts with 16-bit registers, interrupts enabled, all
; registers zero except D and the data bank, which are its own.

create_process:
        sta new_entry
        tsc
        sta kernel_sp_save
        lda stack_top,x
        tcs                     ; temporarily on the new process's stack

        ; Return address for an RTL from the entry point: k_exit.
        A8
        lda #^k_exit
        pha
        A16
        lda #.loword(k_exit - 1)
        pha

        ; What the interrupt would have pushed.
        A8
        lda slot_bank,x
        pha                     ; program bank
        A16
        lda new_entry
        pha                     ; return address
        A8
        lda #$00
        pha                     ; flags: 16-bit registers, interrupts enabled
        A16

        ; What the handler would have pushed.
        pea $0000               ; A
        pea $0000               ; X
        pea $0000               ; Y
        lda direct_page,x
        pha                     ; Direct Register
        A8
        lda slot_bank,x
        pha                     ; data bank register
        A16

        tsc
        sta saved_stack,x
        lda #PS_READY
        sta proc_state,x

        lda kernel_sp_save
        tcs                     ; back on the caller's stack
        rts

.segment "RODATA"

; Per-slot constants (README section 5)
stack_top:   .word $14FF, $20FF, $2CFF, $38FF, $44FF, $50FF, $5CFF
direct_page: .word $0200, $0300, $0400, $0500, $0600, $0700, $0800
slot_bank:   .word $01, $02, $03, $04, $05, $06, $07
