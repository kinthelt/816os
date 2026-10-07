; hw.inc - breadboard computer hardware (README section 2)
;
; Addresses and ACIA settings follow the be6502 project (rom/bios.s and
; pld/decode.pld).
;
; All of these live in bank 0. Kernel code runs with the data bank register
; at 0 and can use them as plain absolute addresses. A process runs with its
; own bank in the data bank register, so it must use long addressing to reach
; them, for example `sta f:VIA_PORTB`.

CPU_HZ      = 6000000
BAUD        = 115200

; W65C22 VIA: I/O slot Y0, $6000-$63FF (registers mirrored on A0-A3)
VIA         = $6000
VIA_PORTB   = VIA + $0
VIA_PORTA   = VIA + $1
VIA_DDRB    = VIA + $2
VIA_DDRA    = VIA + $3
VIA_T1CL    = VIA + $4      ; read: counter low, clears the T1 flag. write: latch low
VIA_T1CH    = VIA + $5      ; write: latch high, loads the counter, clears the T1 flag
VIA_T1LL    = VIA + $6
VIA_T1LH    = VIA + $7
VIA_T2CL    = VIA + $8      ; read: counter low, clears the T2 flag. write: latch low
VIA_T2CH    = VIA + $9      ; write: loads the counter and starts it, clears the T2 flag
VIA_SR      = VIA + $A
VIA_ACR     = VIA + $B
VIA_PCR     = VIA + $C
VIA_IFR     = VIA + $D
VIA_IER     = VIA + $E

VIA_INT_T1  = %01000000     ; IFR / IER bit for timer 1
VIA_INT_T2  = %00100000     ; IFR / IER bit for timer 2

; W65C51N ACIA: I/O slot Y4, $7000-$73FF (registers mirrored on A0-A1)
ACIA        = $7000
ACIA_DATA   = ACIA + 0
ACIA_STATUS = ACIA + 1      ; read clears the IRQ bit. write: programmed reset
ACIA_CMD    = ACIA + 2
ACIA_CTRL   = ACIA + 3

ACIA_ST_IRQ  = %10000000
ACIA_ST_RDRF = %00001000    ; receive data register full

; Control: 1 stop bit, 8 data bits, internal baud rate generator, SBR = 0000.
; On the W65C51N with a 1.8432 MHz crystal, SBR 0000 selects 115200 baud.
ACIA_CTRL_VALUE = %00010000
; Command: no parity, no echo, RTS low with transmit interrupts off,
; receive interrupts on, DTR low (receiver enabled). Bits 7-6 only matter
; when parity is enabled (bit 5); $89 matches be6502's ACIA_SETUP.
; The kernel never changes it after boot: no RTS flow control (README
; section 0), because raising RTS on the 6551 also turns the transmitter off.
ACIA_CMD_VALUE  = %10001001
