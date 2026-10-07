"""Kernel and shell tests: boot the test and demo ROMs in the simulator and
drive them through the shell, as a person at the terminal would.

Test ROM programs (tests/apps/test_table.s):
  torture   register-integrity checker; prints "done <bank>" and exits
  echoq     echoes input, exits on 'q'
  quitter   prints "bye", returns with RTL
  crasher   takes the console lock, prints, hits BRK
  hog       takes the console lock and never releases it
"""

import os
import re
import unittest

from support import BUILD, rom_machine, sim816

HZ = sim816.CPU_HZ
CTRL_Z = b'\x1a'
ESC = b'\x1b'
FREE, READY, PAUSED = 0, 1, 2


def image(name):
    with open(os.path.join(BUILD, 'apps', name + '.bin'), 'rb') as f:
        return f.read()


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
        ok = self.m.run(int(seconds * HZ), until=lambda: text in self.m.acia.output[self.mark:])
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


class BootTest(unittest.TestCase):

    def test_boot_starts_only_the_shell(self):
        sh = Shell(self)
        self.assertEqual([sh.state(b) for b in range(8)], [READY] + [FREE] * 7)
        for bank in range(1, 8):
            self.assertEqual(bytes(sh.m.bus.ram[bank << 16:(bank << 16) + 256]), bytes(256))
        sh.assertClean()

    def test_timer_tick_rate(self):
        m = rom_machine('test')
        m.run(HZ // 2)
        # 200 Hz less the time before the timer starts; the period is SLICE_COUNT + 2.
        self.assertIn(m.peek(m.sym('ticks'), 4), range(97, 101))


class RunTest(unittest.TestCase):

    def test_run_loads_into_first_free_bank(self):
        sh = Shell(self)
        bank = sh.run(b'echoq')
        self.assertEqual(bank, 1)
        img = image('echoq')
        self.assertEqual(bytes(sh.m.bus.ram[0x10000:0x10000 + len(img)]), img)
        self.assertEqual(sh.state(1), READY)
        self.assertEqual(sh.word('fg_proc'), 2)

    def test_one_image_runs_with_separate_state_per_bank(self):
        sh = Shell(self)
        banks = [sh.background(b'torture') for _ in range(3)]
        self.assertEqual(banks, [1, 2, 3])
        sh.m.run(HZ // 20)                      # let the last one get going
        marker = len(image('torture')) - 2
        for bank in banks:
            self.assertEqual(sh.dp(bank, 2), bank)                          # pid in its direct page
            self.assertEqual(sh.m.peek((bank << 16) + marker, 2), bank)     # and in its own bank

    def test_unknown_program(self):
        sh = Shell(self)
        self.assertIn(b'no such program', sh.command(b'run nothing'))

    def test_no_free_bank(self):
        sh = Shell(self)
        for _ in range(7):
            sh.run(b'hog')
            sh.type(CTRL_Z, b'paused\r\n> ')
        self.assertIn(b'no free bank', sh.command(b'run hog'))

    def test_exit_gives_keyboard_back(self):
        sh = Shell(self)
        bank = sh.run(b'quitter')
        sh.expect(b'bye\r\n> ')
        self.assertEqual(sh.state(bank), FREE)
        self.assertEqual(sh.word('fg_proc'), 0)
        self.assertIn(b'0    ready  shell', sh.command(b'ps'))
        sh.assertClean()

    def test_brk_is_reported_and_releases_the_console(self):
        sh = Shell(self)
        bank = sh.run(b'crasher')
        sh.expect(b'> ')
        crash = image('crasher').index(bytes([0x00, 0x42]))
        self.assertIn(b'crashing\r\n\r\n[1] crasher hit BRK at %06X\r\n' % ((bank << 16) + crash),
                      sh.out())
        self.assertEqual(sh.state(bank), FREE)
        self.assertEqual(sh.word('con_owner'), 0)
        self.assertIn(b'bank state', sh.command(b'ps'))     # the shell can print


class SchedulingTest(unittest.TestCase):

    def test_preemption_preserves_registers(self):
        sh = Shell(self)
        for _ in range(3):
            sh.background(b'torture')
        sh.run(b'echoq')
        sh.type(b'abcdefghijklmnop' * 30)       # keep ACIA interrupts coming too
        for bank in (1, 2, 3):
            sh.expect(b'done %d\r\n' % bank, seconds=4)
        for bank in (1, 2, 3):
            self.assertEqual(sh.dp(bank), 40)                  # iterations
        self.assertGreater(sh.m.peek(sh.m.sym('ticks'), 4), 50)
        sh.assertClean()                                       # no BRK from a failed check

    def test_paused_process_does_not_run(self):
        sh = Shell(self)
        bank = sh.run(b'torture')
        sh.m.run(HZ // 50)
        sh.pause(bank)
        self.assertEqual(sh.state(bank), PAUSED)
        before = sh.dp(bank)
        sh.m.run(HZ // 5)
        self.assertEqual(sh.dp(bank), before)
        sh.command(b'bg %d' % bank)
        sh.expect(b'done %d\r\n' % bank, seconds=3)
        sh.assertClean()


class ForegroundTest(unittest.TestCase):

    def test_ctrl_z_and_fg(self):
        sh = Shell(self)
        bank = sh.run(b'echoq')
        sh.type(b'hi\r', b'hi\r\n')
        sh.pause(bank)
        self.assertIn(b'%d    paused echoq' % bank, sh.command(b'ps'))
        sh.type(b'fg %d\r' % bank, b'fg %d\r\n' % bank)
        sh.m.run(HZ // 50)
        sh.type(b'yo\r', b'yo\r\n')
        sh.type(b'q', b'> ')
        self.assertEqual(sh.state(bank), FREE)
        sh.assertClean()

    def test_only_the_foreground_gets_input(self):
        sh = Shell(self)
        sh.background(b'echoq')
        out = sh.command(b'ps')                 # echoq is running but must not see it
        self.assertEqual(out.count(b'ps\r\n'), 1)
        self.assertIn(b'1    ready  echoq', out)

    def test_ctrl_z_releases_the_console_lock(self):
        sh = Shell(self)
        bank = sh.run(b'hog')
        sh.expect(b'hogging\r\n')
        self.assertEqual(sh.word('con_owner'), bank * 2 + 2)
        sh.pause(bank)                          # the shell printed, so it got the lock
        self.assertEqual(sh.word('con_owner'), 0)

    def test_ctrl_z_at_the_shell_is_ignored(self):
        sh = Shell(self)
        sh.type(CTRL_Z)
        sh.m.run(HZ // 50)
        self.assertIn(b'bank state', sh.command(b'ps'))
        self.assertEqual(sh.state(0), READY)

    def test_receive_burst_while_busy(self):
        # Back-to-back characters at 115200 while three processes compete for
        # the CPU: nothing lost at the ACIA or in the ring.
        sh = Shell(self)
        for _ in range(3):
            sh.background(b'torture')
        sh.run(b'echoq')
        payload = bytes(b'abcdefghijklmnoprstuvwxyz'[i % 25] for i in range(200))
        sh.type(payload)
        sh.m.run(HZ, until=lambda: len(re.sub(rb'done \d\r\n', b'', sh.out())) >= len(payload))
        self.assertEqual(re.sub(rb'done \d\r\n', b'', sh.out()), payload)
        sh.assertClean()


class KillTest(unittest.TestCase):

    def test_kill(self):
        sh = Shell(self)
        bank = sh.background(b'torture')
        self.assertIn(b'[%d] killed' % bank, sh.command(b'kill %d' % bank))
        self.assertEqual(sh.state(bank), FREE)
        self.assertIn(b'no such process', sh.command(b'kill %d' % bank))
        self.assertIn(b'usage', sh.command(b'kill 9'))
        self.assertIn(b'usage', sh.command(b'fg 0'))

    def test_kill_0_restarts_the_shell(self):
        sh = Shell(self)
        bank = sh.background(b'torture')
        sh.type(b'kill 0\r', b'816os\r\n> ')
        self.assertEqual(sh.state(0), READY)
        self.assertEqual(sh.state(bank), READY)       # others carry on
        self.assertIn(b'%d    ready  torture' % bank, sh.command(b'ps'))


class MonitorTest(unittest.TestCase):

    def test_store_and_examine(self):
        sh = Shell(self)
        sh.command(b'21000: a9 00 FF')
        self.assertEqual(sh.m.peek(0x021000, 3), 0xFF00A9)
        self.assertIn(b'\r\n021000: A9 00 FF\r\n> ', sh.command(b'21000.21002'))

    def test_examine_across_a_bank_boundary(self):
        sh = Shell(self)
        sh.command(b'1fffe: 11 22 33')
        self.assertEqual(sh.m.peek(0x020000), 0x33)
        out = sh.command(b'1fffe.20000')
        self.assertIn(b'01FFFE: 11 22\r\n020000: 33\r\n', out)

    def test_examine_rom(self):
        sh = Shell(self)
        out = sh.command(b'ff00 ff01')
        self.assertIn(b'00FF00: 4C\r\n00FF01: %02X\r\n' % sh.m.peek(0xFF01), out)

    def test_esc_stops_a_listing_and_keeps_typeahead(self):
        sh = Shell(self)
        sh.type(b'0.ffff\r')
        sh.m.run(HZ // 20)
        sh.type(ESC + b'ps\r', b'bank state', seconds=1)
        self.assertNotIn(b'00FFF8:', sh.out())

    def test_typeahead_during_a_listing_is_kept(self):
        sh = Shell(self)
        sh.type(b'8000.80ff\rps\r', b'bank state', seconds=1)

    def test_bad_line_does_nothing(self):
        sh = Shell(self)
        self.assertEqual(sh.command(b'bogus'), b'bogus\r\n?\r\n> ')
        self.assertEqual(sh.command(b'kil 3'), b'kil 3\r\n?\r\n> ')

    def test_line_editing(self):
        sh = Shell(self)
        self.assertIn(b'bank state', sh.command(b'PS'))           # case-insensitive
        sh.type(b'pz\x08s\r', b'bank state')                      # backspace
        sh.type(b'ps' + ESC, b'\\\r\n> ')                         # ESC cancels
        sh.m.run(HZ // 20)
        self.assertNotIn(b'bank state', sh.out())

    def test_help_lists_programs(self):
        sh = Shell(self)
        self.assertIn(b'programs: torture echoq quitter crasher hog\r\n', sh.command(b'help'))


class DemoTest(unittest.TestCase):

    def test_demo_rom(self):
        sh = Shell(self, rom='816os')
        self.assertIn(b'programs: ticker leds echo', sh.command(b'help'))
        sh.background(b'leds')
        sh.run(b'ticker')
        sh.expect(b'ticker 2: 0000\r\n')
        sh.expect(b'ticker 2: 0001\r\n', seconds=3)
        sh.pause(2)
        leds = [v for _, v in sh.m.via.portb_log]
        self.assertEqual(leds[:9], [1, 2, 4, 8, 16, 32, 64, 128, 64])
        bank = sh.run(b'echo')
        sh.type(b'hi\r', b'hi\r\n')
        sh.pause(bank)
        sh.assertClean()


if __name__ == '__main__':
    unittest.main()
