; kernel.inc - kernel-internal constants and service frame macros

NUM_PROCS   = 7             ; process slots 0-6, in banks 1-7
IDLE_SLOT   = 7             ; pseudo-process that runs when nothing else can
NUM_SLOTS   = NUM_PROCS + 1 ; table entries, including idle

PS_FREE     = 0
PS_READY    = 1

KSTACK_TOP  = $5FFF         ; kernel stack: boot, then the idle loop
RX_BUF      = $5D00         ; 256-byte ACIA receive ring
APP_LOAD    = $0000         ; where a program image is loaded in its bank

; 5 ms at 6 MHz. Timer 1's free-run period is a couple of cycles longer than
; the latch value (README section 10).
SLICE_COUNT = CPU_HZ / 200

; Timer 2 one-shot delay between transmitted characters: one character time
; (10 bits) plus 10%, rounded up. Works around the W65C51N transmit status bug.
; Same formula as TX_CYCLES in be6502's rom/bios.s (572 cycles at 6 MHz).
TX_DELAY    = (CPU_HZ * 10 / BAUD * 110 + 99) / 100

; ---------------------------------------------------------------------------
; Service entry and exit.
;
; Services are entered with JSL, on the caller's stack, with the caller's
; register widths, Direct Register and data bank register. SVC_ENTER saves
; what the caller expects back, then switches to 16-bit registers, the kernel
; direct page and data bank 0. The frame it leaves on the stack:
;
;   SVC_DBR,s   caller's data bank register
;   SVC_D,s     caller's Direct Register (2 bytes)
;   SVC_Y,s     caller's Y (2 bytes)
;   SVC_X,s     caller's X (2 bytes)
;   SVC_P,s     caller's status flags
;   then the 3-byte JSL return address
;
; Offsets are for S as SVC_ENTER leaves it. Add the size of anything pushed
; since.

SVC_DBR = 1
SVC_D   = 2
SVC_Y   = 4
SVC_X   = 6
SVC_P   = 8

.macro SVC_ENTER
        php
        rep #$30
        .a16
        .i16
        phx
        phy
        phd
        phb
        pea $0000
        pld                     ; kernel direct page
        phk
        plb                     ; data bank 0 (services run in bank 0)
.endmacro

.macro SVC_EXIT
        plb
        pld
        ply
        plx
        plp                     ; caller's widths and interrupt flag
        rtl
.endmacro

; Set or clear the carry flag the caller gets back. Keeps A.
.macro SVC_SEC
        pha
        sep #$20
        .a8
        lda SVC_P+2,s
        ora #$01
        sta SVC_P+2,s
        rep #$20
        .a16
        pla
.endmacro

.macro SVC_CLC
        pha
        sep #$20
        .a8
        lda SVC_P+2,s
        and #$FE
        sta SVC_P+2,s
        rep #$20
        .a16
        pla
.endmacro
