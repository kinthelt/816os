"""Checks of the simulator's CPU on the behaviour the kernel relies on.

Each case cites the W65C816S datasheet section it follows.
"""

import unittest

from support import assemble, run_to_stp

NATIVE = """
start:
        clc
        xce
        rep #$30
        .a16
        .i16
        lda #$1FFF
        tcs
"""


class CPUTest(unittest.TestCase):

    def test_native_interrupt_frame(self):
        # 2.18: the interrupt pushes program bank, PC high, PC low, flags.
        m = assemble(NATIVE + """
        sep #$30
        .a8
        .i8
cop_at: cop $00
after:  stp
handler:
        stp
""", vectors={0xFFE4: 'handler'})
        run_to_stp(m)
        after = m.sym('after')
        self.assertEqual(m.peek(0x1FFF), 0x00)               # program bank
        self.assertEqual(m.peek(0x1FFD, 2), after)           # return address
        self.assertEqual(m.peek(0x1FFC) & 0x30, 0x30)        # M and X as they were
        self.assertEqual(m.cpu.s, 0x1FFB)
        self.assertTrue(m.cpu.p & 0x04)                      # I set

    def test_rti_restores_widths_and_bank(self):
        # 7.11: in native mode RTI also pulls the program bank.
        m = assemble(NATIVE + """
        sep #$20
        .a8
        lda #$DB                ; STP
        sta f:$031234
        lda #$03
        pha                     ; program bank 3
        rep #$20
        .a16
        pea $1234               ; return address
        sep #$20
        .a8
        lda #$30
        pha                     ; flags: 8-bit A and index
        rti
""")
        run_to_stp(m)
        self.assertEqual(m.cpu.pbr, 3)
        self.assertEqual(m.cpu.pc, 0x1235)      # just past the STP
        self.assertEqual(m.cpu.p & 0x30, 0x30)
        self.assertEqual(m.cpu.s, 0x1FFF)

    def test_b_accumulator_survives_8bit_mode(self):
        # 2.4 / 7.23: the high byte stays in B while M = 1.
        m = assemble(NATIVE + """
        lda #$1234
        sep #$20
        .a8
        lda #$56
        xba
        stp
""")
        run_to_stp(m)
        self.assertEqual(m.cpu.a, 0x5612)

    def test_8bit_index_clears_high_byte(self):
        # 2.7: setting X forces the index registers' high bytes to zero.
        m = assemble(NATIVE + """
        ldx #$1234
        ldy #$ABCD
        sep #$10
        rep #$10
        stp
""")
        run_to_stp(m)
        self.assertEqual(m.cpu.x, 0x34)
        self.assertEqual(m.cpu.y, 0xCD)

    def test_direct_page_indexed_does_not_wrap_in_native_mode(self):
        # 7.2 / Table 7-1
        m = assemble(NATIVE + """
        lda #$0000
        tcd
        lda #$BEEF
        sta $0110
        ldx #$0020
        lda $F0,x
        stp
""")
        run_to_stp(m)
        self.assertEqual(m.cpu.a, 0xBEEF)

    def test_direct_register_offsets_direct_page(self):
        # 2.6
        m = assemble(NATIVE + """
        lda #$0300
        tcd
        lda #$4242
        sta $10                 ; $0310
        stp
""")
        run_to_stp(m)
        self.assertEqual(m.peek(0x0310, 2), 0x4242)

    def test_absolute_uses_data_bank(self):
        m = assemble(NATIVE + """
        sep #$20
        .a8
        lda #$02
        pha
        plb
        lda #$77
        sta $1000               ; $02:1000
        rep #$20
        .a16
        ldy #$0010
        lda #$5A5A
        sta $FFF8,y             ; crosses into bank 3
        stp
""")
        run_to_stp(m)
        self.assertEqual(m.peek(0x021000), 0x77)
        self.assertEqual(m.peek(0x030008, 2), 0x5A5A)

    def test_jsl_rtl(self):
        m = assemble(NATIVE + """
        jsl sub
        lda #$0001
        stp
sub:    tsc
        tax
        rtl
""")
        run_to_stp(m)
        self.assertEqual(m.cpu.a, 1)
        self.assertEqual(m.cpu.x, 0x1FFC)          # 3 bytes pushed
        self.assertEqual(m.cpu.s, 0x1FFF)

    def test_mvn_copies_and_sets_data_bank(self):
        # 7.18
        m = assemble(NATIVE + """
        sep #$20
        .a8
        lda #$11
        sta $2000
        lda #$22
        sta $2001
        lda #$33
        sta $2002
        rep #$20
        .a16
        lda #2                  ; 3 bytes
        ldx #$2000
        ldy #$4000
        mvn $000000, $040000    ; ca65 takes addresses: banks 0 -> 4
        stp
""")
        run_to_stp(m)
        self.assertEqual(m.peek(0x044000, 3), 0x332211)
        self.assertEqual(m.cpu.dbr, 4)
        self.assertEqual(m.cpu.a, 0xFFFF)
        self.assertEqual(m.cpu.x, 0x2003)
        self.assertEqual(m.cpu.y, 0x4003)

    def test_tsb_trb(self):
        m = assemble(NATIVE + """
        sep #$20
        .a8
        stz $10
        lda #$01
        tsb $10                 ; was clear: Z = 1
        php
        tsb $10                 ; was set: Z = 0
        php
        trb $10
        stp
""")
        run_to_stp(m)
        self.assertTrue(m.peek(0x1FFF) & 0x02)
        self.assertFalse(m.peek(0x1FFE) & 0x02)
        self.assertEqual(m.peek(0x10), 0)

    def test_adc_sbc_flags(self):
        m = assemble(NATIVE + """
        clc
        lda #$7FFF
        adc #$0001              ; overflow, no carry
        php
        sec
        lda #$0000
        sbc #$0001              ; borrow: carry clear
        php
        sta $20
        stp
""")
        run_to_stp(m)
        p1 = m.peek(0x1FFF)
        p2 = m.peek(0x1FFE)
        self.assertTrue(p1 & 0x40)
        self.assertFalse(p1 & 0x01)
        self.assertTrue(p1 & 0x80)
        self.assertFalse(p2 & 0x01)
        self.assertEqual(m.peek(0x20, 2), 0xFFFF)

    def test_stack_relative_and_phd_pld(self):
        m = assemble(NATIVE + """
        lda #$0500
        tcd
        phd
        lda #$0000
        tcd
        lda 1,s
        pld
        stp
""")
        run_to_stp(m)
        self.assertEqual(m.cpu.a, 0x0500)
        self.assertEqual(m.cpu.d, 0x0500)


if __name__ == '__main__':
    unittest.main()
