"""Shared helpers for the simulator tests."""

import os
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
