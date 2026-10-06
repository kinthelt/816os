"""Kernel tests: boot the test and demo ROMs in the simulator.

Test ROM slots (tests/apps/test_table.s):
  0-2  torture   register-integrity checker, one image in three banks
  3    echoq     echo, exits on 'q'
  4    quitter   prints "bye", returns with RTL
  5    crasher   takes the console lock, prints, hits BRK
  6    empty
"""

import os
import unittest

from support import BUILD, rom_machine, sim816

HZ = sim816.CPU_HZ
IDLE = 7
BOOT_CYCLES = 100_000       # boot is done well within this


def image(name):
    with open(os.path.join(BUILD, 'apps', name + '.bin'), 'rb') as f:
        return f.read()


class KernelTest(unittest.TestCase):

    def boot(self, name='test'):
        m = rom_machine(name)
        m.run(BOOT_CYCLES)
        return m

    def word(self, m, sym, index=0):
        return m.peek(m.sym(sym) + 2 * index, 2)

    def states(self, m):
        return [self.word(m, 'proc_state', i) for i in range(8)]

    def run_until_done(self, m, budget=4 * HZ):
        want = [b'done 0\r\n', b'done 1\r\n', b'done 2\r\n']
        ok = m.run(budget, until=lambda: all(w in m.acia.output for w in want))
        self.assertTrue(ok, 'torture processes did not finish: %r' % bytes(m.acia.output))

    def assertClean(self, m):
        self.assertEqual(m.acia.tx_too_soon, 0, 'a character was sent too soon')
        self.assertEqual(m.acia.rx_overruns, 0, 'the ACIA dropped a received byte')

    # -- boot and loading ---------------------------------------------------------

    def test_boot_loads_each_image_into_its_bank(self):
        m = self.boot()
        torture = image('torture')
        code = len(torture) - 2           # last word is `marker`, written at run time
        for slot in range(3):
            got = bytes(m.bus.ram[(slot + 1) << 16:((slot + 1) << 16) + code])
            self.assertEqual(got, torture[:code], 'slot %d' % slot)
        for slot, name in [(3, 'echoq'), (4, 'quitter'), (5, 'crasher')]:
            img = image(name)
            base = (slot + 1) << 16
            self.assertEqual(bytes(m.bus.ram[base:base + len(img)]), img, name)

    def test_one_image_runs_with_separate_state_per_bank(self):
        m = self.boot()
        marker = len(image('torture')) - 2
        for slot in range(3):
            # torture keeps its slot number in its direct page (offset 2)
            self.assertEqual(m.peek(0x0200 + slot * 0x100 + 2, 2), slot)
            # and in `marker`, written with an absolute address in its own bank
            self.assertEqual(m.peek(((slot + 1) << 16) + marker, 2), slot)

    def test_empty_slot_is_never_run(self):
        m = self.boot()
        self.assertEqual(self.states(m)[6], 0)
        seen = set()
        for _ in range(200):
            m.run(1000)
            seen.add(self.word(m, 'current_proc') // 2)
        self.assertNotIn(6, seen)
        self.assertTrue({0, 1, 2} <= seen, seen)

    # -- scheduling -----------------------------------------------------------------

    def test_preemption_preserves_registers(self):
        m = self.boot()
        # Keep the ACIA interrupting the whole time as well.
        m.acia.send(b'abcdefghijklmnop' * 40)
        self.run_until_done(m)
        for slot in range(3):
            self.assertEqual(m.peek(0x0200 + slot * 0x100, 2), 40)
        # The crasher's BRK is expected; a torture failure would add another.
        self.assertEqual(self.word(m, 'brk_count'), 1)
        self.assertEqual(self.word(m, 'last_brk_slot'), 5 * 2)
        # Many slices went by, so there were many preemptions mid-check.
        self.assertGreater(m.peek(m.sym('ticks'), 4), 50)
        self.assertClean(m)

    def test_timer_tick_rate(self):
        m = rom_machine('test')
        m.run(HZ // 2)
        ticks = m.peek(m.sym('ticks'), 4)
        # 200 Hz less the time before the timer starts; the period is SLICE_COUNT + 2.
        self.assertIn(ticks, range(97, 101))

    # -- exit and crash ---------------------------------------------------------------

    def test_rtl_from_entry_point_exits(self):
        m = self.boot()
        self.assertTrue(m.run_until_output(b'bye\r\n', HZ))
        m.run(20_000)
        self.assertEqual(self.states(m)[4], 0)

    def test_brk_kills_process_and_releases_console_lock(self):
        m = self.boot()
        self.run_until_done(m)
        out = bytes(m.acia.output)
        self.assertIn(b'crashing\r\n', out)
        self.assertEqual(self.states(m)[5], 0)
        self.assertEqual(self.word(m, 'brk_count'), 1)
        pc = m.peek(m.sym('last_brk_pc'), 3)
        self.assertEqual(pc >> 16, 6)                     # slot 5 runs in bank 6
        crash = image('crasher').index(bytes([0x00, 0x42]))
        self.assertEqual(pc & 0xFFFF, crash + 2)          # BRK pushes its address + 2
        # Others still got the console after it died holding the lock.
        self.assertGreater(out.index(b'done 0'), out.index(b'crashing'))
        self.assertEqual(self.word(m, 'con_owner'), 0)

    def test_idle_when_every_process_has_ended(self):
        m = self.boot()
        self.run_until_done(m)
        m.acia.send(b'q')
        m.run(HZ // 10)
        self.assertEqual(self.states(m), [0] * 8)
        self.assertEqual(self.word(m, 'current_proc'), IDLE * 2)
        self.assertTrue(m.cpu.waiting)                    # parked in WAI
        before = m.peek(m.sym('ticks'), 4)
        m.run(HZ // 10)
        self.assertGreaterEqual(m.peek(m.sym('ticks'), 4) - before, 19)
        self.assertClean(m)

    # -- console ------------------------------------------------------------------------

    def test_echo(self):
        m = self.boot()
        m.acia.send(b'hello\r')
        self.assertTrue(m.run_until_output(b'hello\r\n', HZ // 2))
        self.assertClean(m)

    def test_receive_burst_while_busy(self):
        # Back-to-back characters at 115200 while three processes compete
        # for the CPU: nothing lost at the ACIA or in the ring.
        m = self.boot()
        payload = bytes(b'abcdefghijklmnoprstuvwxyz'[i % 25] for i in range(200))
        m.acia.send(payload)
        m.run(HZ // 2, until=lambda: len(m.acia.output) > 400)
        out = bytes(m.acia.output)
        for line in (b'bye\r\n', b'crashing\r\n', b'done 0\r\n', b'done 1\r\n', b'done 2\r\n'):
            out = out.replace(line, b'')
        self.assertEqual(out, payload)
        self.assertClean(m)

    def test_lines_are_not_interleaved(self):
        m = self.boot()
        m.acia.send(b'xyz' * 30)
        self.run_until_done(m)
        out = bytes(m.acia.output)
        for line in (b'bye\r\n', b'crashing\r\n', b'done 0\r\n', b'done 1\r\n', b'done 2\r\n'):
            self.assertEqual(out.count(line), 1, line)

    # -- demo ROM -------------------------------------------------------------------------

    def test_demo_rom(self):
        m = rom_machine('816os')
        m.run(HZ * 11 // 10)
        out = bytes(m.acia.output).split(b'\r\n')
        self.assertEqual(out[:5], [b'ticker 0: 0000', b'ticker 1: 0000', b'ticker 0: 0001',
                                   b'ticker 0: 0002', b'ticker 1: 0001'])
        leds = [v for _, v in m.via.portb_log]
        self.assertEqual(leds[:11], [1, 2, 4, 8, 16, 32, 64, 128, 64, 32, 16])
        m.acia.send(b'hi\r')
        self.assertTrue(m.run_until_output(b'hi\r\n', HZ // 2))
        self.assertClean(m)


if __name__ == '__main__':
    unittest.main()
