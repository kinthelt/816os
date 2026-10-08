; kvars.inc - kernel variables, defined in kernel/kvars.s
;
; The ZEROPAGE ones are in the kernel direct page ($0000-$00FF) and must only
; be used while D = 0. The rest are in $0100-$01FF and use absolute
; addressing, so they work with any D as long as the data bank is 0.

.importzp rx_head, rx_tail

.import current_proc, saved_stack, proc_state
.import ticks, tx_pending, con_lock, con_owner
.import kernel_sp_save, new_entry, mvn_stub
.import fg_proc, resched, proc_name, rx_raw
.import brk_count, last_brk_slot, last_brk_pc
