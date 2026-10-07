# 816os: Preemptive Multitasking OS for the 65C816 Breadboard Computer

Design document, plus a first kernel that implements it. The kernel passes its tests in a simulator (`tools/sim816.py`) but has not run on the hardware yet. See section 0 for what's built and what to check before burning a ROM.

## 0. Status and building

Needs the cc65 toolchain (`ca65`, `ld65`) and Python 3 for the simulator and tests.

```
make            # build/816os.bin: 32 KB ROM image for $8000-$FFFF
make test       # build the test ROM, run the simulator tests
make run        # run the demo ROM in the simulator; typed keys go to the ACIA, Ctrl-] quits
```

**Using it:** the machine boots to a shell prompt (`> `). Nothing else runs until you start it.

```
> run ticker          start a program from ROM in the first free bank, in the foreground
[1] ticker
ticker 1: 0000
^Z                    Ctrl-Z pauses the foreground program and returns to the shell
[1] ticker paused
> bg 1                resume it in the background (fg 1 would resume it in the foreground)
> ps                  list processes, by bank
> kill 1              end it; kill 0 restarts the shell
> 21000: a9 00        Wozmon-style store, examine (21000) and block examine (21000.2100f)
> load 21000          receive a file with XMODEM/CRC into memory from $02:1000
> save 21000.213ff    send $02:1000-$02:13FF with XMODEM/CRC
> help                commands and the programs in ROM
```

Input is case-insensitive. ESC cancels a line, or stops a long examine, or aborts a transfer. Only the foreground process receives keyboard input.

**What's built:** boot into native mode, the per-process direct pages and stacks (section 5), the 13-byte context frame and timer-driven round-robin scheduler (sections 6-8 and 10), interrupt-driven ACIA receive, paced transmit, the console lock (section 13), and a JSL jump table of kernel services. Beyond the design above:

- **Processes are numbered by bank.** Slot 0 is the shell, running from ROM in bank 0; slots 1-7 are programs in banks 1-7. Each slot is free, ready or paused, and the scheduler only picks ready ones.
- **The shell is part of the kernel** (`kernel/shell.s`), scheduled like any process. If it is killed or hits `BRK`, the kernel starts a fresh one. Its memory commands follow be6502's `rom/wozmon.s` (24-bit addresses), without Wozmon's run and XMODEM commands. It checks a whole line before running it, so a mistyped command does nothing rather than half-running as hex. Nothing stops you examining or storing anywhere, including the kernel's memory and the I/O window; reading `$7000`, `$7001`, `$6004` or `$6008` changes the ACIA's or VIA's state and can stall the kernel.
- **Foreground and Ctrl-Z.** One process at a time gets keyboard input. The receive interrupt catches Ctrl-Z itself: it pauses the foreground process, drops the console lock if that process held it, and gives the keyboard back to the shell.
- **XMODEM save and load** (`kernel/xmodem.s`), ported from be6502's `rom/xmodem.s` with the protocol handling unchanged: CRC only, 24-bit addresses across bank boundaries, raw memory images in whole 128-byte blocks (a load can write up to 127 bytes of padding past the end of the file). Timeouts count the kernel's timer ticks instead of a timed loop, and the shell yields while it waits, so other processes keep running. For the whole transfer the shell holds the console lock and turns off Ctrl-Z, so a `$1A` byte in a file is just data.
- **Output waits for the console lock.** While one process holds the lock, `K_PUTC` and `K_PUTS` from any other process wait, even if that process never takes the lock itself. Nothing else can print in the middle of a locked line or a transfer.
- **Idle process.** After boot, the boot code becomes the idle loop (`WAI`) on the kernel stack, in a pseudo-slot 8 that only runs when no process is ready. While the shell is waiting for input it polls and yields, so idle rarely runs yet; blocking input would fix that.
- **Yield is `COP`.** `COP` pushes the same frame as an interrupt, so the yield handler shares the switch code. The next process gets the rest of the current slice; timer 1 is not restarted, so ticks stay evenly spaced.
- **Exit.** `K_EXIT`, or `RTL` from a process's entry point, frees its slot. `BRK` in a process kills it and records the slot and address. Either way, the kernel releases the console lock if the process held it.
- **Programs are loaded from ROM on demand.** Each image is linked at $0000 (`cfg/app.cfg`). `run` copies it with `MVN` into $0000 of the first free bank. Running a program twice gives two copies in two banks.

**Layout:**

| Path | Contents |
|---|---|
| `include/os816.inc` | Kernel service addresses and calling rules, for programs |
| `include/hw.inc` | VIA and ACIA addresses and settings |
| `kernel/` | Boot, scheduler and interrupt handlers, console driver, shell, jump table, vectors |
| `apps/` | Demo programs (`ticker`, `leds`, `echo`) and the demo ROM's program table |
| `tests/` | Simulator tests and the test ROM's programs |
| `tools/sim816.py` | 65C816 + W65C22 + W65C51N simulator |

**Kernel services:** call with `JSL` to the fixed addresses in `include/os816.inc`: `K_PUTC`, `K_GETC`, `K_PUTS`, `K_YIELD`, `K_EXIT`, `K_GETPID`, `K_TICKS`, `K_CON_LOCK`, `K_CON_UNLOCK`. They keep the caller's register widths, X, Y, D and data bank register. A service that may be preempted keeps its working state on the caller's stack (`K_PUTS` points D at its stack frame), so two processes can be inside the same service at once.

**The simulator** models the CPU, the VIA timers and port B, and the ACIA. It includes the W65C51N's stuck transmit-empty bit and counts receive overruns and characters sent too close together. Cycle counts are approximate, so timings are close to the hardware's but not exact. The tests drive the machine through the shell, as a person at the terminal would. They cover register preservation under preemption (a torture program checks A, B, X, Y, D, the data bank register and register widths while timer and ACIA interrupts switch processes), running one image in several banks, Ctrl-Z, `fg`, `bg` and `kill`, foreground-only input, exit, BRK recovery with the console lock held, restarting the shell, memory examine and store, line editing, and 200-character receive bursts with no overruns. An XMODEM/CRC peer written in Python stands in for the terminal program to test `save` and `load`: every byte value, bank boundaries, damaged and resent blocks, cancelling from either end, and a background process printing throughout.

**Matches be6502** (`rom/bios.s`, `pld/decode.pld`): the memory map and I/O slots, the ACIA's control (`$10`) and command (`$89`) values, and the timer 2 transmit delay (`TX_CYCLES`' formula, 572 cycles at 6 MHz). The simulator uses the GAL's decode.

**No receive flow control, unlike the BIOS.** The kernel writes the ACIA command register once at boot and never changes it, so RTS stays asserted. The BIOS stops the sender by writing `$01`, but on the 6551 family that also turns the transmitter off, which would stall every process printing to the console, and making output wait for it can deadlock (a process stuck printing never reads the input that would let the sender resume). XMODEM sends 133-byte blocks and waits for each acknowledgement, so it fits in the 256-byte ring without flow control. A long paste while no process reads will lose characters: the receive interrupt drops bytes once the ring is full. Revisit once processes can block waiting for input.

**Still to check or decide before burning a ROM:**

- **VIA IRQ wiring** (section 11): the chip variant decides how its IRQ output can share the line with the ACIA.
- **Port A** is left untouched. Port B is set to all outputs for the LEDs.

## 1. Summary

- One process per RAM bank (banks $01–$07), so up to seven processes, plus the shell, which runs from ROM as the process in bank 0.
- No memory protection. Processes stay in their own bank by convention.
- VIA timer 1 fires a periodic interrupt. On each tick, the interrupt handler saves the running process's registers on that process's own stack, then resumes the next process.
- Nothing is copied on a context switch. Each process has its own direct page and its own stack in bank 0, permanently. Switching processes is just switching the stack pointer.
- Every process runs in native mode. Emulation-mode programs (wozmon, MS BASIC) run alone on bare metal with the kernel stopped.

## 2. Hardware this design assumes

| Item | Value |
|---|---|
| CPU clock (PHI2) | 6 MHz |
| Bank 0 RAM | $0000–$5FFF (24 KB) |
| VIA | $6000 |
| ACIA (W65C51N) | $7000 |
| ROM | $8000–$FFFF |
| Banks $01–$07 | 64 KB RAM each |
| ACIA | 115200 bps, interrupt-driven receive into a 256-byte circular buffer |
| VIA timer 2 | Already used for the ACIA transmit delay (W65C51N status-flag bug workaround) |
| VIA port B | 8 LEDs, including PB7 |

Timer 1 is free, so the scheduler uses it.

## 3. Why no copying is needed

On a 6502, zero page is fixed at $0000 and the stack at $0100, so a context switch has to copy them out and in. The 65C816 removes both limits in native mode:

- **Direct Register (D).** A 16-bit register added to every direct page address. An instruction like `LDA $12` encodes only the offset $12; the CPU adds D at run time. Code assembled for "zero page at $00" runs unchanged with its direct page anywhere in bank 0. (W65C816S datasheet section 2.6. The datasheet calls it the "Direct Register"; "direct page register" appears only in its introduction.)
- **Stack pointer (S).** 16 bits wide in native mode, so the stack can sit anywhere in bank 0. (Datasheet section 2.11.)

Each process gets a permanent direct page and stack in bank 0. The scheduler only changes S; the process's own D is saved and restored along with its other registers.

## 4. Rules every process must follow

These rules are what make one binary loadable into any of banks 1–7 without relocation.

1. **Native mode only.** In emulation mode, an interrupt clears the program bank register without saving it, and the return-from-interrupt instruction doesn't restore it (datasheet section 7.11.2), so code outside bank 0 can't survive an interrupt. Emulation mode also forces the stack into page 1 (section 2.11), so two emulation-mode processes would collide.
2. **Use 16-bit (absolute) addresses for the process's own code and data.** Absolute addresses take their bank from the data bank register, and `JSR`/`JMP` take theirs from the program bank register. Both are set to the process's own bank, so the same code works in any bank.
3. **No long (24-bit) addresses to the process's own code or data.** `JSL`, `JML`, and long loads and stores encode the bank in the instruction. Use them only for kernel calls and IPC.
4. **Watch for direct page variables that silently become absolute addresses.** Example: `LDA zpvar,Y`. `LDA` has no direct-page-indexed-by-Y form, so the assembler emits absolute,Y (datasheet section 6.3.3.5). Absolute,Y goes through the data bank register, not D, so the access hits $bb:00xx in the process's bank instead of its direct page. Use `(zpptr),Y`, or index with X instead.
5. **No hardcoded `$0100,X` stack indexing.** Common in 6502 code that reads the stack pointer into X. With per-process stacks outside page 1, it reads the wrong memory.

## 5. Bank 0 layout

| Range | Size | Use |
|---|---|---|
| $0000–$00FF | 256 B | Kernel direct page (includes ACIA buffer head and tail pointers) |
| $0100–$01FF | 256 B | Kernel variables and process table |
| $0200–$09FF | 8 × 256 B | Direct pages: bank *n*'s process at $0200 + *n* × $100 (bank 0 is the shell) |
| $0A00–$0AFF | 256 B | Shell input line |
| $0B00–$4AFF | 8 × 2 KB | Stacks: bank *n*'s process occupies $0B00 + *n* × $0800 up to $0B00 + (*n*+1) × $0800 − 1 |
| $4B00–$4B83 | 132 B | XMODEM block buffer |
| $4B84–$5CFF | ~8.4 KB | Free |
| $5D00–$5DFF | 256 B | ACIA receive buffer |
| $5E00–$5FFF | 512 B | Kernel stack (top at $5FFF): boot, then the idle loop |

Totals exactly 24 KB.

Every direct page is page-aligned. The datasheet notes direct addressing takes an extra cycle when the low byte of D isn't zero (section 3.5.17).

- **ACIA buffer at $5D00.** The BIOS keeps its buffer at $0300 (`INPUT_BUFFER` in `rom/bios.cfg`), which is bank 1's direct page here. That's fine while 816os is its own ROM image. If the BIOS and the OS ever share a ROM, the BIOS buffer has to move.
- **Kernel stack shrunk from 768 to 512 bytes** to fit the ACIA buffer. Boot, idle, and handler work should need well under 256 bytes.

Process slot table (constants in ROM):

| Slot | Bank | Direct page | Initial stack top |
|---|---|---|---|
| 0 (shell) | $00 | $0200 | $12FF |
| 1 | $01 | $0300 | $1AFF |
| 2 | $02 | $0400 | $22FF |
| 3 | $03 | $0500 | $2AFF |
| 4 | $04 | $0600 | $32FF |
| 5 | $05 | $0700 | $3AFF |
| 6 | $06 | $0800 | $42FF |
| 7 | $07 | $0900 | $4AFF |

## 6. The saved context frame

When a process is not running, its whole register state sits on top of its own stack as a 13-byte frame. The process table stores only each process's saved stack pointer.

The interrupt pushes the first four bytes (program bank, return address high, return address low, status flags; datasheet section 2.18). The handler pushes the rest. With T as the stack pointer value at the moment the interrupt arrived:

| Address | Contents | Pushed by |
|---|---|---|
| T | Program bank register | Interrupt |
| T−1 | Return address, high byte | Interrupt |
| T−2 | Return address, low byte | Interrupt |
| T−3 | Status flags (includes M and X width bits) | Interrupt |
| T−4, T−5 | Accumulator, high then low byte | `PHA` (16-bit) |
| T−6, T−7 | X, high then low | `PHX` (16-bit) |
| T−8, T−9 | Y, high then low | `PHY` (16-bit) |
| T−10, T−11 | Direct Register, high then low | `PHD` |
| T−12 | Data bank register | `PHB` |

Saved stack pointer = T − 13.

Why the handler pushes in 16-bit mode:
- The accumulator's high byte (B) survives a switch to 8-bit mode and can hold live data, for example via `XBA` (datasheet sections 2.4 and 7.23). Saving only the low 8 bits would lose it.
- With 8-bit index registers, the high bytes of X and Y are forced to zero (section 2.7), so pushing 16 bits is harmless.
- The return-from-interrupt instruction restores the status flags, which restores each process's register widths.

## 7. Native-mode interrupt handler

The native-mode IRQ vector at $00FFEE points here. This is separate from the emulation-mode vector at $00FFFE.

On entry, the data bank register and Direct Register still belong to the interrupted process. Only the program bank register is cleared (datasheet section 7.11.1). The handler sets its own before touching kernel variables.

```asm
.p816

VIA_T1CL    = $6004   ; read: timer 1 low counter (reading clears the timer 1 flag)
VIA_T1CH    = $6005   ; write: timer 1 high counter (loads counter, starts countdown)
VIA_ACR     = $600B   ; auxiliary control register
VIA_IFR     = $600D   ; interrupt flag register
VIA_IER     = $600E   ; interrupt enable register
ACIA_STATUS = $7001   ; bit 7 set = ACIA requested an interrupt

NUM_PROCS   = 7

irq_native:
    rep #$30                 ; accumulator and index registers to 16 bits
    .a16
    .i16
    pha                      ; save the process's accumulator (all 16 bits)
    phx                      ; save X
    phy                      ; save Y
    phd                      ; save its Direct Register
    phb                      ; save its data bank register

    lda #$0000
    tcd                      ; kernel direct page at $0000
    phk
    plb                      ; data bank = 0 (handler runs in bank 0)

    sep #$30                 ; 8-bit registers for the device checks
    .a8
    .i8
    lda ACIA_STATUS
    bpl @no_acia             ; bit 7 clear: ACIA didn't interrupt
    jsr acia_rx_service      ; existing receive routine; A holds the status byte
@no_acia:
    lda VIA_IFR
    and #%01000000           ; timer 1 flag
    beq @same_process        ; not a timer tick: return to the same process

    lda VIA_T1CL             ; acknowledge the timer 1 interrupt
    rep #$30
    .a16
    .i16
    ldx current_proc         ; slot number times 2
    tsc
    sta saved_stack,x        ; park this process's stack pointer
    inx                      ; round-robin to the next slot
    inx
    cpx #NUM_PROCS*2
    bcc @switch
    ldx #0
@switch:
    stx current_proc
    lda saved_stack,x
    tcs                      ; switch to the next process's stack
    bra restore

@same_process:
    rep #$30
restore:
    .a16
    .i16
    plb                      ; data bank register
    pld                      ; Direct Register
    ply
    plx
    pla
    rti                      ; pulls flags, return address, and program bank
```

Notes:
- **Both interrupt sources are checked on every entry.** The IRQ line is level-sensitive, so if the ACIA and the timer are both pending, the ACIA is serviced and the timer switch still happens. Anything still pending re-triggers the interrupt immediately after the return.
- **`acia_rx_service` runs with 8-bit registers,** the kernel direct page, and data bank 0. That matches what an emulation-era routine expects, as long as its variables live in the kernel direct page and bank 0.
- **Cost:** roughly 130 cycles for a timer tick with a switch, by the datasheet's cycle counts.

## 8. Creating processes

`create_process` builds the 13-byte frame by temporarily pointing S at the new process's stack and pushing the frame with the same push instructions the handler uses. That keeps the byte order right by construction.

```asm
; Call with interrupts disabled, native mode, 16-bit registers.
; In:  X         = slot number * 2 (0, 2, ... 12)
;      new_entry = 16-bit entry address within the process's bank
;      new_bank  = the process's bank ($01-$07)
create_process:
    .a16
    .i16
    tsc
    sta kernel_sp_save       ; park the kernel's stack pointer
    lda stack_top,x
    tcs                      ; temporarily use the new process's stack

    sep #$20
    .a8
    lda new_bank
    pha                      ; program bank (pulled last by the return-from-interrupt)
    rep #$20
    .a16
    lda new_entry
    pha                      ; entry address, high byte then low
    sep #$20
    .a8
    lda #$00                 ; initial flags: 16-bit registers, interrupts enabled
    pha
    rep #$20
    .a16
    pea $0000                ; accumulator
    pea $0000                ; X
    pea $0000                ; Y
    lda direct_page,x
    pha                      ; the slot's Direct Register value
    sep #$20
    .a8
    lda new_bank
    pha                      ; data bank register = the process's own bank
    rep #$20
    .a16

    tsc
    sta saved_stack,x        ; the scheduler resumes the process from here
    lda kernel_sp_save
    tcs                      ; back to the kernel's stack
    rts

; Constant tables (ROM)
stack_top:   .word $14FF, $20FF, $2CFF, $38FF, $44FF, $50FF, $5CFF
direct_page: .word $0200, $0300, $0400, $0500, $0600, $0700, $0800
```

Set the initial flags byte to `$30` instead of `$00` if a process should start with 8-bit registers.

## 9. Boot sequence

```asm
reset:                       ; the CPU starts in emulation mode
    sei
    clc
    xce                      ; switch to native mode
    rep #$30
    .a16
    .i16
    lda #$5FFF
    tcs                      ; kernel stack
    lda #$0000
    tcd                      ; kernel direct page
    phk
    plb                      ; data bank = 0

    ; ... initialize ACIA and VIA as now ...
    ; ... call create_process for each process to start ...
    ; ... start the timer (section 10) ...

    ldx #0
    stx current_proc
    lda saved_stack          ; slot 0's saved stack pointer
    tcs
    jmp restore              ; "returns" into process 0; its flags enable interrupts
```

Interrupts stay disabled until that first return-from-interrupt pulls a flags byte with interrupts enabled.

## 10. Timer setup

VIA timer 1 in free-run (continuous) mode reloads itself from its latches on every count to zero, so the ticks stay evenly spaced regardless of how quickly the CPU responds (W65C22 datasheet section 2.7). It counts down at the PHI2 rate.

```asm
    sep #$20
    .a8
    lda VIA_ACR
    and #%00111111           ; clear the timer 1 mode bits, keep timer 2 / shift / latch bits
    ora #%01000000           ; timer 1: continuous interrupts, PB7 output disabled
    sta VIA_ACR
    lda #<SLICE_COUNT
    sta VIA_T1CL             ; low byte to the latch
    lda #>SLICE_COUNT
    sta VIA_T1CH             ; high byte: loads the counter and starts it
    lda #%11000000           ; bit 7 = set, bit 6 = timer 1
    sta VIA_IER
    rep #$20
    .a16
```

Points:
- **The read-modify-write on the auxiliary control register** leaves timer 2's mode alone, since the ACIA transmit delay uses it.
- **Keep auxiliary control register bit 7 at 0.** Setting it would let timer 1 drive PB7, which is one of the LEDs.

Counts at 6 MHz:

| Slice | `SLICE_COUNT` | Overhead at ~130 cycles per switch |
|---|---|---|
| 1 ms | 6,000 | ~2.2% |
| 5 ms (suggested start) | 30,000 | ~0.4% |
| 10 ms | 60,000 | ~0.2% |
| Maximum | 65,535 | ≈10.9 ms |

The WDC datasheet doesn't state the exact reload overhead in free-run mode. Older 6522 documentation gives a period of N+2 cycles. To measure it, temporarily set auxiliary control register bit 7 so PB7 toggles on each timeout, and look at PB7 on the logic analyzer.

## 11. Interrupt wiring check (hardware)

The VIA and ACIA share the CPU's IRQ line, and how the VIA's IRQ output can be shared depends on which W65C22 variant is installed (W65C22 datasheet section 3.5):

- **W65C22N:** open-drain IRQ output. It can be wired directly together with other open-drain IRQ outputs and a pull-up resistor.
- **W65C22S:** totem-pole IRQ output that actively drives high. It must not be wired directly to another device's IRQ output. WDC suggests either logically OR-ing the IRQ signals (with a gate) or putting a low-voltage diode (under 0.5 V forward drop) in series with the VIA's IRQ output.

Before enabling timer interrupts: check the letter after "W65C22" on the chip, and check how (or whether) the VIA's IRQ pin currently connects to the CPU.

## 12. Kernel services and shared code

Kernel services are called through the planned fixed jump table in ROM, using `JSL`.

- **They run on the caller's stack, with the caller's Direct Register and data bank register.** A service that uses kernel variables must either use long addressing or save D and the data bank register (`PHD`/`PHB`), set its own, and restore them on exit.
- **Shared state needs protection.** This covers the ACIA receive buffer, the ACIA transmit path with its timer 2 delay, and the process table. Two tools:
  - **Short critical sections:** `PHP` / `SEI` ... `PLP`. This restores whatever interrupt state the caller had.
  - **Long ownership (for example, the console):** a lock (section 13), not disabled interrupts.

**Hard limit on how long interrupts can stay disabled.** At 115200 bps with 10 bits per character, a character arrives every ~86.8 µs, about 520 cycles at 6 MHz. The ACIA only holds a received byte until the next one finishes arriving, so if interrupts stay off for much longer than one character time, received bytes are lost. This rules out disabling interrupts around the whole transmit routine with its inter-byte delays. The interrupt handler itself (~130 cycles) fits comfortably.

## 13. Locking

On a single CPU, interrupts are only taken between instructions, so any single instruction is atomic with respect to the scheduler. `TSB` (test and set bits) reads a memory byte, sets the Z flag from (accumulator AND memory) using the old value, and sets the bits, all in one instruction. That makes it a test-and-set.

```asm
; 8-bit accumulator. console_lock is a kernel variable in bank 0.
acquire_console:
    lda #%00000001
@spin:
    tsb console_lock         ; set the bit; Z=1 if it was clear beforehand
    bne @spin                ; already held: try again
    rts

release_console:
    lda #%00000001
    trb console_lock         ; clear the bit
    rts
```

Spinning burns the rest of the time slice. A yield call (section 17) would fix that later.

Because the lock uses ordinary 16-bit addressing, the caller's data bank must be 0 here. Otherwise use a long-addressed variable, or run this inside a kernel service that has set its own data bank.

## 14. IPC (not decided)

Keep IPC out of bank 0. Bank 0 is the only scarce memory, and it gives IPC no advantage: ordinary 16-bit addresses take their bank from each process's data bank register, so every process would need long addressing to reach a shared area anyway, and long addressing reaches any bank equally.

Options:

1. **Inbox in each process's own bank** at a fixed offset (for example, $bb:E000–$bb:FFFF). Senders write to it with long addressing.
2. **Kernel-mediated copy** using the block-move instruction `MVN`, at 7 cycles per byte (datasheet Table 3-1). Gotchas:
   - `MVN` leaves the data bank register set to the destination bank (section 7.18), so restore it afterward.
   - It uses X and Y as source and destination addresses, so run it with 16-bit index registers.
   - It's interruptible, so long copies don't block the scheduler.
3. **Dedicate one bank to kernel and IPC.** Simple and roomy, but leaves six process banks.

Use `TSB`/`TRB` (section 13) to guard mailbox state.

## 15. Emulation-mode programs (wozmon, MS BASIC)

These run alone, on bare metal:

1. Stop the timer and disable interrupts.
2. Switch to emulation mode and run the program in bank 0. It owns everything in bank 0 below $8000, including page 1 as its stack.
3. When it exits, return to native mode.

The program overwrites every process's direct page and stack, plus the process table. Choice for exit, not yet decided:

- **OS restarts.** Exiting the guest means a fresh boot of the OS. Simplest.
- **Save and restore bank 0.** Copy $00:0000–$5FFF into a holding area with `MVN` before launching, and copy it back on exit. About 172,000 cycles (~29 ms at 6 MHz) each way. The suspended processes resume where they left off. Costs a 24 KB holding area in one of banks 1–7.

## 16. Porting 8-bit code to native mode

Running 6502-style code in native mode with 8-bit registers mostly works. Spots that break:

- `$0100,X` stack indexing (rule 5 above).
- **Direct page indexed addressing doesn't wrap within the page in native mode.** In emulation mode with the low byte of D zero, `$F0,X` with X = $20 wraps to $10. In native mode it reaches $0110 (relative to D) instead (datasheet section 7.2 and Table 7-1).
- **Different interrupt and break vectors** in native mode (datasheet Table 5-3). This only matters for programs that hook interrupts.

## 17. Known issue in the current build

Programs currently run in bank 1 in emulation mode, and ACIA receive is interrupt-driven. If an ACIA interrupt arrives while bank-1 code is executing, the program bank is lost and the return lands at the same address in bank 0 (datasheet section 7.11.2). If that address is $8000 or higher, it lands in ROM. Either keep interrupts disabled while emulation-mode code runs outside bank 0, or move that code to native mode.

## 18. Open decisions

| Decision | Current leaning |
|---|---|
| Time slice length | 5 ms (`SLICE_COUNT` in `include/kernel.inc`); tune for console responsiveness |
| IPC model | Undecided (section 14) |
| Emulation guest exit behavior | Undecided (section 15) |
| ACIA buffer location | $5D00 in the 816os ROM; the BIOS's is at $0300 (section 5) |
| Handler switches to the kernel stack after saving registers | Optional. Isolates handler stack use from process stacks, at a few cycles per interrupt |
| Blocking | Later phase. The idle loop is built (section 0); processes still wait by yielding in a loop |
| Yield call | Built, as `COP` (section 0). `K_CON_LOCK` yields while it waits |
| Receive flow control | Off (section 0). Revisit with blocking input |
| Shell | Built into the kernel as the process in bank 0 (section 0) |
| Loading new programs | `load` puts a file anywhere in memory (section 0). Starting loaded code as a process is still to do |
| Filesystem | Later. Files are raw memory images sent over XMODEM |
| VIA IRQ wiring | Check the chip variant and the wiring (section 11) |

## 19. References

- WDC W65C816S datasheet (March 13, 2024): sections 2.4 (accumulator), 2.6 (Direct Register), 2.11 (stack pointer), 2.18 (interrupt push order), 3.5.17 (direct page alignment), 6.3.3.5 (address mode extension), 7.11 (interrupts and the bank registers), 7.18 (`MVN`/`MVP` and the data bank), 7.20 and 7.23 (register transfers and the B accumulator), Tables 3-1 and 5-3.
- WDC W65C22 datasheet (Sept 13, 2010): sections 2.5–2.7 (timer 1), 2.14 (interrupt operation), 3.5 (IRQ output, N vs S), Table 2-8 (auxiliary control register).
