"""Receive flow control: RTS goes high when the receive ring is nearly full
and low again once it drains, and a sender that honours it loses nothing.

The simulated sender stops after `acia.skid` more characters once RTS goes
high, like a USB serial adapter.
"""

import unittest

from support import Shell, sim816

HZ = sim816.CPU_HZ
CMD_RUN, CMD_HOLD = 0x89, 0x81
PASTE = bytes(b'abcdefghijklmnoprstuvwxyz0123456789'[i % 35] for i in range(1500))


def busy_echo(test, skid=3, honor_rts=True):
    """Three busy background programs and echoq in the foreground, then a
    long paste. Returns the shell and what was echoed."""
    sh = Shell(test)
    sh.m.acia.skid = skid
    sh.m.acia.honor_rts = honor_rts
    for _ in range(3):
        sh.background(b'torture')
    sh.run(b'echoq')
    sh.type(PASTE)
    sh.m.run(HZ * 2, until=lambda: not sh.m.acia.pending and
             len(sh.out().replace(b'done', b'')) >= len(PASTE))
    sh.m.run(HZ // 5)
    echoed = sh.out()
    for bank in (1, 2, 3):
        echoed = echoed.replace(b'done %d\r\n' % bank, b'')
    return sh, echoed


class FlowControlTest(unittest.TestCase):

    def test_long_paste_while_busy_loses_nothing(self):
        sh, echoed = busy_echo(self)
        self.assertEqual(echoed, PASTE)
        self.assertGreater(sh.m.acia.rts_raises, 0)          # it was needed
        self.assertGreater(sh.m.acia.tx_while_held, 0)       # output kept going
        self.assertEqual(sh.m.acia.cmd, CMD_RUN)             # and RTS is low again
        self.assertEqual(sh.m.peek(sh.m.sym('rx_held')), 0)
        sh.assertClean()

    def test_without_flow_control_the_same_paste_loses_data(self):
        # The control for the test above: a sender that ignores RTS.
        sh, echoed = busy_echo(self, honor_rts=False)
        self.assertLess(len(echoed), len(PASTE))

    def test_a_sender_that_overshoots(self):
        sh, echoed = busy_echo(self, skid=16)
        self.assertEqual(echoed, PASTE)
        sh.assertClean()

    def test_held_while_nothing_reads_then_released(self):
        sh = Shell(self)
        bank = sh.run(b'sleeper')                # foreground for a second, reads nothing
        sh.type(b'x' * 400)
        sh.m.run(HZ // 5)
        self.assertEqual(sh.m.acia.cmd, CMD_HOLD)
        waiting = (sh.m.peek(0) - sh.m.peek(1)) & 0xFF        # rx_head - rx_tail
        self.assertGreaterEqual(waiting, 192)
        self.assertLessEqual(waiting, 192 + sh.m.acia.skid)
        self.assertGreater(len(sh.m.acia.pending), 0)        # the rest waited at the sender
        sh.m.run(HZ, until=lambda: sh.state(bank) == 0)     # it ends; the shell has the keyboard
        # The shell reads the typed-ahead x's (as one over-long line), so
        # RTS drops and the rest arrive.
        sh.m.run(HZ // 2, until=lambda: not sh.m.acia.pending)
        self.assertEqual(len(sh.m.acia.pending), 0)
        sh.m.run(HZ // 20)
        self.assertEqual(sh.m.acia.cmd, CMD_RUN)
        sh.assertClean()


if __name__ == '__main__':
    unittest.main()
