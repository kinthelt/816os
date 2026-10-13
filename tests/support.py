"""Shared helpers for the simulator tests."""

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import sim816  # noqa: E402

BUILD = os.path.join(ROOT, 'build')


def rom_machine(name):
    """Load build/<name>.bin and its labels. Run `make test` to build them."""
    path = os.path.join(BUILD, name + '.bin')
    if not os.path.exists(path):
        raise FileNotFoundError(path + ' is missing; run `make test`')
    return sim816.load_machine(path)


ROM_CFG = """
MEMORY { ROM: start = $8000, size = $8000, fill = yes, fillval = $FF, file = %O; }
SEGMENTS {
    CODE:    load = ROM, type = ro;
    VECTORS: load = ROM, type = ro, start = $FFE0;
}
"""


def assemble(body, vectors=None):
    """Assemble a snippet into a ROM that starts at `start` on reset.

    `body` is ca65 source for the CODE segment and must define `start`.
    `vectors` maps a vector address ($FFE4 etc.) to a label in `body`.
    Returns a Machine.
    """
    vectors = dict(vectors or {})
    vectors[0xFFFC] = 'start'
    words = []
    for addr in range(0xFFE0, 0x10000, 2):
        words.append('        .word %s' % vectors.get(addr, '0'))
    src = '.p816\n.segment "CODE"\n' + body + '\n.segment "VECTORS"\n' + '\n'.join(words) + '\n'
    with tempfile.TemporaryDirectory() as tmp:
        s = os.path.join(tmp, 't.s')
        o = os.path.join(tmp, 't.o')
        cfg = os.path.join(tmp, 't.cfg')
        rom = os.path.join(tmp, 't.bin')
        lbl = os.path.join(tmp, 't.lbl')
        with open(s, 'w') as f:
            f.write(src)
        with open(cfg, 'w') as f:
            f.write(ROM_CFG)
        subprocess.run(['ca65', '--cpu', '65816', '-g', '-o', o, s], check=True)
        subprocess.run(['ld65', '-C', cfg, '-Ln', lbl, '-o', rom, o], check=True)
        with open(rom, 'rb') as f:
            data = f.read()
        labels = sim816.load_labels(lbl)
    return sim816.Machine(data, labels)


def run_to_stp(m, cycles=100000):
    """Run until the program executes STP."""
    try:
        m.run(cycles)
    except sim816.SimError as e:
        if 'STP' in str(e):
            return
        raise
    raise AssertionError('program did not reach STP')


CTRL_Z = b'\x1a'
ESC = b'\x1b'
FREE, READY, PAUSED = 0, 1, 2


class Shell:
    """A simulated machine with a person typing at the shell."""

    def __init__(self, test, rom='test'):
        self.t = test
        self.m = rom_machine(rom)
        self.mark = 0
        self.expect(b'816os\r\n> ')

    # -- terminal ---------------------------------------------------------------

    def out(self):
        """Everything printed since the last command was typed."""
        return bytes(self.m.acia.output[self.mark:])

    def expect(self, text, seconds=1.0):
        ok = self.m.run(int(seconds * sim816.CPU_HZ), until=lambda: text in self.m.acia.output[self.mark:])
        self.t.assertTrue(ok, 'never saw %r; got %r' % (text, self.out()))

    def type(self, keys, expect=None, seconds=1.0):
        self.mark = len(self.m.acia.output)
        self.m.acia.send(keys)
        if expect is not None:
            self.expect(expect, seconds)

    def command(self, line, seconds=1.0):
        """Type a line and wait for the next prompt. Returns the output."""
        self.type(line + b'\r', line + b'\r\n')
        self.expect(b'\r\n> ', seconds)
        return self.out()

    def run(self, name):
        """`run name`; returns the bank it was given. It is in the foreground."""
        self.type(b'run ' + name + b'\r', b'] ' + name + b'\r\n')
        return int(re.search(rb'\[(\d)\] ' + name, self.out()).group(1))

    def pause(self, bank):
        self.type(CTRL_Z, b'[%d] ' % bank)
        self.expect(b' paused\r\n> ')

    def background(self, name):
        """Start a program and put it in the background."""
        bank = self.run(name)
        self.pause(bank)
        self.command(b'bg %d' % bank)
        return bank

    # -- kernel state ---------------------------------------------------------------

    def word(self, sym, index=0):
        return self.m.peek(self.m.sym(sym) + 2 * index, 2)

    def state(self, bank):
        return self.word('proc_state', bank)

    def dp(self, bank, offset=0):
        return self.m.peek(0x0200 + bank * 0x100 + offset, 2)

    def assertClean(self):
        self.t.assertEqual(self.m.acia.tx_too_soon, 0, 'a character was sent too soon')
        self.t.assertEqual(self.m.acia.rx_overruns, 0, 'the ACIA dropped a received byte')
        self.t.assertEqual(self.word('brk_count'), 0)
