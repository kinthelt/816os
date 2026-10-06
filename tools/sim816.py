#!/usr/bin/env python3
"""sim816 - simulator for the 65C816 breadboard computer.

Models the machine in README section 2, with the address decode from
be6502's pld/decode.pld: a W65C816S, RAM in bank 0 below $6000 and in banks
1-7, an I/O window at $6000-$7FFF split into 1 KB slots (W65C22 VIA in slot
0 at $6000, W65C51N ACIA in slot 4 at $7000), a 32 KB ROM at $8000, and 32 KB
of video RAM at bank $80.

On the hardware, banks $08-$7F alias banks $00-$07 and the empty I/O slots
read as a floating bus. The simulator treats touching any of those, or
writing to ROM, as an error, since the OS should never do it.

Cycle counts are approximate (close to the datasheet, but not exact), so
timer and serial timings are close to the hardware's, not identical.
The ACIA model includes the W65C51N's stuck transmit-empty bit, and it counts
characters written before the previous one could have finished sending.

Run a ROM interactively:

    python3 tools/sim816.py build/816os.bin

Keys you type go to the ACIA's receiver; what it sends is printed. Ctrl-C
quits. The simulator runs slower than the real 6 MHz machine.
"""

import argparse
import os
import sys

CPU_HZ = 6_000_000
BAUD = 115_200
CHAR_CYCLES = CPU_HZ * 10 / BAUD  # one 8N1 character, in CPU cycles

FLAG_C = 0x01
FLAG_Z = 0x02
FLAG_I = 0x04
FLAG_D = 0x08
FLAG_X = 0x10
FLAG_M = 0x20
FLAG_V = 0x40
FLAG_N = 0x80


class SimError(Exception):
    pass


# ---------------------------------------------------------------------------
# W65C22 VIA (timers, port B, interrupt flags)

class VIA:
    def __init__(self):
        self.orb = 0
        self.ddrb = 0
        self.ora = 0
        self.ddra = 0
        self.t1c = 0xFFFF
        self.t1l = 0xFFFF
        self.t1_armed = False
        self.t2c = 0xFFFF
        self.t2l_lo = 0xFF
        self.t2_armed = False
        self.acr = 0
        self.pcr = 0
        self.ifr = 0
        self.ier = 0
        self.portb_log = []    # (cycle, value) each time port B's output changes
        self.now = lambda: 0

    def irq(self):
        return (self.ifr & self.ier & 0x7F) != 0

    def portb(self):
        return self.orb & self.ddrb

    def read(self, reg):
        if reg == 0x0:
            return self.orb & self.ddrb
        if reg == 0x1:
            return self.ora & self.ddra
        if reg == 0x2:
            return self.ddrb
        if reg == 0x3:
            return self.ddra
        if reg == 0x4:
            self.ifr &= ~0x40
            return self.t1c & 0xFF
        if reg == 0x5:
            return (self.t1c >> 8) & 0xFF
        if reg == 0x6:
            return self.t1l & 0xFF
        if reg == 0x7:
            return self.t1l >> 8
        if reg == 0x8:
            self.ifr &= ~0x20
            return self.t2c & 0xFF
        if reg == 0x9:
            return (self.t2c >> 8) & 0xFF
        if reg == 0xB:
            return self.acr
        if reg == 0xC:
            return self.pcr
        if reg == 0xD:
            return self.ifr | (0x80 if self.irq() else 0)
        if reg == 0xE:
            return self.ier | 0x80
        return 0

    def write(self, reg, v):
        if reg == 0x0:
            old = self.portb()
            self.orb = v
            if self.portb() != old:
                self.portb_log.append((self.now(), self.portb()))
        elif reg == 0x1:
            self.ora = v
        elif reg == 0x2:
            old = self.portb()
            self.ddrb = v
            if self.portb() != old:
                self.portb_log.append((self.now(), self.portb()))
        elif reg == 0x3:
            self.ddra = v
        elif reg in (0x4, 0x6):
            self.t1l = (self.t1l & 0xFF00) | v
        elif reg == 0x5:
            self.t1l = (self.t1l & 0x00FF) | (v << 8)
            self.t1c = self.t1l
            self.ifr &= ~0x40
            self.t1_armed = True
        elif reg == 0x7:
            self.t1l = (self.t1l & 0x00FF) | (v << 8)
            self.ifr &= ~0x40
        elif reg == 0x8:
            self.t2l_lo = v
        elif reg == 0x9:
            self.t2c = (v << 8) | self.t2l_lo
            self.ifr &= ~0x20
            self.t2_armed = True
        elif reg == 0xB:
            if v & 0xA0:
                raise SimError("VIA ACR $%02X: PB7 output and T2 pulse counting "
                               "are not modelled" % v)
            self.acr = v
        elif reg == 0xC:
            self.pcr = v
        elif reg == 0xD:
            self.ifr &= ~(v & 0x7F)
        elif reg == 0xE:
            if v & 0x80:
                self.ier |= v & 0x7F
            else:
                self.ier &= ~(v & 0x7F)

    def tick(self, n):
        self.t1c -= n
        while self.t1c < 0:
            if self.acr & 0x40:            # free-run: period is latch + 2
                self.ifr |= 0x40
                self.t1c += self.t1l + 2
            else:                          # one-shot: flag once, keep counting
                if self.t1_armed:
                    self.ifr |= 0x40
                    self.t1_armed = False
                self.t1c += 0x10000
        self.t2c -= n
        if self.t2c < 0:
            if self.t2_armed:
                self.ifr |= 0x20
                self.t2_armed = False
            self.t2c %= 0x10000

    def cycles_to_irq(self):
        """Cycles until a timer could raise an enabled interrupt, or None."""
        best = None
        if self.ier & 0x40 and (self.acr & 0x40 or self.t1_armed):
            best = self.t1c + 1
        if self.ier & 0x20 and self.t2_armed:
            n = self.t2c + 1
            best = n if best is None else min(best, n)
        return best


# ---------------------------------------------------------------------------
# W65C51N ACIA

class ACIA:
    def __init__(self, now):
        self.now = now
        self.data = 0
        self.rdrf = False
        self.overrun = False
        self.irq_flag = False
        self.cmd = 0
        self.ctrl = 0
        self.rx_queue = []        # (arrival cycle, byte), in order
        self.last_rx_time = -CHAR_CYCLES
        self.output = bytearray()
        self.last_tx_time = None
        self.tx_too_soon = 0      # characters sent before the last one finished
        self.rx_overruns = 0      # characters lost because the CPU was too slow

    def rx_irq_enabled(self):
        return (self.cmd & 0x03) == 0x01   # DTR on, receive IRQ not disabled

    def send(self, data, gap=CHAR_CYCLES):
        """Queue bytes to arrive at the receiver, back to back at the line rate."""
        for b in data:
            t = max(self.now(), self.last_rx_time) + gap
            self.rx_queue.append((t, b))
            self.last_rx_time = t

    def poll(self):
        now = self.now()
        while self.rx_queue and self.rx_queue[0][0] <= now:
            _, b = self.rx_queue.pop(0)
            if self.rdrf:
                self.overrun = True
                self.rx_overruns += 1
            else:
                self.data = b
                self.rdrf = True
            if self.rx_irq_enabled():
                self.irq_flag = True

    def next_rx(self):
        return self.rx_queue[0][0] if self.rx_queue else None

    def read(self, reg):
        if reg == 0:
            self.rdrf = False
            self.overrun = False
            return self.data
        if reg == 1:
            v = ((0x80 if self.irq_flag else 0) | 0x10 |   # TDRE always reads 1
                 (0x08 if self.rdrf else 0) | (0x04 if self.overrun else 0))
            self.irq_flag = False
            return v
        if reg == 2:
            return self.cmd
        return self.ctrl

    def write(self, reg, v):
        if reg == 0:
            now = self.now()
            if self.last_tx_time is not None and now - self.last_tx_time < CHAR_CYCLES:
                self.tx_too_soon += 1
            self.last_tx_time = now
            self.output.append(v)
        elif reg == 1:                    # programmed reset
            self.cmd &= 0xE0
            self.overrun = False
            self.irq_flag = False
        elif reg == 2:
            self.cmd = v
        else:
            self.ctrl = v


# ---------------------------------------------------------------------------
# Memory map

class Bus:
    def __init__(self, rom):
        if len(rom) != 0x8000:
            raise SimError("ROM image must be 32768 bytes, got %d" % len(rom))
        self.rom = bytes(rom)
        self.ram = bytearray(8 * 0x10000)  # bank 0 uses only $0000-$5FFF
        self.vram = bytearray(0x8000)
        self.cycles = 0
        self.via = VIA()
        self.via.now = lambda: self.cycles
        self.acia = ACIA(lambda: self.cycles)
        self.errors = []

    def read(self, a):
        bank = a >> 16
        if bank == 0:
            if a < 0x6000:
                return self.ram[a]
            if a >= 0x8000:
                return self.rom[a - 0x8000]
            slot = (a >> 10) & 7
            if slot == 0:
                return self.via.read(a & 0xF)
            if slot == 4:
                return self.acia.read(a & 0x3)
            self.errors.append("read from empty I/O slot $%06X" % a)
            return 0
        if bank < 8:
            return self.ram[a]
        if bank & 0x80:
            return self.vram[a & 0x7FFF]
        self.errors.append("read from $%06X, which aliases bank $%02X"
                           % (a, bank & 7))
        return 0

    def write(self, a, v):
        bank = a >> 16
        if bank == 0:
            if a < 0x6000:
                self.ram[a] = v
            elif a >= 0x8000:
                self.errors.append("write to ROM $%06X" % a)
            else:
                slot = (a >> 10) & 7
                if slot == 0:
                    self.via.write(a & 0xF, v)
                elif slot == 4:
                    self.acia.write(a & 0x3, v)
                else:
                    self.errors.append("write to empty I/O slot $%06X" % a)
        elif bank < 8:
            self.ram[a] = v
        elif bank & 0x80:
            self.vram[a & 0x7FFF] = v
        else:
            self.errors.append("write to $%06X, which aliases bank $%02X"
                               % (a, bank & 7))

    def tick(self, n):
        self.cycles += n
        self.via.tick(n)
        self.acia.poll()

    def irq(self):
        return self.via.irq() or self.acia.irq_flag

    def cycles_to_event(self):
        cands = []
        n = self.via.cycles_to_irq()
        if n is not None:
            cands.append(n)
        t = self.acia.next_rx()
        if t is not None:
            cands.append(max(1, int(t - self.cycles) + 1))
        return max(1, min(cands)) if cands else None


# ---------------------------------------------------------------------------
# W65C816S

# Addressing mode base cycle counts (8-bit operand, no page-cross penalties).
MODE_CYCLES = {
    'imp': 2, 'acc': 2, 'imm': 2, 'dp': 3, 'dpx': 4, 'dpy': 4, 'abs': 4,
    'absx': 4, 'absy': 4, 'long': 5, 'longx': 5, 'ind': 5, 'indx': 6,
    'indy': 5, 'indl': 6, 'indly': 6, 'sr': 4, 'sry': 7, 'rel': 2,
}

# Instructions whose cycle count doesn't follow the mode table.
FIXED_CYCLES = {
    'BRK': 8, 'COP': 8, 'RTI': 7, 'RTS': 6, 'RTL': 6, 'JSL': 8, 'JML': 4,
    'PHA': 3, 'PHX': 3, 'PHY': 3, 'PHB': 3, 'PHK': 3, 'PHP': 3, 'PHD': 4,
    'PLA': 4, 'PLX': 4, 'PLY': 4, 'PLB': 4, 'PLP': 4, 'PLD': 5,
    'PEA': 5, 'PEI': 6, 'PER': 6, 'BRL': 4, 'XBA': 3, 'WAI': 3, 'STP': 3,
    'MVN': 7, 'MVP': 7, 'WDM': 2,
}

RMW = {'ASL', 'LSR', 'ROL', 'ROR', 'INC', 'DEC', 'TSB', 'TRB'}


def _opcode_table():
    t = {}
    # Group 1: ORA AND EOR ADC STA LDA CMP SBC
    g1_modes = {0x01: 'indx', 0x03: 'sr', 0x05: 'dp', 0x07: 'indl', 0x09: 'imm',
                0x0D: 'abs', 0x0F: 'long', 0x11: 'indy', 0x12: 'ind', 0x13: 'sry',
                0x15: 'dpx', 0x17: 'indly', 0x19: 'absy', 0x1D: 'absx', 0x1F: 'longx'}
    for i, mn in enumerate(['ORA', 'AND', 'EOR', 'ADC', 'STA', 'LDA', 'CMP', 'SBC']):
        for off, mode in g1_modes.items():
            t[(i << 5) + off] = (mn, mode)
    # Shifts and rotates
    for i, mn in enumerate(['ASL', 'ROL', 'LSR', 'ROR']):
        base = i << 5
        t[base + 0x06] = (mn, 'dp')
        t[base + 0x0A] = (mn, 'acc')
        t[base + 0x0E] = (mn, 'abs')
        t[base + 0x16] = (mn, 'dpx')
        t[base + 0x1E] = (mn, 'absx')
    more = {
        0x89: ('BIT', 'imm'), 0x24: ('BIT', 'dp'), 0x2C: ('BIT', 'abs'),
        0x34: ('BIT', 'dpx'), 0x3C: ('BIT', 'absx'),
        0xE6: ('INC', 'dp'), 0xEE: ('INC', 'abs'), 0xF6: ('INC', 'dpx'),
        0xFE: ('INC', 'absx'), 0x1A: ('INC', 'acc'),
        0xC6: ('DEC', 'dp'), 0xCE: ('DEC', 'abs'), 0xD6: ('DEC', 'dpx'),
        0xDE: ('DEC', 'absx'), 0x3A: ('DEC', 'acc'),
        0xA2: ('LDX', 'imm'), 0xA6: ('LDX', 'dp'), 0xAE: ('LDX', 'abs'),
        0xB6: ('LDX', 'dpy'), 0xBE: ('LDX', 'absy'),
        0xA0: ('LDY', 'imm'), 0xA4: ('LDY', 'dp'), 0xAC: ('LDY', 'abs'),
        0xB4: ('LDY', 'dpx'), 0xBC: ('LDY', 'absx'),
        0x86: ('STX', 'dp'), 0x8E: ('STX', 'abs'), 0x96: ('STX', 'dpy'),
        0x84: ('STY', 'dp'), 0x8C: ('STY', 'abs'), 0x94: ('STY', 'dpx'),
        0x64: ('STZ', 'dp'), 0x74: ('STZ', 'dpx'), 0x9C: ('STZ', 'abs'),
        0x9E: ('STZ', 'absx'),
        0xE0: ('CPX', 'imm'), 0xE4: ('CPX', 'dp'), 0xEC: ('CPX', 'abs'),
        0xC0: ('CPY', 'imm'), 0xC4: ('CPY', 'dp'), 0xCC: ('CPY', 'abs'),
        0x04: ('TSB', 'dp'), 0x0C: ('TSB', 'abs'),
        0x14: ('TRB', 'dp'), 0x1C: ('TRB', 'abs'),
        0x10: ('BPL', 'rel'), 0x30: ('BMI', 'rel'), 0x50: ('BVC', 'rel'),
        0x70: ('BVS', 'rel'), 0x80: ('BRA', 'rel'), 0x90: ('BCC', 'rel'),
        0xB0: ('BCS', 'rel'), 0xD0: ('BNE', 'rel'), 0xF0: ('BEQ', 'rel'),
        0x82: ('BRL', 'imp'),
        0x00: ('BRK', 'imp'), 0x02: ('COP', 'imp'), 0x40: ('RTI', 'imp'),
        0x20: ('JSR', 'abs'), 0xFC: ('JSR', 'absxind'), 0x22: ('JSL', 'imp'),
        0x60: ('RTS', 'imp'), 0x6B: ('RTL', 'imp'),
        0x4C: ('JMP', 'abs'), 0x6C: ('JMP', 'absind'), 0x7C: ('JMP', 'absxind'),
        0x5C: ('JML', 'long'), 0xDC: ('JML', 'absindl'),
        0x08: ('PHP', 'imp'), 0x28: ('PLP', 'imp'), 0x48: ('PHA', 'imp'),
        0x68: ('PLA', 'imp'), 0xDA: ('PHX', 'imp'), 0xFA: ('PLX', 'imp'),
        0x5A: ('PHY', 'imp'), 0x7A: ('PLY', 'imp'), 0x8B: ('PHB', 'imp'),
        0xAB: ('PLB', 'imp'), 0x0B: ('PHD', 'imp'), 0x2B: ('PLD', 'imp'),
        0x4B: ('PHK', 'imp'), 0xF4: ('PEA', 'imp'), 0xD4: ('PEI', 'imp'),
        0x62: ('PER', 'imp'),
        0x18: ('CLC', 'imp'), 0x38: ('SEC', 'imp'), 0x58: ('CLI', 'imp'),
        0x78: ('SEI', 'imp'), 0xD8: ('CLD', 'imp'), 0xF8: ('SED', 'imp'),
        0xB8: ('CLV', 'imp'), 0xC2: ('REP', 'imp'), 0xE2: ('SEP', 'imp'),
        0xFB: ('XCE', 'imp'), 0xEB: ('XBA', 'imp'),
        0xAA: ('TAX', 'imp'), 0xA8: ('TAY', 'imp'), 0x8A: ('TXA', 'imp'),
        0x98: ('TYA', 'imp'), 0x9A: ('TXS', 'imp'), 0xBA: ('TSX', 'imp'),
        0x9B: ('TXY', 'imp'), 0xBB: ('TYX', 'imp'), 0x1B: ('TCS', 'imp'),
        0x3B: ('TSC', 'imp'), 0x5B: ('TCD', 'imp'), 0x7B: ('TDC', 'imp'),
        0xE8: ('INX', 'imp'), 0xC8: ('INY', 'imp'), 0xCA: ('DEX', 'imp'),
        0x88: ('DEY', 'imp'),
        0x44: ('MVP', 'imp'), 0x54: ('MVN', 'imp'),
        0xCB: ('WAI', 'imp'), 0xDB: ('STP', 'imp'), 0xEA: ('NOP', 'imp'),
        0x42: ('WDM', 'imp'),
    }
    t.update(more)
    assert len(t) == 256, len(t)
    return t


OPCODES = _opcode_table()


class CPU:
    def __init__(self, bus):
        self.bus = bus
        self.table = []
        for code in range(256):
            mn, mode = OPCODES[code]
            self.table.append((getattr(self, 'op_' + mn), mode, mn))
        self.reset()

    # -- state ---------------------------------------------------------------

    def reset(self):
        self.e = 1
        self.p = FLAG_M | FLAG_X | FLAG_I
        self.a = 0
        self.x = 0
        self.y = 0
        self.s = 0x01FF
        self.d = 0
        self.dbr = 0
        self.pbr = 0
        self.pc = self.rd16_bank0(0xFFFC)
        self.waiting = False
        self.stopped = False
        self.extra = 0

    def m16(self):
        return not (self.p & FLAG_M)

    def x16(self):
        return not (self.p & FLAG_X)

    def fix_flags(self):
        if self.e:
            self.p |= FLAG_M | FLAG_X
        if self.p & FLAG_X:
            self.x &= 0xFF
            self.y &= 0xFF

    def setnz(self, v, w16):
        p = self.p & ~(FLAG_N | FLAG_Z)
        if w16:
            if v & 0x8000:
                p |= FLAG_N
            if not (v & 0xFFFF):
                p |= FLAG_Z
        else:
            if v & 0x80:
                p |= FLAG_N
            if not (v & 0xFF):
                p |= FLAG_Z
        self.p = p

    def setflag(self, f, on):
        if on:
            self.p |= f
        else:
            self.p &= ~f

    # -- memory helpers -------------------------------------------------------

    def rd(self, a):
        return self.bus.read(a & 0xFFFFFF)

    def wr(self, a, v):
        self.bus.write(a & 0xFFFFFF, v & 0xFF)

    def rd16_bank0(self, a):
        return self.rd(a & 0xFFFF) | (self.rd((a + 1) & 0xFFFF) << 8)

    def fetch8(self):
        v = self.rd((self.pbr << 16) | self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        return v

    def fetch16(self):
        lo = self.fetch8()
        return lo | (self.fetch8() << 8)

    def fetch24(self):
        lo = self.fetch16()
        return lo | (self.fetch8() << 16)

    def push8(self, v):
        self.wr(self.s, v)
        if self.e:
            self.s = 0x0100 | ((self.s - 1) & 0xFF)
        else:
            self.s = (self.s - 1) & 0xFFFF

    def pull8(self):
        if self.e:
            self.s = 0x0100 | ((self.s + 1) & 0xFF)
        else:
            self.s = (self.s + 1) & 0xFFFF
        return self.rd(self.s)

    def push16(self, v):
        self.push8(v >> 8)
        self.push8(v)

    def pull16(self):
        lo = self.pull8()
        return lo | (self.pull8() << 8)

    def rdw(self, addr, wrap, w16):
        lo = self.rd(addr)
        if not w16:
            return lo
        if wrap:
            a2 = (addr & 0xFF0000) | ((addr + 1) & 0xFFFF)
        else:
            a2 = addr + 1
        self.extra += 1
        return lo | (self.rd(a2) << 8)

    def wrw(self, addr, wrap, v, w16):
        self.wr(addr, v)
        if w16:
            if wrap:
                a2 = (addr & 0xFF0000) | ((addr + 1) & 0xFFFF)
            else:
                a2 = addr + 1
            self.wr(a2, v >> 8)
            self.extra += 1

    # -- addressing modes -----------------------------------------------------
    # Return (24-bit address, wrap) where wrap means a 16-bit operand's high
    # byte stays in bank 0 (direct page and stack relative modes).

    def dp_addr(self, off, index=0):
        if self.e and not (self.d & 0xFF):
            return self.d | ((off + index) & 0xFF)
        return (self.d + off + index) & 0xFFFF

    def ea(self, mode):
        if mode == 'dp':
            return self.dp_addr(self.fetch8()), True
        if mode == 'dpx':
            return self.dp_addr(self.fetch8(), self.x), True
        if mode == 'dpy':
            return self.dp_addr(self.fetch8(), self.y), True
        if mode == 'abs':
            return (self.dbr << 16) | self.fetch16(), False
        if mode == 'absx':
            return ((self.dbr << 16) | self.fetch16()) + self.x & 0xFFFFFF, False
        if mode == 'absy':
            return ((self.dbr << 16) | self.fetch16()) + self.y & 0xFFFFFF, False
        if mode == 'long':
            return self.fetch24(), False
        if mode == 'longx':
            return (self.fetch24() + self.x) & 0xFFFFFF, False
        if mode == 'ind':
            ptr = self.rd16_bank0(self.dp_addr(self.fetch8()))
            return (self.dbr << 16) | ptr, False
        if mode == 'indx':
            ptr = self.rd16_bank0(self.dp_addr(self.fetch8(), self.x))
            return (self.dbr << 16) | ptr, False
        if mode == 'indy':
            ptr = self.rd16_bank0(self.dp_addr(self.fetch8()))
            return (((self.dbr << 16) | ptr) + self.y) & 0xFFFFFF, False
        if mode == 'indl':
            pa = self.dp_addr(self.fetch8())
            ptr = self.rd16_bank0(pa) | (self.rd((pa + 2) & 0xFFFF) << 16)
            return ptr, False
        if mode == 'indly':
            pa = self.dp_addr(self.fetch8())
            ptr = self.rd16_bank0(pa) | (self.rd((pa + 2) & 0xFFFF) << 16)
            return (ptr + self.y) & 0xFFFFFF, False
        if mode == 'sr':
            return (self.s + self.fetch8()) & 0xFFFF, True
        if mode == 'sry':
            ptr = self.rd16_bank0((self.s + self.fetch8()) & 0xFFFF)
            return (((self.dbr << 16) | ptr) + self.y) & 0xFFFFFF, False
        raise SimError("bad addressing mode " + mode)

    def operand(self, mode, w16):
        if mode == 'imm':
            if w16:
                self.extra += 1
                return self.fetch16()
            return self.fetch8()
        addr, wrap = self.ea(mode)
        return self.rdw(addr, wrap, w16)

    # -- loads and stores ------------------------------------------------------

    def op_LDA(self, mode):
        w = self.m16()
        v = self.operand(mode, w)
        self.a = v if w else (self.a & 0xFF00) | v
        self.setnz(v, w)

    def op_LDX(self, mode):
        w = self.x16()
        self.x = self.operand(mode, w)
        self.setnz(self.x, w)

    def op_LDY(self, mode):
        w = self.x16()
        self.y = self.operand(mode, w)
        self.setnz(self.y, w)

    def op_STA(self, mode):
        addr, wrap = self.ea(mode)
        self.wrw(addr, wrap, self.a, self.m16())

    def op_STX(self, mode):
        addr, wrap = self.ea(mode)
        self.wrw(addr, wrap, self.x, self.x16())

    def op_STY(self, mode):
        addr, wrap = self.ea(mode)
        self.wrw(addr, wrap, self.y, self.x16())

    def op_STZ(self, mode):
        addr, wrap = self.ea(mode)
        self.wrw(addr, wrap, 0, self.m16())

    # -- arithmetic and logic ---------------------------------------------------

    def _acc(self):
        return self.a if self.m16() else self.a & 0xFF

    def _set_acc(self, v):
        if self.m16():
            self.a = v & 0xFFFF
        else:
            self.a = (self.a & 0xFF00) | (v & 0xFF)

    def op_ORA(self, mode):
        w = self.m16()
        r = self._acc() | self.operand(mode, w)
        self._set_acc(r)
        self.setnz(r, w)

    def op_AND(self, mode):
        w = self.m16()
        r = self._acc() & self.operand(mode, w)
        self._set_acc(r)
        self.setnz(r, w)

    def op_EOR(self, mode):
        w = self.m16()
        r = self._acc() ^ self.operand(mode, w)
        self._set_acc(r)
        self.setnz(r, w)

    def op_ADC(self, mode):
        w = self.m16()
        v = self.operand(mode, w)
        a = self._acc()
        bits = 16 if w else 8
        mask = (1 << bits) - 1
        sign = 1 << (bits - 1)
        c = self.p & FLAG_C
        if self.p & FLAG_D:
            r = 0
            carry = c
            for sh in range(0, bits, 4):
                dsum = ((a >> sh) & 0xF) + ((v >> sh) & 0xF) + carry
                if dsum > 9:
                    dsum += 6
                carry = 1 if dsum > 0xF else 0
                r |= (dsum & 0xF) << sh
            bin_r = a + v + c
        else:
            r = a + v + c
            carry = 1 if r > mask else 0
            bin_r = r
        self.setflag(FLAG_V, (~(a ^ v) & (a ^ bin_r) & sign) != 0)
        self.setflag(FLAG_C, carry)
        r &= mask
        self._set_acc(r)
        self.setnz(r, w)

    def op_SBC(self, mode):
        w = self.m16()
        v = self.operand(mode, w)
        a = self._acc()
        bits = 16 if w else 8
        mask = (1 << bits) - 1
        sign = 1 << (bits - 1)
        c = self.p & FLAG_C
        bin_r = a + (v ^ mask) + c
        if self.p & FLAG_D:
            r = 0
            borrow = 1 - c
            for sh in range(0, bits, 4):
                dd = ((a >> sh) & 0xF) - ((v >> sh) & 0xF) - borrow
                if dd < 0:
                    dd += 10
                    borrow = 1
                else:
                    borrow = 0
                r |= (dd & 0xF) << sh
            carry = 1 - borrow
        else:
            r = bin_r
            carry = 1 if bin_r > mask else 0
        self.setflag(FLAG_V, ((a ^ v) & (a ^ bin_r) & sign) != 0)
        self.setflag(FLAG_C, carry)
        r &= mask
        self._set_acc(r)
        self.setnz(r, w)

    def _compare(self, reg, v, w):
        mask = 0xFFFF if w else 0xFF
        reg &= mask
        self.setflag(FLAG_C, reg >= v)
        self.setnz((reg - v) & mask, w)

    def op_CMP(self, mode):
        w = self.m16()
        self._compare(self.a, self.operand(mode, w), w)

    def op_CPX(self, mode):
        w = self.x16()
        self._compare(self.x, self.operand(mode, w), w)

    def op_CPY(self, mode):
        w = self.x16()
        self._compare(self.y, self.operand(mode, w), w)

    def op_BIT(self, mode):
        w = self.m16()
        v = self.operand(mode, w)
        self.setflag(FLAG_Z, (self._acc() & v) == 0)
        if mode != 'imm':
            sign = 0x8000 if w else 0x80
            self.setflag(FLAG_N, v & sign)
            self.setflag(FLAG_V, v & (sign >> 1))

    def _rmw(self, mode, fn):
        w = self.m16()
        if mode == 'acc':
            r = fn(self._acc(), w)
            self._set_acc(r)
        else:
            addr, wrap = self.ea(mode)
            v = self.rdw(addr, wrap, w)
            r = fn(v, w)
            self.wrw(addr, wrap, r, w)

    def op_ASL(self, mode):
        def f(v, w):
            sign = 0x8000 if w else 0x80
            self.setflag(FLAG_C, v & sign)
            r = (v << 1) & (0xFFFF if w else 0xFF)
            self.setnz(r, w)
            return r
        self._rmw(mode, f)

    def op_LSR(self, mode):
        def f(v, w):
            self.setflag(FLAG_C, v & 1)
            r = v >> 1
            self.setnz(r, w)
            return r
        self._rmw(mode, f)

    def op_ROL(self, mode):
        def f(v, w):
            sign = 0x8000 if w else 0x80
            r = ((v << 1) | (self.p & FLAG_C)) & (0xFFFF if w else 0xFF)
            self.setflag(FLAG_C, v & sign)
            self.setnz(r, w)
            return r
        self._rmw(mode, f)

    def op_ROR(self, mode):
        def f(v, w):
            sign = 0x8000 if w else 0x80
            r = (v >> 1) | (sign if self.p & FLAG_C else 0)
            self.setflag(FLAG_C, v & 1)
            self.setnz(r, w)
            return r
        self._rmw(mode, f)

    def op_INC(self, mode):
        def f(v, w):
            r = (v + 1) & (0xFFFF if w else 0xFF)
            self.setnz(r, w)
            return r
        self._rmw(mode, f)

    def op_DEC(self, mode):
        def f(v, w):
            r = (v - 1) & (0xFFFF if w else 0xFF)
            self.setnz(r, w)
            return r
        self._rmw(mode, f)

    def op_TSB(self, mode):
        def f(v, w):
            a = self._acc()
            self.setflag(FLAG_Z, (a & v) == 0)
            return v | a
        self._rmw(mode, f)

    def op_TRB(self, mode):
        def f(v, w):
            a = self._acc()
            self.setflag(FLAG_Z, (a & v) == 0)
            return v & ~a
        self._rmw(mode, f)

    def _index_step(self, reg, delta):
        w = self.x16()
        v = (getattr(self, reg) + delta) & (0xFFFF if w else 0xFF)
        setattr(self, reg, v)
        self.setnz(v, w)

    def op_INX(self, mode):
        self._index_step('x', 1)

    def op_INY(self, mode):
        self._index_step('y', 1)

    def op_DEX(self, mode):
        self._index_step('x', -1)

    def op_DEY(self, mode):
        self._index_step('y', -1)

    # -- branches and jumps ------------------------------------------------------

    def _branch(self, cond):
        off = self.fetch8()
        if cond:
            if off & 0x80:
                off -= 0x100
            self.pc = (self.pc + off) & 0xFFFF
            self.extra += 1

    def op_BPL(self, mode):
        self._branch(not (self.p & FLAG_N))

    def op_BMI(self, mode):
        self._branch(self.p & FLAG_N)

    def op_BVC(self, mode):
        self._branch(not (self.p & FLAG_V))

    def op_BVS(self, mode):
        self._branch(self.p & FLAG_V)

    def op_BCC(self, mode):
        self._branch(not (self.p & FLAG_C))

    def op_BCS(self, mode):
        self._branch(self.p & FLAG_C)

    def op_BNE(self, mode):
        self._branch(not (self.p & FLAG_Z))

    def op_BEQ(self, mode):
        self._branch(self.p & FLAG_Z)

    def op_BRA(self, mode):
        self._branch(True)

    def op_BRL(self, mode):
        off = self.fetch16()
        if off & 0x8000:
            off -= 0x10000
        self.pc = (self.pc + off) & 0xFFFF

    def op_JMP(self, mode):
        addr = self.fetch16()
        if mode == 'abs':
            self.pc = addr
        elif mode == 'absind':                  # JMP (abs): pointer in bank 0
            self.pc = self.rd16_bank0(addr)
            self.extra += 2
        else:                                   # JMP (abs,X): pointer in program bank
            pa = (addr + self.x) & 0xFFFF
            b = self.pbr << 16
            self.pc = self.rd(b | pa) | (self.rd(b | ((pa + 1) & 0xFFFF)) << 8)
            self.extra += 2

    def op_JML(self, mode):
        if mode == 'long':
            t = self.fetch24()
        else:                                   # JML [abs]: pointer in bank 0
            pa = self.fetch16()
            t = self.rd16_bank0(pa) | (self.rd((pa + 2) & 0xFFFF) << 16)
            self.extra += 2
        self.pc = t & 0xFFFF
        self.pbr = t >> 16

    def op_JSR(self, mode):
        addr = self.fetch16()
        self.push16((self.pc - 1) & 0xFFFF)
        if mode == 'abs':
            self.pc = addr
        else:
            pa = (addr + self.x) & 0xFFFF
            b = self.pbr << 16
            self.pc = self.rd(b | pa) | (self.rd(b | ((pa + 1) & 0xFFFF)) << 8)
        self.extra += 2

    def op_JSL(self, mode):
        t = self.fetch24()
        self.push8(self.pbr)
        self.push16((self.pc - 1) & 0xFFFF)
        self.pbr = t >> 16
        self.pc = t & 0xFFFF

    def op_RTS(self, mode):
        self.pc = (self.pull16() + 1) & 0xFFFF

    def op_RTL(self, mode):
        self.pc = (self.pull16() + 1) & 0xFFFF
        self.pbr = self.pull8()

    def interrupt(self, native_vec, emu_vec, brk=False):
        if not self.e:
            self.push8(self.pbr)
            self.push16(self.pc)
            self.push8(self.p)
            vec = native_vec
        else:
            self.push16(self.pc)
            p = self.p | 0x20
            p = (p | 0x10) if brk else (p & ~0x10)
            self.push8(p)
            vec = emu_vec
        self.p = (self.p | FLAG_I) & ~FLAG_D
        self.pbr = 0
        self.pc = self.rd16_bank0(vec)

    def op_BRK(self, mode):
        self.fetch8()                            # signature byte
        self.interrupt(0xFFE6, 0xFFFE, brk=True)

    def op_COP(self, mode):
        self.fetch8()
        self.interrupt(0xFFE4, 0xFFF4)

    def op_RTI(self, mode):
        self.p = self.pull8()
        self.fix_flags()
        self.pc = self.pull16()
        if not self.e:
            self.pbr = self.pull8()

    # -- stack -------------------------------------------------------------------

    def op_PHA(self, mode):
        if self.m16():
            self.push16(self.a)
        else:
            self.push8(self.a & 0xFF)

    def op_PLA(self, mode):
        if self.m16():
            self.a = self.pull16()
            self.setnz(self.a, True)
        else:
            v = self.pull8()
            self.a = (self.a & 0xFF00) | v
            self.setnz(v, False)

    def op_PHX(self, mode):
        if self.x16():
            self.push16(self.x)
        else:
            self.push8(self.x)

    def op_PLX(self, mode):
        w = self.x16()
        self.x = self.pull16() if w else self.pull8()
        self.setnz(self.x, w)

    def op_PHY(self, mode):
        if self.x16():
            self.push16(self.y)
        else:
            self.push8(self.y)

    def op_PLY(self, mode):
        w = self.x16()
        self.y = self.pull16() if w else self.pull8()
        self.setnz(self.y, w)

    def op_PHB(self, mode):
        self.push8(self.dbr)

    def op_PLB(self, mode):
        self.dbr = self.pull8()
        self.setnz(self.dbr, False)

    def op_PHD(self, mode):
        self.push16(self.d)

    def op_PLD(self, mode):
        self.d = self.pull16()
        self.setnz(self.d, True)

    def op_PHK(self, mode):
        self.push8(self.pbr)

    def op_PHP(self, mode):
        self.push8(self.p)

    def op_PLP(self, mode):
        self.p = self.pull8()
        self.fix_flags()

    def op_PEA(self, mode):
        self.push16(self.fetch16())

    def op_PEI(self, mode):
        self.push16(self.rd16_bank0(self.dp_addr(self.fetch8())))

    def op_PER(self, mode):
        off = self.fetch16()
        self.push16((self.pc + off) & 0xFFFF)

    # -- flags and transfers -------------------------------------------------------

    def op_CLC(self, mode):
        self.p &= ~FLAG_C

    def op_SEC(self, mode):
        self.p |= FLAG_C

    def op_CLI(self, mode):
        self.p &= ~FLAG_I

    def op_SEI(self, mode):
        self.p |= FLAG_I

    def op_CLD(self, mode):
        self.p &= ~FLAG_D

    def op_SED(self, mode):
        self.p |= FLAG_D

    def op_CLV(self, mode):
        self.p &= ~FLAG_V

    def op_REP(self, mode):
        self.p &= ~self.fetch8()
        self.fix_flags()

    def op_SEP(self, mode):
        self.p |= self.fetch8()
        self.fix_flags()

    def op_XCE(self, mode):
        c = self.p & FLAG_C
        self.p = (self.p & ~FLAG_C) | self.e
        self.e = c
        if self.e:
            self.s = 0x0100 | (self.s & 0xFF)
        self.fix_flags()

    def op_XBA(self, mode):
        self.a = ((self.a >> 8) | (self.a << 8)) & 0xFFFF
        self.setnz(self.a & 0xFF, False)

    def _to_index(self, v):
        w = self.x16()
        v = v if w else v & 0xFF
        self.setnz(v, w)
        return v

    def _to_acc(self, v):
        w = self.m16()
        self._set_acc(v)
        self.setnz(v, w)

    def op_TAX(self, mode):
        self.x = self._to_index(self.a)

    def op_TAY(self, mode):
        self.y = self._to_index(self.a)

    def op_TXA(self, mode):
        self._to_acc(self.x)

    def op_TYA(self, mode):
        self._to_acc(self.y)

    def op_TXY(self, mode):
        self.y = self._to_index(self.x)

    def op_TYX(self, mode):
        self.x = self._to_index(self.y)

    def op_TSX(self, mode):
        self.x = self._to_index(self.s)

    def op_TXS(self, mode):
        self.s = (0x0100 | (self.x & 0xFF)) if self.e else self.x

    def op_TCS(self, mode):
        self.s = (0x0100 | (self.a & 0xFF)) if self.e else self.a

    def op_TSC(self, mode):
        self.a = self.s
        self.setnz(self.a, True)

    def op_TCD(self, mode):
        self.d = self.a
        self.setnz(self.d, True)

    def op_TDC(self, mode):
        self.a = self.d
        self.setnz(self.a, True)

    # -- block moves and the rest ----------------------------------------------------
    # MVN/MVP move one byte per execution and back the PC up until A wraps to
    # $FFFF, like the real chip, so interrupts can be taken mid-move.

    def _block(self, step):
        dst = self.fetch8()
        src = self.fetch8()
        self.dbr = dst
        self.wr((dst << 16) | self.y, self.rd((src << 16) | self.x))
        mask = 0xFFFF if self.x16() else 0xFF
        self.x = (self.x + step) & mask
        self.y = (self.y + step) & mask
        self.a = (self.a - 1) & 0xFFFF
        if self.a != 0xFFFF:
            self.pc = (self.pc - 3) & 0xFFFF

    def op_MVN(self, mode):
        self._block(1)

    def op_MVP(self, mode):
        self._block(-1)

    def op_WAI(self, mode):
        self.waiting = True

    def op_STP(self, mode):
        self.stopped = True

    def op_NOP(self, mode):
        pass

    def op_WDM(self, mode):
        self.fetch8()

    # -- execution -------------------------------------------------------------------

    def step(self):
        """Run one instruction (or take an interrupt). Returns cycles used."""
        if self.stopped:
            return 0
        if self.waiting:
            if not self.bus.irq():
                return 0
            self.waiting = False
        if self.bus.irq() and not (self.p & FLAG_I):
            self.interrupt(0xFFEE, 0xFFFE)
            return 8
        fn, mode, mn = self.table[self.fetch8()]
        self.extra = 0
        fn(mode)
        cyc = FIXED_CYCLES.get(mn)
        if cyc is None:
            cyc = MODE_CYCLES.get(mode, 3)
            if mn in RMW and mode != 'acc':
                cyc += 2 + self.extra    # write-back of a 16-bit operand
        return cyc + self.extra


# ---------------------------------------------------------------------------
# The whole machine

class Machine:
    def __init__(self, rom, labels=None):
        self.bus = Bus(rom)
        self.cpu = CPU(self.bus)
        self.labels = labels or {}

    @property
    def cycles(self):
        return self.bus.cycles

    @property
    def acia(self):
        return self.bus.acia

    @property
    def via(self):
        return self.bus.via

    def run(self, cycles, until=None, check_every=2000):
        """Run for up to `cycles` cycles, or until until() returns true
        (checked every `check_every` cycles). Returns True if until() fired."""
        cpu = self.cpu
        bus = self.bus
        end = bus.cycles + cycles
        next_check = bus.cycles + check_every
        while bus.cycles < end:
            if cpu.stopped:
                raise SimError("CPU stopped (STP) at $%02X:%04X" % (cpu.pbr, cpu.pc))
            n = cpu.step()
            if n == 0:                               # waiting: skip ahead
                n = bus.cycles_to_event()
                if n is None:
                    raise SimError("WAI with interrupts that can never arrive")
                n = min(n, end - bus.cycles)
                n = max(n, 1)
            bus.tick(n)
            if bus.errors:
                raise SimError("; ".join(bus.errors[:5]) + " (PC $%02X:%04X)"
                               % (cpu.pbr, cpu.pc))
            if until is not None and bus.cycles >= next_check:
                next_check = bus.cycles + check_every
                if until():
                    return True
        return False

    def run_until_output(self, text, cycles):
        """Run until the ACIA has sent `text` (bytes), or the cycle budget ends."""
        out = self.acia.output
        return self.run(cycles, until=lambda: text in out)

    # Memory access for tests
    def peek(self, addr, n=1):
        v = 0
        for i in range(n):
            v |= self.bus.read((addr + i) & 0xFFFFFF) << (8 * i)
        return v

    def sym(self, name):
        return self.labels[name]


def load_labels(path):
    """Read an ld65 -Ln (VICE) label file: lines like `al 00FF00 .name`."""
    labels = {}
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 3 and parts[0] == 'al':
                labels[parts[2].lstrip('.')] = int(parts[1], 16)
    return labels


def load_machine(rom_path):
    with open(rom_path, 'rb') as f:
        rom = f.read()
    lbl = os.path.splitext(rom_path)[0] + '.lbl'
    labels = load_labels(lbl) if os.path.exists(lbl) else {}
    return Machine(rom, labels)


# ---------------------------------------------------------------------------
# Interactive use

def interactive(m, show_leds):
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    is_tty = os.isatty(fd)
    old = termios.tcgetattr(fd) if is_tty else None
    if is_tty:
        tty.setcbreak(fd)
    sent = 0
    leds_seen = 0
    try:
        while True:
            m.run(20000)
            out = m.acia.output
            if len(out) > sent:
                sys.stdout.write(out[sent:].decode('latin-1').replace('\r', ''))
                sys.stdout.flush()
                sent = len(out)
            if show_leds:
                log = m.via.portb_log
                for _, v in log[leds_seen:]:
                    sys.stderr.write('[LEDs %s]\n' % format(v, '08b')
                                     .replace('0', '.').replace('1', '*'))
                leds_seen = len(log)
            r, _, _ = select.select([fd], [], [], 0)
            if r:
                data = os.read(fd, 64)
                if not data:
                    break
                m.acia.send(data.replace(b'\n', b'\r'))
    except KeyboardInterrupt:
        pass
    finally:
        if is_tty:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print('\n[%.3f simulated seconds]' % (m.cycles / CPU_HZ))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('rom', help='32 KB ROM image for $8000-$FFFF')
    ap.add_argument('--leds', action='store_true', help='print port B changes')
    ap.add_argument('--seconds', type=float,
                    help='run this many simulated seconds, print output, exit')
    args = ap.parse_args()
    m = load_machine(args.rom)
    if args.seconds is not None:
        m.run(int(args.seconds * CPU_HZ))
        sys.stdout.write(m.acia.output.decode('latin-1').replace('\r', ''))
        if args.leds:
            for t, v in m.via.portb_log:
                print('%8.3f s  LEDs %s' % (t / CPU_HZ, format(v, '08b')))
        return
    interactive(m, args.leds)


if __name__ == '__main__':
    main()
