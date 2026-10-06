; kvars.s - kernel variables (README section 5)
;
; Boot clears $0000-$01FF, so everything here starts at zero.

.p816
.include "hw.inc"
.include "kernel.inc"

.exportzp rx_head, rx_tail

.export current_proc, saved_stack, proc_state
.export ticks, tx_pending, con_lock, con_owner
.export kernel_sp_save, new_entry, mvn_stub
.export brk_count, last_brk_slot, last_brk_pc

.segment "ZEROPAGE"

rx_head:        .res 1      ; ACIA receive ring: next slot the IRQ writes
rx_tail:        .res 1      ; next slot K_GETC reads. head = tail means empty

.segment "BSS"

; Process table. Indexed by slot number * 2, including the idle slot.
current_proc:   .res 2              ; running slot * 2
saved_stack:    .res NUM_SLOTS * 2  ; stack pointer of each stopped process
proc_state:     .res NUM_SLOTS * 2  ; PS_FREE or PS_READY (idle's entry stays 0)

ticks:          .res 4      ; timer 1 ticks since boot

tx_pending:     .res 1      ; nonzero once a character has been sent
con_lock:       .res 1      ; bit 0 set while a process holds the console
con_owner:      .res 2      ; holder's slot * 2 + 2, or 0

kernel_sp_save: .res 2      ; create_process scratch
new_entry:      .res 2

mvn_stub:       .res 4      ; MVN dst,src / RTS, patched before each copy

brk_count:      .res 2      ; processes killed by BRK
last_brk_slot:  .res 2      ; slot * 2 of the last one
last_brk_pc:    .res 3      ; return address its BRK pushed (BRK address + 2)
