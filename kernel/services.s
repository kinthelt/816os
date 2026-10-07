; services.s - the kernel's fixed jump table and small services
;
; Processes call services with JSL to the addresses in include/os816.inc.
; Each entry is a 3-byte JMP. The asserts fail the link if this table and
; os816.inc disagree.

.p816
.include "hw.inc"
.include "kernel.inc"
.include "macros.inc"
.include "kvars.inc"
.include "os816.inc"

.import k_putc, k_getc, k_puts, k_con_lock, k_con_unlock
.import k_yield, k_exit

.segment "CODE"

; K_GETPID: A = slot number.
k_getpid:
        SVC_ENTER
        lda current_proc
        lsr a
        SVC_EXIT

; K_TICKS: A = low 16 bits of the tick count. A 16-bit load is a single
; instruction, so the timer can't change it halfway through.
k_ticks:
        SVC_ENTER
        lda ticks
        SVC_EXIT

.segment "JUMPTABLE"

.macro ENTRY addr, target
        .assert * = .loword(addr), lderror, "jump table out of step with os816.inc"
        jmp target
.endmacro

        ENTRY K_PUTC,       k_putc
        ENTRY K_GETC,       k_getc
        ENTRY K_PUTS,       k_puts
        ENTRY K_YIELD,      k_yield
        ENTRY K_EXIT,       k_exit
        ENTRY K_GETPID,     k_getpid
        ENTRY K_TICKS,      k_ticks
        ENTRY K_CON_LOCK,   k_con_lock
        ENTRY K_CON_UNLOCK, k_con_unlock
