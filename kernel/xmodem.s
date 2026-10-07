; xmodem.s - the K_LOAD and K_SAVE services: XMODEM/CRC over the console
;
; Ported from be6502's rom/xmodem.s, which is based on Daryl Rictor's
; XMODEM/CRC sender/receiver for the 65C02 (August 2002). The protocol
; handling is unchanged:
;
;   - 24-bit addresses; transfers run across bank boundaries.
;   - Files are raw memory images with no load address.
;   - CRC only, no fallback to checksums.
;   - Carry set on success, clear on failure or cancel (ESC from the
;     keyboard, or CAN from the other end).
;   - The receiver ACKs and drops a repeated block, NAKs a damaged one, and
;     gives up after 10 errors in a row. The sender ignores stray
;     characters while it waits for a reply, and ends with EOT/ACK.
;   - Files go in whole 128-byte blocks: the sender pads the last one with
;     zeros, and the receiver writes every byte, so a load can write up to
;     127 bytes of padding past the end of the file.
;
; What changed for 816os:
;
;   - The routines run as kernel services, called with JSL by any process,
;     like the BIOS's XLOAD and XSAVE. They have their own direct page and
;     block buffer in bank 0 (XM_DP, XM_BUF). The console lock makes sure
;     only one transfer runs at a time.
;   - Only the foreground process may transfer, since it is the one that
;     receives the console's input. Anyone else gets carry clear at once.
;   - Characters go through K_GETC and K_PUTC.
;   - Timeouts count the kernel's 200 Hz timer ticks instead of a timed
;     polling loop, which preemption would throw off. While waiting for a
;     character the caller yields, so other processes keep running.
;   - For the whole transfer the service holds the console lock, so no other
;     output can land in the middle of it, and turns on raw receive so that
;     a $1A byte isn't taken as Ctrl-Z.

.p816
.include "hw.inc"
.include "kernel.inc"
.include "macros.inc"
.include "kvars.inc"
.include "os816.inc"

.export k_load, k_save

; Direct page variables, in XMODEM's own direct page, XM_DP (same offsets
; as be6502).
blkno           = $36           ; block number
errcnt          = $37           ; error counter, 10 is the limit
crc             = $38           ; CRC (two bytes)
crch            = $39
ptr             = $3A           ; data pointer (three bytes)
ptrh            = $3B
ptrb            = $3C
count           = $3D           ; bytes left to send (three bytes)
counth          = $3E
countb          = $3F
retry2          = $42           ; timeout, in tenths of a second
deadline        = $43           ; tick count at which GetByte gives up (2 bytes)
locked          = $45           ; nonzero if this transfer took the console lock

Rbuff           = XM_BUF        ; <blk #> <~blk #> <128 bytes> <CRCH> <CRCL>

TICKS_1S        = 10            ; GetByte timeouts, in tenths of a second
TICKS_3S        = 30
TICKS_10S       = 100

; XMODEM control characters
SOH             = $01
EOT             = $04
ACK             = $06
NAK             = $15
CAN             = $18
CR              = $0D
LF              = $0A
ESC             = $1B

.macro CHROUT
        jsl K_PUTC
.endmacro

.segment "CODE"

; ---------------------------------------------------------------------------
; K_LOAD: receive a file into memory.
; In:  A = address of a 3-byte table (low, high, bank) in the caller's data
;      bank: where the file goes.
; Out: carry set if the transfer succeeded; clear if it failed, was
;      cancelled, or the caller isn't the foreground process.

k_load:
        SVC_ENTER
        pha
        tax                     ; table address
        A8
        lda SVC_DBR+2,s         ; read the table from the caller's bank
        pha
        plb
        A16
        lda a:0,x
        sta f:XM_DP+ptr
        A8
        lda a:2,x
        sta f:XM_DP+ptrb
        phk
        plb                     ; data bank 0
        A16
        jsr xm_open
        bcc @done
        AXY8
        jsr XModemRcv
        AXY16
        jsr xm_close
@done:
        pla                     ; the caller's A (carry unchanged)
        bcc @failed
        SVC_SEC
        SVC_EXIT
@failed:
        SVC_CLC
        SVC_EXIT

; ---------------------------------------------------------------------------
; K_SAVE: send memory as a file.
; In:  A = address of a 3-byte table (low, high, bank): the first byte to send.
;      X = address of a 3-byte table: how many bytes to send.
;      Both tables are in the caller's data bank.
; Out: carry set if the transfer succeeded; clear if it failed, was
;      cancelled, or the caller isn't the foreground process.

k_save:
        SVC_ENTER
        pha
        tay                     ; start table; X = count table
        A8
        lda SVC_DBR+2,s         ; read the tables from the caller's bank
        pha
        plb
        A16
        lda a:0,y
        sta f:XM_DP+ptr
        lda a:0,x
        sta f:XM_DP+count
        A8
        lda a:2,y
        sta f:XM_DP+ptrb
        lda a:2,x
        sta f:XM_DP+countb
        phk
        plb                     ; data bank 0
        A16
        jsr xm_open
        bcc @done
        AXY8
        jsr XModemSend
        AXY16
        jsr xm_close
@done:
        pla
        bcc @failed
        SVC_SEC
        SVC_EXIT
@failed:
        SVC_CLC
        SVC_EXIT

; xm_open: claim the console for a transfer.
; A, X, Y 16-bit, D = 0, data bank 0.
; Out: carry set, D = XM_DP, console locked, raw receive on. Carry clear
; (and nothing changed) if the caller isn't the foreground process.
xm_open:
        lda current_proc
        cmp fg_proc
        bne @refuse
        lda #XM_DP
        tcd
        A8
        stz locked
        A16
        lda current_proc        ; take the lock unless the caller has it already
        inc a
        inc a
        cmp con_owner
        beq @have_lock
        jsl K_CON_LOCK
        A8
        lda #1
        sta locked
        A16
@have_lock:
        A8
        lda #1
        sta rx_raw              ; Ctrl-Z is data until the transfer ends
        A16
        sec
        rts
@refuse:
        clc
        rts

; xm_close: undo xm_open. Keeps carry. A, X, Y 16-bit, D = XM_DP.
xm_close:
        php
        A8
        stz rx_raw
        lda locked
        beq @kept
        jsl K_CON_UNLOCK
@kept:
        A16
        plp
        rts

        .a8
        .i8

; ---------------------------------------------------------------------------
; XModemSend: send count bytes starting at ptr.
; Returns carry set on success, clear on failure or cancel.

XModemSend:
        jsr PrintMsg            ; send prompt and info
        lda #$01
        sta blkno               ; set block # to 1
Wait4CRC:
        lda #TICKS_3S           ; 3 seconds
        sta retry2
        jsr GetByte
        bcc Wait4CRC            ; wait for something to come in...
        cmp #'C'                ; is it the "C" to start a CRC xfer?
        beq LdBuffer            ; yes
        cmp #ESC                ; is it a cancel? <Esc> Key
        beq SCancel             ; yes
        cmp #CAN                ; cancelled by the receiver?
        bne Wait4CRC            ; No, wait for another character
SCancel:
        jmp Cancel              ; send CAN, print abort msg and exit

LdBuffer:
        lda count               ; anything left to send?
        ora counth
        ora countb
        beq SendEOT             ; no, end the transfer
        lda blkno
        sta Rbuff               ; save in 1st byte of buffer
        eor #$FF
        sta Rbuff+1             ; save 1's comp of blkno next
        ldx #$02                ; init buffer index
LdBuff1:
        lda [ptr]               ; get a data byte
        sta Rbuff,x             ; save it in the buffer
        inc ptr                 ; Inc address pointer
        bne LdBuff2
        inc ptrh
        bne LdBuff2
        inc ptrb                ; carry into the bank
LdBuff2:
        lda count               ; decrement the byte count
        bne LdBuff3
        lda counth
        bne LdBuff4
        dec countb
LdBuff4:
        dec counth
LdBuff3:
        dec count
        inx
        cpx #$82                ; last byte in block?
        beq SCalcCRC            ; yes, calc CRC
        lda count               ; any bytes left?
        ora counth
        ora countb
        bne LdBuff1             ; yes, get the next
LdBuff5:
        stz Rbuff,x             ; Fill rest of 128 bytes with $00
        inx
        cpx #$82                ; Are we at the end of the 128 byte block?
        bne LdBuff5             ; no, keep filling
SCalcCRC:
        jsr CalcCRC
        lda crch                ; save Hi byte of CRC to buffer
        sta Rbuff,y
        iny
        lda crc                 ; save lo byte of CRC to buffer
        sta Rbuff,y
        stz errcnt              ; error counter set to 0
Resend:
        ldx #$00
        lda #SOH
        CHROUT                  ; send SOH
SendBlk:
        lda Rbuff,x             ; Send 132 bytes in buffer to the console
        CHROUT
        inx
        cpx #$84                ; last byte?
        bne SendBlk             ; no, get next
        jsr GetReply            ; Wait for Ack/Nack
        bcc Seterror            ; No reply after 10 seconds, or NAK
        inc blkno               ; ACK, send next block
        bra LdBuffer
Seterror:
        jsr CountErr            ; Inc error counter
        bcc Resend              ; under 10 errors, resend block
SCancel2:
        jmp Cancel              ; too many errors or cancelled

SendEOT:
        stz errcnt              ; error counter set to 0
SendEOT1:
        lda #EOT                ; tell the receiver we're done
        CHROUT
        jsr GetReply            ; Wait for Ack/Nack
        bcs SDone               ; ACK, all done
        jsr CountErr            ; NAK or no reply, inc error counter
        bcc SendEOT1            ; under 10 errors, send EOT again
        bra SCancel2            ; too many errors or cancelled
SDone:
        jmp Print_Good          ; All Done..Print msg and exit (carry set)

; Wait up to 10 seconds for the receiver's reply to a block or EOT.
; Returns carry set for ACK; carry clear for NAK or a timeout, with A=NAK;
; and pulls the return address and cancels for ESC or CAN. Anything else
; is ignored.
GetReply:
        lda #TICKS_10S          ; 10 second delay
        sta retry2
GetReply1:
        jsr GetByte             ; Wait for Ack/Nack
        bcc GetReply3           ; No chr received after 10 seconds
        cmp #ACK                ; Chr received... is it:
        beq GetReply2           ; ACK, return carry set
        cmp #NAK
        beq GetReply3           ; NAK, return carry clear
        cmp #ESC
        beq GetReply4           ; Esc pressed to abort
        cmp #CAN
        beq GetReply4           ; cancelled by the receiver
        dec retry2              ; anything else: ignore it, but let it
        bne GetReply1           ; use up a tenth of the 10 seconds
GetReply3:
        lda #NAK
        clc
GetReply2:
        rts
GetReply4:
        pla                     ; drop the return address
        pla
        jmp Cancel              ; and cancel the transfer

; Count an error. Returns carry set once there have been 10 in a row
; (Xmodem spec for failure).
CountErr:
        inc errcnt
        lda errcnt
        cmp #$0A                ; are there 10 errors?
        rts                     ; carry set if so

; ---------------------------------------------------------------------------
; XModemRcv: receive a file into memory starting at ptr.
; Returns carry set on success, clear on failure or cancel.

XModemRcv:
        jsr PrintMsg            ; send prompt and info
        jsr Flush               ; drop anything left over from the command line
        lda #$01
        sta blkno               ; set block # to 1
        stz errcnt              ; error counter set to 0
StartCrc:
        lda #'C'                ; "C" start with CRC mode
        CHROUT                  ; send it
        lda #TICKS_3S
        sta retry2              ; wait ~3 seconds for the sender
        jsr GetByte             ; wait for input
        bcs GotByte             ; byte received, process it
        bcc StartCrc            ; resend "C"

StartBlk:
        lda #TICKS_10S
        sta retry2              ; wait ~10 seconds
        jsr GetByte             ; get first byte of block
        bcc BadCrc              ; timed out, send NAK
GotByte:
        cmp #ESC                ; quitting?
        beq RCancel             ; yes
        cmp #CAN                ; cancelled by the sender?
        beq RCancel             ; yes
        cmp #SOH                ; start of block?
        beq BegBlk              ; yes
        cmp #EOT
        bne BadCrc              ; Not SOH or EOT, so flush buffer & send NAK
        jmp RDone               ; EOT - all done!
BegBlk:
        ldx #$00
GetBlk:
        lda #TICKS_1S           ; 1 sec window to receive characters
        sta retry2
GetBlk1:
        jsr GetByte             ; get next character
        bcc BadCrc              ; chr rcv error, flush and send NAK
GetBlk2:
        sta Rbuff,x             ; good char, save it in the rcv buffer
        inx                     ; inc buffer pointer
        cpx #$84                ; <01> <FE> <128 bytes> <CRCH> <CRCL>
        bne GetBlk              ; get 132 characters
        lda Rbuff               ; get block # from buffer
        eor Rbuff+1             ; with its 1's comp the result is $FF
        inc a                   ; and this makes it zero
        bne BadCrc              ; damaged header, send NAK
        jsr CalcCRC             ; calc CRC
        lda Rbuff,y             ; get hi CRC from buffer
        cmp crch                ; compare to calculated hi CRC
        bne BadCrc              ; bad crc, send NAK
        iny
        lda Rbuff,y             ; get lo CRC from buffer
        cmp crc                 ; compare to calculated lo CRC
        bne BadCrc              ; bad crc, send NAK
        lda Rbuff               ; get block # from buffer
        cmp blkno               ; compare to expected block #
        beq GoodCrc             ; matched!
        inc a
        cmp blkno               ; the previous block again?
        beq SendAck             ; yes, our ACK was lost: ACK and drop it
RCancel:
        jmp Cancel              ; out of sequence - fatal error
BadCrc:
        jsr CountErr            ; Inc error counter
        bcs RCancel             ; 10 errors, give up
        jsr Flush               ; flush the input port
        lda #NAK
        CHROUT                  ; send NAK to resend block
        bra StartBlk            ; start over, get the block again
GoodCrc:
        ldx #$02
CopyBlk3:
        lda Rbuff,x             ; get data byte from buffer
        sta [ptr]               ; save to target
        inc ptr                 ; point to next address
        bne CopyBlk4            ; did it step over page boundary?
        inc ptrh                ; adjust high address for page crossing
        bne CopyBlk4            ; did it step over bank boundary?
        inc ptrb                ; adjust bank for bank crossing
CopyBlk4:
        inx                     ; point to next data byte
        cpx #$82                ; is it the last byte
        bne CopyBlk3            ; no, get the next one
IncBlk:
        inc blkno               ; done.  Inc the block #
SendAck:
        stz errcnt              ; error counter set to 0
        lda #ACK                ; send ACK
        CHROUT
        jmp StartBlk            ; get next block

RDone:
        lda #ACK                ; last block, send ACK and exit.
        CHROUT
        jsr Flush               ; get leftover characters, if any
        jmp Print_Good          ; print msg and exit (carry set)

; ---------------------------------------------------------------------------
; Subroutines

; GetByte: wait up to retry2 tenths of a second for a character.
; Returns carry set with the character in A, or carry clear on a timeout.
; Keeps X and Y.
GetByte:
        A16
        lda retry2              ; ticks = tenths * 20
        and #$00FF
        asl a
        asl a
        sta deadline            ; * 4
        asl a
        asl a                   ; * 16
        clc
        adc deadline
        clc
        adc ticks               ; low 16 bits of the tick count
        sta deadline
        A8
@poll:
        jsl K_GETC
        bcs @got
        jsl K_YIELD             ; let other processes run meanwhile
        A16
        lda ticks
        sec
        sbc deadline            ; still before the deadline (as a signed
        A8                      ; difference, so it survives wrapping)?
        bmi @poll
        clc
@got:
        rts

Flush:
        lda #TICKS_1S           ; flush receive buffer
        sta retry2              ; flush until empty for ~1 sec.
Flush1:
        jsr GetByte             ; read the port
        bcs Flush               ; if chr recvd, wait for another
        rts                     ; else done

; Cancel the transfer: tell the other end, wait for it to go quiet, then
; print the error message. Returns carry clear.
Cancel:
        lda #CAN                ; two CANs cancel a transfer
        CHROUT
        CHROUT
        jsr Flush               ; drain whatever is still coming in
        A16
        lda #ErrMsg
        jsl K_PUTS
        A8
        clc                     ; failed
        rts

Print_Good:
        A16
        lda #GoodMsg
        jsl K_PUTS
        A8
        sec                     ; succeeded
        rts

PrintMsg:
        A16
        lda #Msg
        jsl K_PUTS
        A8
        rts

; CalcCRC: CRC-16 of the 128 data bytes in Rbuff. Y = $82 on exit.
CalcCRC:
        lda #$00
        sta crc
        sta crch
        ldy #$02
CalcCRC1:
        lda Rbuff,y
        eor crc+1               ; Quick CRC computation with lookup tables
        tax                     ; updates the two bytes at crc & crc+1
        lda crc                 ; with the byte send in the "A" register
        eor crchi,x
        sta crc+1
        lda crclo,x
        sta crc
        iny
        cpy #$82                ; done yet?
        bne CalcCRC1            ; no, get next
        rts

.segment "RODATA"

Msg:        .byte CR, LF, "Begin XMODEM/CRC transfer.  Press <Esc> to abort...", CR, LF, 0
ErrMsg:     .byte CR, LF, "Transfer Error!", CR, LF, 0
GoodMsg:    .byte CR, LF, "Transfer Successful!", CR, LF, 0

; CRC lookup tables
crclo:
 .byte $00,$21,$42,$63,$84,$A5,$C6,$E7,$08,$29,$4A,$6B,$8C,$AD,$CE,$EF
 .byte $31,$10,$73,$52,$B5,$94,$F7,$D6,$39,$18,$7B,$5A,$BD,$9C,$FF,$DE
 .byte $62,$43,$20,$01,$E6,$C7,$A4,$85,$6A,$4B,$28,$09,$EE,$CF,$AC,$8D
 .byte $53,$72,$11,$30,$D7,$F6,$95,$B4,$5B,$7A,$19,$38,$DF,$FE,$9D,$BC
 .byte $C4,$E5,$86,$A7,$40,$61,$02,$23,$CC,$ED,$8E,$AF,$48,$69,$0A,$2B
 .byte $F5,$D4,$B7,$96,$71,$50,$33,$12,$FD,$DC,$BF,$9E,$79,$58,$3B,$1A
 .byte $A6,$87,$E4,$C5,$22,$03,$60,$41,$AE,$8F,$EC,$CD,$2A,$0B,$68,$49
 .byte $97,$B6,$D5,$F4,$13,$32,$51,$70,$9F,$BE,$DD,$FC,$1B,$3A,$59,$78
 .byte $88,$A9,$CA,$EB,$0C,$2D,$4E,$6F,$80,$A1,$C2,$E3,$04,$25,$46,$67
 .byte $B9,$98,$FB,$DA,$3D,$1C,$7F,$5E,$B1,$90,$F3,$D2,$35,$14,$77,$56
 .byte $EA,$CB,$A8,$89,$6E,$4F,$2C,$0D,$E2,$C3,$A0,$81,$66,$47,$24,$05
 .byte $DB,$FA,$99,$B8,$5F,$7E,$1D,$3C,$D3,$F2,$91,$B0,$57,$76,$15,$34
 .byte $4C,$6D,$0E,$2F,$C8,$E9,$8A,$AB,$44,$65,$06,$27,$C0,$E1,$82,$A3
 .byte $7D,$5C,$3F,$1E,$F9,$D8,$BB,$9A,$75,$54,$37,$16,$F1,$D0,$B3,$92
 .byte $2E,$0F,$6C,$4D,$AA,$8B,$E8,$C9,$26,$07,$64,$45,$A2,$83,$E0,$C1
 .byte $1F,$3E,$5D,$7C,$9B,$BA,$D9,$F8,$17,$36,$55,$74,$93,$B2,$D1,$F0

crchi:
 .byte $00,$10,$20,$30,$40,$50,$60,$70,$81,$91,$A1,$B1,$C1,$D1,$E1,$F1
 .byte $12,$02,$32,$22,$52,$42,$72,$62,$93,$83,$B3,$A3,$D3,$C3,$F3,$E3
 .byte $24,$34,$04,$14,$64,$74,$44,$54,$A5,$B5,$85,$95,$E5,$F5,$C5,$D5
 .byte $36,$26,$16,$06,$76,$66,$56,$46,$B7,$A7,$97,$87,$F7,$E7,$D7,$C7
 .byte $48,$58,$68,$78,$08,$18,$28,$38,$C9,$D9,$E9,$F9,$89,$99,$A9,$B9
 .byte $5A,$4A,$7A,$6A,$1A,$0A,$3A,$2A,$DB,$CB,$FB,$EB,$9B,$8B,$BB,$AB
 .byte $6C,$7C,$4C,$5C,$2C,$3C,$0C,$1C,$ED,$FD,$CD,$DD,$AD,$BD,$8D,$9D
 .byte $7E,$6E,$5E,$4E,$3E,$2E,$1E,$0E,$FF,$EF,$DF,$CF,$BF,$AF,$9F,$8F
 .byte $91,$81,$B1,$A1,$D1,$C1,$F1,$E1,$10,$00,$30,$20,$50,$40,$70,$60
 .byte $83,$93,$A3,$B3,$C3,$D3,$E3,$F3,$02,$12,$22,$32,$42,$52,$62,$72
 .byte $B5,$A5,$95,$85,$F5,$E5,$D5,$C5,$34,$24,$14,$04,$74,$64,$54,$44
 .byte $A7,$B7,$87,$97,$E7,$F7,$C7,$D7,$26,$36,$06,$16,$66,$76,$46,$56
 .byte $D9,$C9,$F9,$E9,$99,$89,$B9,$A9,$58,$48,$78,$68,$18,$08,$38,$28
 .byte $CB,$DB,$EB,$FB,$8B,$9B,$AB,$BB,$4A,$5A,$6A,$7A,$0A,$1A,$2A,$3A
 .byte $FD,$ED,$DD,$CD,$BD,$AD,$9D,$8D,$7C,$6C,$5C,$4C,$3C,$2C,$1C,$0C
 .byte $EF,$FF,$CF,$DF,$AF,$BF,$8F,$9F,$6E,$7E,$4E,$5E,$2E,$3E,$0E,$1E
