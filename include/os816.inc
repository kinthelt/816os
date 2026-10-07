; os816.inc - the 816os kernel interface for processes
;
; Call every service with JSL, in native mode. Register widths, X, Y, the
; Direct Register and the data bank register are preserved. A is preserved
; unless the service returns a value in it. Pass 16-bit arguments with a
; 16-bit accumulator.
;
; The addresses are a fixed jump table in ROM. kernel/services.s checks at
; link time that the table matches this file.

K_PUTC        = $00FF00   ; A low byte = character. Waits for the transmitter.
K_GETC        = $00FF03   ; Carry set: A = character (high byte 0).
                          ; Carry clear: nothing received, A = 0.
                          ; Only the foreground process gets input; for any
                          ; other process this always returns carry clear.
                          ; Ctrl-Z never arrives: it pauses the foreground
                          ; process and returns the keyboard to the shell.
K_PUTS        = $00FF06   ; A = 16-bit address of a zero-terminated string
                          ; in the caller's data bank.
K_YIELD       = $00FF09   ; Give the rest of this time slice to the next process.
K_EXIT        = $00FF0C   ; End this process. Does not return. Returning from
                          ; the process's entry point with RTL does the same.
                          ; If it had the keyboard, the shell gets it back.
K_GETPID      = $00FF0F   ; A = this process's bank number (1-7).
K_TICKS       = $00FF12   ; A = low 16 bits of the timer tick count.
K_CON_LOCK    = $00FF15   ; Take the console lock, yielding while another process
                          ; holds it. Not recursive.
K_CON_UNLOCK  = $00FF18   ; Release the console lock, if this process holds it.
K_LOAD        = $00FF1B   ; Receive a file with XMODEM/CRC over the console.
                          ; A = address of a 3-byte table (low, high, bank):
                          ; where the file goes. Writes whole 128-byte blocks.
K_SAVE        = $00FF1E   ; Send memory as a file with XMODEM/CRC.
                          ; A = address of a 3-byte table: the first byte.
                          ; X = address of a 3-byte table: the byte count.
                          ; For both: the tables are in the caller's data
                          ; bank; carry set on success, clear on failure,
                          ; cancel (ESC or CAN), or if the caller isn't the
                          ; foreground process. Only one transfer runs at a
                          ; time, and no other output appears during it.

TICK_HZ       = 200       ; timer ticks per second (5 ms slices)
