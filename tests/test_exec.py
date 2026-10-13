"""Running programs that are already in memory: `run <bank>` and K_EXEC."""

import os
import unittest

from support import BUILD, FREE, READY, Shell, sim816
from test_xmodem import GOOD, Peer

HZ = sim816.CPU_HZ


def image(name):
    with open(os.path.join(BUILD, 'apps', name + '.bin'), 'rb') as f:
        return f.read()


def load(sh, bank, data):
    """`load <bank>0000` and send `data` from the terminal."""
    sh.type(b'load %x0000\r' % bank)
    Peer(sh).upload(data)
    sh.expect(GOOD + b'> ', seconds=3)


class RunBankTest(unittest.TestCase):

    def test_load_and_run(self):
        sh = Shell(self)
        load(sh, 3, image('quitter'))
        sh.type(b'run 3\r', b'[3] loaded\r\nbye\r\n> ')
        self.assertEqual(sh.state(3), FREE)
        sh.assertClean()

    def test_one_loaded_image_in_two_banks(self):
        sh = Shell(self)
        torture = image('torture')
        load(sh, 2, torture)
        load(sh, 5, torture)
        sh.type(b'run 2\r', b'[2] loaded\r\n')
        sh.pause(2)
        sh.command(b'bg 2')
        sh.type(b'run 5\r', b'[5] loaded\r\n')
        sh.expect(b'done 5\r\n', seconds=4)
        sh.expect(b'done 2\r\n', seconds=4)
        self.assertEqual(sh.dp(2), 40)
        self.assertEqual(sh.dp(5), 40)
        sh.assertClean()                    # no BRK from a failed check

    def test_empty_bank_dies_on_brk(self):
        sh = Shell(self)
        out = sh.command(b'run 3')
        self.assertIn(b'[3] loaded\r\n\r\n[3] loaded hit BRK at 030000\r\n', out)
        self.assertEqual(sh.state(3), FREE)

    def test_bank_in_use(self):
        sh = Shell(self)
        bank = sh.background(b'hog')
        self.assertIn(b'bank in use', sh.command(b'run %d' % bank))
        self.assertEqual(sh.state(bank), READY)
        self.assertIn(b'bank in use', sh.command(b'run 0'))

    def test_names_still_work(self):
        sh = Shell(self)
        self.assertIn(b'no such program', sh.command(b'run 9'))
        sh.type(b'run quitter\r', b'[1] quitter\r\nbye\r\n> ')


class ExecServiceTest(unittest.TestCase):

    def test_a_program_can_start_another(self):
        sh = Shell(self)
        quitter = image('quitter')
        sh.m.bus.ram[0x070000:0x070000 + len(quitter)] = quitter
        sh.type(b'run spawner\r', b'spawner ok\r\n')
        sh.expect(b'bye\r\n')               # the one it started, in the background
        sh.m.run(HZ // 20)
        self.assertEqual(sh.state(7), FREE)
        sh.assertClean()


if __name__ == '__main__':
    unittest.main()
