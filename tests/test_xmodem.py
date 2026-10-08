"""The shell's save and load commands, against an XMODEM/CRC peer written
here in Python, standing in for the terminal program."""

import binascii
import unittest

from support import CTRL_Z, ESC, Shell, sim816

HZ = sim816.CPU_HZ
SOH, EOT, ACK, NAK, CAN = 0x01, 0x04, 0x06, 0x15, 0x18
BEGIN = b'Begin XMODEM/CRC transfer.  Press <Esc> to abort...\r\n'
GOOD = b'\r\nTransfer Successful!\r\n'
ERROR = b'\r\nTransfer Error!\r\n'


def crc16(data):
    return binascii.crc_hqx(data, 0)        # CRC-16/XMODEM


def block(number, data):
    data = data.ljust(128, b'\0')
    return (bytes([SOH, number & 0xFF, 0xFF - (number & 0xFF)]) + data
            + crc16(data).to_bytes(2, 'big'))


class Peer:
    """Reads what the machine sends, starting where the transfer begins."""

    def __init__(self, sh):
        self.sh = sh
        self.m = sh.m
        sh.expect(BEGIN, seconds=2)
        out = self.m.acia.output
        self.pos = self.start = out.index(BEGIN, sh.mark) + len(BEGIN)

    def read(self, n=1, seconds=5.0):
        out = self.m.acia.output
        ok = self.m.run(int(seconds * HZ), check_every=500,
                        until=lambda: len(out) >= self.pos + n)
        if not ok:
            raise AssertionError('machine sent nothing more; got %r' % bytes(out[self.pos:]))
        data = bytes(out[self.pos:self.pos + n])
        self.pos += n
        return data

    def read_until(self, wanted, seconds=5.0):
        """Skip bytes until one in `wanted` arrives; return it."""
        while True:
            b = self.read(1, seconds)[0]
            if b in wanted:
                return b

    def send(self, data):
        self.m.acia.send(data)

    # -- the terminal sends a file: `load` --------------------------------------

    def upload(self, data, damage=()):
        """Send `data`. Blocks numbered in `damage` go wrong once first."""
        self.read_until({ord('C')}, seconds=3)
        blocks = [data[i:i + 128] for i in range(0, len(data), 128)]
        damaged = set(damage)
        for n, chunk in enumerate(blocks, 1):
            while True:
                pkt = block(n, chunk)
                if n in damaged:
                    damaged.discard(n)
                    pkt = pkt[:10] + bytes([pkt[10] ^ 0xFF]) + pkt[11:]
                self.send(pkt)
                reply = self.read_until({ACK, NAK, CAN})
                if reply == ACK:
                    break
                self.sh.t.assertEqual(reply, NAK)
        self.send(bytes([EOT]))
        self.sh.t.assertEqual(self.read_until({ACK, NAK}), ACK)

    # -- the terminal receives a file: `save` -------------------------------------

    def download(self):
        """Receive a file; returns it, padding included."""
        self.send(b'C')
        data = b''
        expected = 1
        while True:
            first = self.read(1)[0]
            if first == EOT:
                self.send(bytes([ACK]))
                return data
            self.sh.t.assertEqual(first, SOH)
            rest = self.read(132)
            self.sh.t.assertEqual(rest[0], expected & 0xFF)
            self.sh.t.assertEqual(rest[1], 0xFF - (expected & 0xFF))
            self.sh.t.assertEqual(crc16(rest[2:130]), int.from_bytes(rest[130:], 'big'))
            data += rest[2:130]
            expected += 1
            self.send(bytes([ACK]))


def every_byte(n):
    return bytes(i & 0xFF for i in range(n))


class LoadTest(unittest.TestCase):

    def load(self, sh, addr, data, **kw):
        sh.type(b'load %x\r' % addr)
        peer = Peer(sh)
        peer.upload(data, **kw)
        sh.expect(GOOD + b'> ', seconds=3)
        return peer

    def test_load_every_byte_value(self):
        # Includes $1A (Ctrl-Z), $1B (ESC) and $18 (CAN) as plain data.
        sh = Shell(self)
        data = every_byte(512)
        self.load(sh, 0x021000, data)
        self.assertEqual(bytes(sh.m.bus.ram[0x021000:0x021200]), data)
        sh.assertClean()

    def test_load_across_a_bank_boundary(self):
        sh = Shell(self)
        data = bytes(reversed(every_byte(256)))
        self.load(sh, 0x01FF80, data)
        self.assertEqual(sh.m.peek(0x01FF80, 1), 0xFF)
        self.assertEqual(bytes(sh.m.bus.ram[0x01FF80:0x020080]), data)

    def test_short_file_is_padded_in_memory(self):
        sh = Shell(self)
        sh.m.bus.ram[0x030000:0x030080] = b'\x55' * 128
        self.load(sh, 0x030000, b'hello')
        self.assertEqual(bytes(sh.m.bus.ram[0x030000:0x030080]), b'hello'.ljust(128, b'\0'))

    def test_damaged_block_is_sent_again(self):
        sh = Shell(self)
        data = every_byte(384)
        self.load(sh, 0x021000, data, damage=[2])
        self.assertEqual(bytes(sh.m.bus.ram[0x021000:0x021180]), data)

    def test_cancel_from_the_terminal(self):
        sh = Shell(self)
        sh.type(b'load 21000\r')
        peer = Peer(sh)
        peer.read_until({ord('C')}, seconds=3)
        peer.send(bytes([CAN, CAN]))
        sh.expect(ERROR + b'> ', seconds=5)
        self.assertIn(b'bank state', sh.command(b'ps'))

    def test_ctrl_z_works_again_afterwards(self):
        sh = Shell(self)
        self.load(sh, 0x021000, b'x' * 10)
        bank = sh.run(b'echoq')
        sh.pause(bank)                          # raw mode is off again


class SaveTest(unittest.TestCase):

    def save(self, sh, start, end):
        sh.type(b'save %x.%x\r' % (start, end))
        peer = Peer(sh)
        data = peer.download()
        sh.expect(GOOD + b'> ', seconds=3)
        return data

    def test_save_every_byte_value_across_a_bank_boundary(self):
        sh = Shell(self)
        data = every_byte(300)
        sh.m.bus.ram[0x03FFF0:0x03FFF0 + 300] = data
        got = self.save(sh, 0x03FFF0, 0x03FFF0 + 299)
        self.assertEqual(got, data.ljust(384, b'\0'))
        sh.assertClean()

    def test_save_one_byte(self):
        sh = Shell(self)
        sh.m.bus.ram[0x021234] = 0xA9
        self.assertEqual(self.save(sh, 0x021234, 0x021234), b'\xA9'.ljust(128, b'\0'))

    def test_round_trip(self):
        sh = Shell(self)
        data = bytes((i * 37 + 11) & 0xFF for i in range(640))
        LoadTest.load(self, sh, 0x050000, data)
        self.assertEqual(self.save(sh, 0x050000, 0x05027F), data)

    def test_esc_cancels_while_waiting(self):
        sh = Shell(self)
        sh.type(b'save 1000.10ff\r')
        Peer(sh)
        sh.m.acia.send(ESC)
        sh.expect(ERROR + b'> ', seconds=5)


class TransferTest(unittest.TestCase):

    def test_a_program_can_use_the_services(self):
        sh = Shell(self)
        bank = sh.run(b'xfer')
        data = every_byte(256)
        peer = Peer(sh)
        peer.upload(data)                       # its K_LOAD
        sh.expect(GOOD, seconds=3)
        self.assertEqual(bytes(sh.m.bus.ram[(bank << 16) + 0x8000:(bank << 16) + 0x8100]), data)
        sh.mark = peer.pos
        peer = Peer(sh)
        self.assertEqual(peer.download(), data)  # its K_SAVE, with the lock held
        sh.expect(GOOD + b'xfer ok\r\n> ', seconds=3)
        self.assertEqual(sh.word('con_owner'), 0)
        sh.assertClean()

    def test_a_background_caller_is_refused(self):
        sh = Shell(self)
        sh.background(b'bgxfer')
        sh.expect(b'bgxfer refused\r\n', seconds=2)
        self.assertNotIn(b'Begin XMODEM', bytes(sh.m.acia.output))
        self.assertEqual(sh.m.peek(0x079000), 0)
        self.assertIn(b'bank state', sh.command(b'ps'))

    def test_other_output_waits_for_the_transfer(self):
        # chatter prints all the time without taking the console lock. None
        # of it may land inside the transfer, and both ends must still agree.
        sh = Shell(self)
        sh.background(b'chatter')
        data = every_byte(256)
        sh.type(b'load 21000\r')
        peer = Peer(sh)
        peer.upload(data)
        sh.expect(GOOD, seconds=3)
        out = bytes(sh.m.acia.output)
        during = out[peer.start:out.index(GOOD, peer.start)]
        self.assertEqual(set(during), {ord('C'), ACK})      # only the protocol
        self.assertEqual(bytes(sh.m.bus.ram[0x021000:0x021100]), data)
        sh.type(b'save 21000.210ff\r')
        peer = Peer(sh)
        self.assertEqual(peer.download(), data)
        sh.expect(GOOD, seconds=3)
        sh.type(b'', b'chatter\r\n')                      # and it carries on after
        sh.assertClean()

    def test_usage(self):
        sh = Shell(self)
        for line in (b'save', b'save 1000', b'save 2000.1000', b'save 1000.zz',
                     b'load', b'load zz', b'load 1000 2000'):
            self.assertIn(b'usage: save', sh.command(line), line)


if __name__ == '__main__':
    unittest.main()
