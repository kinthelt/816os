; shell.s - the kernel's command shell, process 0
;
; Runs from ROM as the process in bank 0, with its direct page at $0200 and
; its own stack. It is scheduled like any other process, and the kernel
; starts it again if it is killed or hits BRK.
;
; Commands (input is case-insensitive):
;
;   ps              list processes
;   run <name>      load a program from ROM into the first free bank and
;                   run it in the foreground
;   fg <bank>       resume a process in the foreground
;   bg <bank>       resume a paused process in the background
;   kill <bank>     end a process; kill 0 restarts the shell
;   help            list commands and programs
;
; Anything else is a memory command, as in be6502's Wozmon. Addresses are
; up to six hex digits, bank first; four or fewer mean bank 0:
;
;   21000           examine $02:1000
;   21000.2100f     examine $02:1000-$02:100F
;   21000: a9 00    store bytes from $02:1000
;
; Nothing stops you from examining or storing anywhere, including the I/O
; window and the kernel's own memory. Reading some I/O registers changes the
; hardware's state (README section 0).
;
; Ctrl-Z, handled by the receive interrupt, pauses the foreground program
; and gives the keyboard back to the shell. ESC cancels a line being typed,
; or stops a long examine.

.p816
.include "hw.inc"
.include "kernel.inc"
.include "macros.inc"
.include "kvars.inc"
.include "os816.inc"

.import create_process, release_slot
.import app_count, app_names, app_image, app_size

.export shell_main, shell_name

IN       = LINE_BUF
LINE_MAX = 127                  ; characters per line, not counting the CR

KEY_BS   = $08
KEY_CR   = $0D
KEY_ESC  = $1B
KEY_DEL  = $7F

; Direct page variables (the shell's direct page is DP_BASE). They start at
; $10 so Wozmon's `XAML-1,x` indexing stays in range.
XAML    = $10                   ; last examined address (24 bits)
XAMH    = $11
XAMB    = $12
STL     = $13                   ; store address (24 bits)
STH     = $14
STB     = $15
L       = $16                   ; hex value being parsed (24 bits)
H       = $17
BK      = $18
YSAV    = $19                   ; input index where the hex value started
MODE    = $1A                   ; $00 examine, $74 store, $B8 block examine
OPEN    = $1B                   ; nonzero: an examine line is waiting for its CR LF
PTR     = $1C                   ; 16-bit pointer to a name in ROM
CIDX    = $1E                   ; table index
START   = $1F                   ; input index of the line's first word
JOB     = $20                   ; slot * 2 of the foreground job (2 bytes)
BRKS    = $22                   ; brk_count when the job started (2 bytes)
SLOT    = $24                   ; slot * 2 (2 bytes)
XSTART  = $26                   ; save: first address (3 bytes), a K_SAVE table
XCOUNT  = $29                   ; save: byte count (3 bytes), a K_SAVE table

.macro PRINT str
        A16
        lda #str
        jsl K_PUTS
        A8
.endmacro

.macro LOCK
        jsl K_CON_LOCK
.endmacro

.macro UNLOCK
        jsl K_CON_UNLOCK
.endmacro

.segment "CODE"

shell_main:
        AXY8
        LOCK
        PRINT msg_banner
        UNLOCK

; ---------------------------------------------------------------------------
; Read a line into IN, folded to lower case and ended with a CR.

prompt:
        PRINT msg_prompt
        ldy #0
nextchar:
        jsl K_GETC
        bcs @got
        jsl K_YIELD
        bra nextchar
@got:
        cmp #KEY_CR
        beq @cr
        cmp #KEY_ESC
        beq @esc
        cmp #KEY_BS
        beq @rubout
        cmp #KEY_DEL
        beq @rubout
        cmp #' '
        bcc nextchar            ; ignore other control characters
        cpy #LINE_MAX
        bcs nextchar            ; line full
        jsl K_PUTC              ; echo it as typed
        cmp #'A'
        bcc @store
        cmp #'Z' + 1
        bcs @store
        ora #$20                ; lower case
@store:
        sta IN,y
        iny
        bra nextchar
@rubout:
        cpy #0
        beq nextchar
        dey
        PRINT msg_rubout
        bra nextchar
@esc:
        PRINT msg_cancel
        bra prompt
@cr:
        sta IN,y
        PRINT msg_crlf
        jsr do_line
        bra prompt

; ---------------------------------------------------------------------------
; do_line: run the command in IN.

do_line:
        ldx #0
        jsr skip_spaces
        lda IN,x
        cmp #KEY_CR
        beq @done               ; empty line
        stx START
        stz CIDX
@next:
        ldy CIDX
        cpy #cmd_table_end - cmd_table
        beq @memory
        lda cmd_table,y
        sta PTR
        lda cmd_table+1,y
        sta PTR+1
        ldx START
        jsr match_word
        bcs @found
        lda CIDX
        clc
        adc #4
        sta CIDX
        bra @next
@found:                         ; X is just past the command word
        jsr skip_spaces
        ldy CIDX
        lda cmd_table+3,y       ; jump to the handler with RTS
        pha
        lda cmd_table+2,y
        pha
@done:
        rts
@memory:
        ldx START               ; check the whole line before doing any of it,
@check:                         ; so a mistyped command can't half-run as hex
        lda IN,x
        cmp #KEY_CR
        beq @valid
        cmp #'.' + 1
        bcc @ok                 ; delimiters and '.', as in Wozmon
        cmp #':'
        beq @ok
        jsr hex_value
        bcs @invalid
@ok:
        inx
        bra @check
@valid:
        jmp monitor
@invalid:
        LOCK
        PRINT msg_what
        UNLOCK
        rts

; hex_value: if A is a hex digit ('0'-'9', 'a'-'f'), carry clear and
; A = its value. Otherwise carry set.
hex_value:
        cmp #'0'
        bcc @no
        cmp #'9' + 1
        bcc @digit
        cmp #'a'
        bcc @no
        cmp #'f' + 1
        bcs @no
        sbc #'a' - 11           ; carry clear: A - 'a' + 10
        clc
        rts
@digit:
        and #$0F                ; carry is clear
        rts
@no:
        sec
        rts

; match_word: does the word at IN,X equal the name at PTR?
; Out: carry set and X just past the word if so, else carry clear.
match_word:
        ldy #0
@loop:
        lda (PTR),y
        beq @end
        cmp IN,x
        bne @no
        inx
        iny
        bra @loop
@end:
        lda IN,x
        cmp #' '
        beq @yes
        cmp #KEY_CR
        beq @yes
@no:
        clc
        rts
@yes:
        sec
        rts

skip_spaces:
        lda IN,x
        cmp #' '
        bne @done
        inx
        bra skip_spaces
@done:
        rts

; parse_bank: a single digit 0-7 at IN,X, then the end of the line.
; Out: carry clear and SLOT = bank * 2, X = the same; carry set if bad.
parse_bank:
        lda IN,x
        sec
        sbc #'0'
        cmp #NUM_PROCS
        bcs @bad
        asl a
        sta SLOT
        stz SLOT+1
        inx
        jsr skip_spaces
        lda IN,x
        cmp #KEY_CR
        bne @bad
        ldx SLOT
        clc
        rts
@bad:
        sec
        rts

.segment "RODATA"

cmd_table:
        .word str_ps,   cmd_ps - 1
        .word str_run,  cmd_run - 1
        .word str_fg,   cmd_fg - 1
        .word str_bg,   cmd_bg - 1
        .word str_kill, cmd_kill - 1
        .word str_save, cmd_save - 1
        .word str_load, cmd_load - 1
        .word str_help, cmd_help - 1
cmd_table_end:

.segment "CODE"

; ---------------------------------------------------------------------------
; help

cmd_help:
        LOCK
        PRINT msg_help
        ldy #0                  ; list the programs in ROM
@prog:
        tya
        lsr a
        cmp app_count
        bcs @end
        lda #' '
        jsl K_PUTC
        A16
        lda app_names,y
        jsl K_PUTS
        A8
        iny
        iny
        bra @prog
@end:
        PRINT msg_crlf
        UNLOCK
        rts

; ---------------------------------------------------------------------------
; ps

cmd_ps:
        LOCK
        PRINT msg_ps_head
        ldx #0
@slot:
        lda proc_state,x
        beq @next               ; free
        txa
        lsr a
        ora #'0'
        jsl K_PUTC
        lda proc_state,x
        cmp #PS_PAUSED
        beq @paused
        PRINT msg_ready
        bra @name
@paused:
        PRINT msg_paused
@name:
        A16
        lda proc_name,x
        jsl K_PUTS
        A8
        PRINT msg_crlf
@next:
        inx
        inx
        cpx #NUM_PROCS * 2
        bcc @slot
        UNLOCK
        rts

; ---------------------------------------------------------------------------
; run <name>

cmd_run:
        stx START               ; the name
        stz CIDX                ; program table index * 2
@find:
        lda CIDX
        lsr a
        cmp app_count
        bcs @unknown
        ldy CIDX
        lda app_names,y
        sta PTR
        lda app_names+1,y
        sta PTR+1
        ldx START
        jsr match_word
        bcs @found
        inc CIDX
        inc CIDX
        bra @find
@unknown:
        LOCK
        PRINT msg_no_program
        UNLOCK
        rts

@found:
        ldx #2                  ; first free bank
@free:
        lda proc_state,x
        beq @got
        inx
        inx
        cpx #NUM_PROCS * 2
        bcc @free
        LOCK
        PRINT msg_no_bank
        UNLOCK
        rts

@got:
        stx SLOT
        stz SLOT+1
        txa
        lsr a
        sta mvn_stub+1          ; destination bank
        stz mvn_stub+2          ; source bank 0: the image is in ROM

        AXY16
        lda CIDX                ; one byte: don't pick up the next variable
        and #$00FF
        tay
        lda app_size,y
        dec a                   ; MVN copies A + 1 bytes
        pha
        lda app_image,y
        tax                     ; source
        pla
        ldy #APP_LOAD           ; destination
        phb                     ; MVN leaves the data bank at the destination
        jsr mvn_stub
        plb

        lda CIDX
        and #$00FF
        tay
        ldx SLOT
        lda app_names,y
        sta proc_name,x
        jsr begin_job
        php
        sei
        lda #APP_LOAD
        jsr create_process
        stx fg_proc             ; it gets the keyboard
        plp
        AXY8

        LOCK
        jsr print_job
        PRINT msg_crlf
        UNLOCK
        jmp wait_fg

; ---------------------------------------------------------------------------
; fg <bank>, bg <bank>, kill <bank>

cmd_fg:
        jsr parse_user_bank
        bcs @done
        jsr begin_job
        php
        sei
        lda #PS_READY
        sta proc_state,x
        stx fg_proc
        plp
        jmp wait_fg
@done:
        rts

cmd_bg:
        jsr parse_user_bank
        bcs @done
        lda proc_state,x
        cmp #PS_PAUSED
        bne @running
        lda #PS_READY
        sta proc_state,x
        LOCK
        jsr print_job
        PRINT msg_crlf
        UNLOCK
@done:
        rts
@running:
        LOCK
        PRINT msg_running
        UNLOCK
        rts

cmd_kill:
        jsr parse_bank
        bcs usage
        cpx #SHELL_SLOT * 2
        bne @other
        jsl K_EXIT              ; the kernel starts a fresh shell
@other:
        lda proc_state,x
        beq no_such
        AXY16                   ; X's high byte is 0 after 8-bit mode
        php
        sei
        jsr release_slot
        plp
        AXY8
        LOCK
        PRINT msg_open
        txa
        lsr a
        ora #'0'
        jsl K_PUTC
        PRINT msg_killed
        UNLOCK
        rts

; parse_user_bank: like parse_bank, but only a bank with a process in it,
; other than the shell. Prints the error itself.
parse_user_bank:
        jsr parse_bank
        bcs usage
        cpx #SHELL_SLOT * 2
        beq usage
        lda proc_state,x
        beq no_such
        clc
        rts

usage:
        LOCK
        PRINT msg_usage
        UNLOCK
        sec
        rts

no_such:
        LOCK
        PRINT msg_no_such
        UNLOCK
        sec
        rts

; ---------------------------------------------------------------------------
; save <start>.<end>, load <addr>: the K_SAVE and K_LOAD services, which do
; the XMODEM/CRC transfer. Addresses are up to six hex digits, bank first,
; as in the memory commands. The services take 3-byte tables in the
; caller's data bank; the shell's data bank is 0, so a table in its direct
; page is at DP_BASE plus its offset.

cmd_save:
        jsr parse_hex24         ; start
        bcs xfer_usage
        lda L
        sta XSTART
        lda H
        sta XSTART+1
        lda BK
        sta XSTART+2
        lda IN,x
        cmp #'.'
        bne xfer_usage
        inx
        jsr parse_hex24         ; end
        bcs xfer_usage
        jsr end_of_line
        bne xfer_usage
        sec                     ; count = end - start + 1
        lda L
        sbc XSTART
        sta XCOUNT
        lda H
        sbc XSTART+1
        sta XCOUNT+1
        lda BK
        sbc XSTART+2
        sta XCOUNT+2
        bcc xfer_usage          ; end before start
        inc XCOUNT
        bne @send
        inc XCOUNT+1
        bne @send
        inc XCOUNT+2
@send:
        AXY16
        lda #DP_BASE + XSTART
        ldx #DP_BASE + XCOUNT
        jsl K_SAVE
        AXY8
        rts

cmd_load:
        jsr parse_hex24         ; leaves the address in L, H, BK: a table
        bcs xfer_usage
        jsr end_of_line
        bne xfer_usage
        A16
        lda #DP_BASE + L
        jsl K_LOAD
        A8
        rts

xfer_usage:
        LOCK
        PRINT msg_xfer_usage
        UNLOCK
        rts

; end_of_line: skip spaces at IN,X; Z set if the line ends there.
end_of_line:
        jsr skip_spaces
        lda IN,x
        cmp #KEY_CR
        rts

; parse_hex24: hex digits at IN,X into L, H, BK. X ends just past them.
; Carry set if there were none.
parse_hex24:
        stz L
        stz H
        stz BK
        stx YSAV
@digit:
        lda IN,x
        jsr hex_value
        bcs @end
        asl a                   ; digit to the high nibble
        asl a
        asl a
        asl a
        ldy #4
@rotate:
        asl a
        rol L
        rol H
        rol BK
        dey
        bne @rotate
        inx
        bra @digit
@end:
        cpx YSAV
        beq @none
        clc
        rts
@none:
        sec
        rts

; begin_job: remember the job and the crash count before it runs.
; In: X = slot * 2. Any widths.
begin_job:
        php
        AXY16
        stx JOB
        lda brk_count
        sta BRKS
        plp
        rts

; wait_fg: wait until the shell has the keyboard again, then say why if
; the job was paused or crashed.
wait_fg:
        .a8
        .i8
        lda fg_proc
        beq @back
        jsl K_YIELD
        bra wait_fg
@back:
        ldx JOB
        lda proc_state,x
        cmp #PS_PAUSED
        beq @paused
        A16
        lda brk_count
        cmp BRKS
        A8
        beq @done               ; it just ended
        lda last_brk_slot
        cmp JOB
        bne @done
        LOCK                    ; it hit BRK
        PRINT msg_crlf
        jsr print_job
        PRINT msg_crashed
        A16
        lda last_brk_pc         ; BRK pushes its address + 2
        sec
        sbc #2
        sta SLOT
        A8
        lda last_brk_pc+2
        jsr prbyte
        lda SLOT+1
        jsr prbyte
        lda SLOT
        jsr prbyte
        PRINT msg_crlf
        UNLOCK
@done:
        rts
@paused:
        LOCK
        PRINT msg_crlf
        jsr print_job
        PRINT msg_paused_note
        UNLOCK
        rts

; print_job: "[n] name" for the slot in X (slot * 2). Keeps X.
print_job:
        PRINT msg_open
        txa
        lsr a
        ora #'0'
        jsl K_PUTC
        PRINT msg_close
        A16
        lda proc_name,x
        jsl K_PUTS
        A8
        rts

; ---------------------------------------------------------------------------
; monitor: Wozmon's examine and store, on the whole line.
; This follows be6502's rom/wozmon.s, without its run and XMODEM commands.

monitor:
        LOCK
        stz OPEN
        ldy #$FF                ; reset text index
        lda #$00                ; examine mode
        tax
setblock:
        asl a
setstor:
        asl a                   ; $00 examine, $74 store, $B8 block examine
        sta MODE
blskip:
        iny
nextitem:
        lda IN,y
        cmp #KEY_CR
        beq to_done
        cmp #'.'
        bcc blskip              ; delimiter
        beq setblock
        cmp #':'
        beq setstor
        stx L                   ; X = 0
        stx H
        stx BK
        sty YSAV

nexthex:
        lda IN,y
        jsr hex_value
        bcs nothex
        asl a                   ; digit to the high nibble
        asl a
        asl a
        asl a
        ldx #4
@rotate:
        asl a
        rol L
        rol H
        rol BK
        dex
        bne @rotate
        iny
        bra nexthex

to_done:
        jmp monitor_done

nothex:
        cpy YSAV
        bne @gothex
        jmp monitor_bad         ; not hex and not a delimiter
@gothex:
        bit MODE
        bvc notstor
        lda L                   ; store mode
        sta [STL]
        inc STL
        bne tonext
        inc STH
        bne tonext
        inc STB
tonext:
        jmp nextitem

notstor:
        bmi xamnext             ; block examine: carry on from the last address
        ldx #3
@setadr:
        lda L-1,x               ; the value becomes both the store and
        sta STL-1,x             ; examine address
        sta XAML-1,x
        dex
        bne @setadr
        jsr print_addr

prdata:
        lda #' '
        jsl K_PUTC
        lda [XAML]
        jsr prbyte
xamnext:
        stx MODE                ; X = 0: back to examine mode
        lda XAML                ; done when XAM >= the value
        cmp L
        lda XAMH
        sbc H
        lda XAMB
        sbc BK
        bcs tonext
        inc XAML
        bne @mod8
        inc XAMH
        bne @mod8
        inc XAMB
@mod8:
        lda XAML
        and #$07
        bne prdata              ; same line
        jsr esc_pressed         ; new line: ESC stops a long listing
        bcs monitor_done
        jsr print_addr
        bra prdata

monitor_bad:
        jsr end_line
        PRINT msg_what
        UNLOCK
        rts

monitor_done:
        jsr end_line
        UNLOCK
        rts

; esc_pressed: carry set if the next waiting key is ESC, which is then
; removed. Any other key is left for the next command line, so typing
; ahead during a listing loses nothing. Reads the receive ring directly,
; with absolute addressing because the ring indexes are in the kernel's
; direct page, not the shell's.
esc_pressed:
        php
        sei
        phx
        ldx a:rx_tail
        cpx a:rx_head
        beq @no
        lda RX_BUF,x
        cmp #KEY_ESC
        bne @no
        inx
        stx a:rx_tail
        plx
        plp
        sec
        rts
@no:
        plx
        plp
        clc
        rts

; print_addr: start a line with the examine address. Keeps X.
print_addr:
        jsr end_line
        lda #1
        sta OPEN
        lda XAMB
        jsr prbyte
        lda XAMH
        jsr prbyte
        lda XAML
        jsr prbyte
        lda #':'
        jsl K_PUTC
        rts

; end_line: finish an examine line, if one is open.
end_line:
        lda OPEN
        beq @done
        stz OPEN
        PRINT msg_crlf
@done:
        rts

; prbyte: print A as two hex digits. Keeps X and Y.
prbyte:
        pha
        lsr a
        lsr a
        lsr a
        lsr a
        jsr prhex
        pla
prhex:
        and #$0F
        ora #'0'
        cmp #'9' + 1
        bcc @out
        adc #6                  ; carry set: + 7 to reach 'A'
@out:
        jsl K_PUTC
        rts

; ---------------------------------------------------------------------------

.segment "RODATA"

shell_name:     .asciiz "shell"
str_ps:         .asciiz "ps"
str_run:        .asciiz "run"
str_fg:         .asciiz "fg"
str_bg:         .asciiz "bg"
str_kill:       .asciiz "kill"
str_help:       .asciiz "help"
str_save:       .asciiz "save"
str_load:       .asciiz "load"

msg_banner:     .byte 13, 10, "816os", 13, 10, 0
msg_prompt:     .asciiz "> "
msg_crlf:       .byte 13, 10, 0
msg_rubout:     .byte KEY_BS, ' ', KEY_BS, 0
msg_cancel:     .byte "\", 13, 10, 0
msg_what:       .byte "?", 13, 10, 0
msg_usage:      .byte "usage: fg|bg|kill <bank 0-7>", 13, 10, 0
msg_xfer_usage: .byte "usage: save <start>.<end> or load <address>", 13, 10, 0
msg_no_such:    .byte "no such process", 13, 10, 0
msg_no_program: .byte "no such program; try help", 13, 10, 0
msg_no_bank:    .byte "no free bank", 13, 10, 0
msg_running:    .byte "already running", 13, 10, 0
msg_open:       .asciiz "["
msg_close:      .asciiz "] "
msg_killed:     .byte "] killed", 13, 10, 0
msg_paused_note: .byte " paused", 13, 10, 0
msg_crashed:    .asciiz " hit BRK at "
msg_ps_head:    .byte "bank state  name", 13, 10, 0
msg_ready:      .asciiz "    ready  "
msg_paused:     .asciiz "    paused "
msg_help:
        .byte "ps            list processes", 13, 10
        .byte "run <name>    run a program in the first free bank", 13, 10
        .byte "fg <bank>     resume a process in the foreground", 13, 10
        .byte "bg <bank>     resume a paused process in the background", 13, 10
        .byte "kill <bank>   end a process (kill 0 restarts the shell)", 13, 10
        .byte "save <a>.<b>  send memory a-b with XMODEM (e.g. save 21000.213ff)", 13, 10
        .byte "load <a>      receive a file with XMODEM into memory at a", 13, 10
        .byte "help          this list", 13, 10
        .byte "21000         examine $02:1000", 13, 10
        .byte "21000.2100f   examine $02:1000-$02:100F", 13, 10
        .byte "21000: a9 00  store bytes from $02:1000", 13, 10
        .byte "ctrl-z pauses the foreground program; esc stops a listing", 13, 10
        .byte "programs:", 0
