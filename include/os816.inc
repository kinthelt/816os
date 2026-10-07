; os816.inc - the 816os kernel interface for processes
;
; Call every service with JSL, in native mode. Register widths, X, Y, the
; Direct Register and the data bank register are preserved. A is preserved
; unless the service returns a value in it. Pass 16-bit arguments with a
; 16-bit accumulator.
;
; The addresses are a fixed jump table in ROM. kernel/services.s checks at
; link time that the table matches this file.

K_PUTC        = $00FFB0   ; A low byte = character. Waits for the transmitter.
K_GETC        = $00FFB3   ; Carry set: A = character (high byte 0).
                          ; Carry clear: nothing received, A = 0.
                          ; Only the foreground process gets input; for any
                          ; other process this always returns carry clear.
                          ; Ctrl-Z never arrives: it pauses the foreground
                          ; process and returns the keyboard to the shell.
K_PUTS        = $00FFB6   ; A = 16-bit address of a zero-terminated string
                          ; in the caller's data bank.
K_YIELD       = $00FFB9   ; Give the rest of this time slice to the next process.
K_EXIT        = $00FFBC   ; End this process. Does not return. Returning from
                          ; the process's entry point with RTL does the same.
                          ; If it had the keyboard, the shell gets it back.
K_GETPID      = $00FFBF   ; A = this process's bank number (1-7).
K_TICKS       = $00FFC2   ; A = low 16 bits of the timer tick count.
K_CON_LOCK    = $00FFC5   ; Take the console lock, yielding while another process
                          ; holds it. Not recursive.
K_CON_UNLOCK  = $00FFC8   ; Release the console lock, if this process holds it.

TICK_HZ       = 200       ; timer ticks per second (5 ms slices)
