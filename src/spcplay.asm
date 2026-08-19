; SNES SPC album player
; Original single-song player /Mic, 2010
; Multi-song menu / album support added on top of that base.
;
; IMPORTANT 65816 WIDTH-SAFETY NOTE:
; LoadSPC (loadspc.asm) internally searches the copied SPC RAM for a free
; block of memory to inject its init code, and that search has several
; different exit paths (it depends on the actual byte content of each
; song's SPC RAM dump, i.e. it is DATA-DEPENDENT). Those exit paths do not
; all leave the 65816's accumulator/index width (the M/X flags) in the
; same state. Because of that, every routine below is written to be
; "width self-contained": each one explicitly sets (via sep/rep) whatever
; accumulator/index width it needs before using it, rather than assuming
; a width inherited from its caller. In particular, code that runs right
; after "jsr LoadSPC" always re-asserts a known width before doing
; anything else. Skipping this discipline is what caused songs to
; intermittently fail to play / the screen to fail to update / input to
; appear to "lock up" in earlier versions of this file.

; album_config.inc MUST be included before "snes.inc", because it defines
; NUM_ROMBANKS and ROMSIZE_CODE which header.asm (pulled in by snes.inc)
; needs in order to size the cartridge header correctly.
.include "album_config.inc"
.include "snes.inc"


; --- Zero page variables -----------------------------------------------
; NOTE: while LoadSPC (in loadspc.asm) is executing it freely uses
; $00f0-$00fe as scratch space, so none of our persistent state lives in
; that range.
.define cursorIndex    $00e0   ; currently highlighted song index
.define scrollTop      $00e1   ; index of the topmost visible song
.define appState        $00e2   ; 0 = menu, 1 = playing, 2 = about (see APPSTATE_*)
.define prevJoyH        $00e3
.define prevJoyL        $00e4
.define joyH             $00e5
.define joyL             $00e6
.define joyPressedH      $00e7
.define joyPressedL      $00e8
.define menuRowSong      $00e9
.define putsColor        $00eb
.define vramAddr         $00ec   ; 2 bytes
.define menuRowCounter   $00ee
.define digitTens        $00ef
.define menuRowPalette   $00ea
.define savedRowAddr     $00d0   ; 2 bytes; distinct from PutsSingle's own
                                  ; internal "vramAddr" scratch so the two
                                  ; never clobber each other

; A small scratch buffer (regular RAM, not zero page) used to compose each
; line of text before it is sent to VRAM.
.define lineBuffer $0300

; A full off-screen copy of BG1's 32x32 tilemap (2048 bytes = 1024 tiles *
; 2 bytes each). All drawing happens into this WRAM buffer - which is safe
; to write at any time, unlike VRAM - and the whole thing is then blasted
; to VRAM in one DMA transfer (fast enough to fit inside a single vblank
; period) so redraws never flicker or get silently dropped by the PPU.
.define tilemapBuffer $1000

.define APPSTATE_MENU    0
.define APPSTATE_PLAYING 1
.define APPSTATE_ABOUT   2

.define TOTAL_ENTRIES (NUM_SONGS+1)  ; +1 for the unnumbered "ABOUT" entry
                                      ; appended at the end of the list

.define VISIBLE_SONGS  10   ; number of songs shown on screen at once
.define ENTRY_HEIGHT    2   ; tile rows used per song entry (author + title)
.define LIST_TOP_ROW    3   ; first tile row used by the song list

; Joypad bit masks (standard SNES controller, auto-read registers)
.define JOY_H_B      $80
.define JOY_H_Y      $40
.define JOY_H_SELECT $20
.define JOY_H_START  $10
.define JOY_H_UP     $08
.define JOY_H_DOWN   $04
.define JOY_H_LEFT   $02
.define JOY_H_RIGHT  $01
.define JOY_L_A      $80
.define JOY_L_X      $40


 .bank 0
 .section "MainCode"

 .include "loadspc.asm"

 Start:
	; Initialize the SNES.
	Snes_Init

	sep	#(A_8BIT|XY_8BIT)

	lda     #BLANK_SCREEN  	; Force VBlank by turning off the screen.
	sta     REG_DISPCNT

	SetPalette palette,0,96
	LoadVRAM font,0,(font_end-font)

	; Set display mode 1 (BG1 uses 16-color tiles)
	lda	#1
	sta     REG_DISPMODE

	lda     #BG1_ENABLE
	sta     REG_BGCNT

	; Set the pattern base address for BG1 to $0000
	stz     REG_CHRBASE_L

	; Set the map base address for BG1 to $4000 (word $2000) and 32x32 tiles
	lda     #32
	sta     REG_BG1MAP

	sep	#A_8BIT
	stz	cursorIndex
	stz	scrollTop
	stz	appState
	stz	prevJoyH
	stz	prevJoyL

	jsr	ClearBG1
	jsr	DrawMenu

	sep     #A_8BIT

	lda     #$0F  		; End VBlank, setting brightness to 15 (100%).
	sta     REG_DISPCNT

 	cli             ; Enable IRQ
 	sep     #A_8BIT ; Enable NMI + auto joypad read
 	lda     #$81
 	sta     REG_NMI_TIMEN

Forever:
	WaitVsync
	jsr	ReadJoypad

	sep	#A_8BIT
	lda	appState
	cmp	#APPSTATE_MENU
	bne	@notMenu
	jsr	MenuHandleInput
	bra	Forever
	@notMenu:
	cmp	#APPSTATE_PLAYING
	bne	@about
	jsr	PlayingHandleInput
	bra	Forever
	@about:
	jsr	AboutHandleInput
	bra	Forever


; -------------------------------------------------------------------------
; Input handling
; -------------------------------------------------------------------------

; Reads the joypad and computes which buttons were newly pressed this
; frame (rising edge) into joyPressedH / joyPressedL.
ReadJoypad:
	php
	sep	#A_8BIT

	; The hardware auto-joypad-read takes a little while after vblank
	; starts to finish latching $4218/$4219. Reading them before it's
	; done can return a stale/mid-shift value, and - more importantly for
	; this player, since DrawMenu/DrawPlayingScreen trigger a DMA
	; transfer shortly after this runs - starting a DMA transfer while
	; the auto-read is still in progress can disturb it. Waiting for
	; REG_HVB_JOY bit 0 to clear avoids both problems.
	@waitAutoRead:
	lda	REG_HVB_JOY
	and	#$01
	bne	@waitAutoRead

	lda	REG_JOY1H
	sta	joyH
	lda	REG_JOY1L
	sta	joyL

	lda	joyH
	eor	prevJoyH
	and	joyH
	sta	joyPressedH

	lda	joyL
	eor	prevJoyL
	and	joyL
	sta	joyPressedL

	lda	joyH
	sta	prevJoyH
	lda	joyL
	sta	prevJoyL

	plp
	rts


MenuHandleInput:
	php
	sep	#A_8BIT

	lda	joyPressedH
	and	#JOY_H_DOWN
	beq	+
	jsr	MenuMoveDown
	+:
	lda	joyPressedH
	and	#JOY_H_UP
	beq	+
	jsr	MenuMoveUp
	+:
	lda	joyPressedH
	and	#JOY_H_START
	bne	@select
	lda	joyPressedL
	and	#JOY_L_A
	beq	@noSelect
	@select:
	jsr	MenuSelect
	@noSelect:

	plp
	rts


MenuMoveDown:
	php
	sep	#A_8BIT

	lda	cursorIndex
	cmp	#(TOTAL_ENTRIES-1)
	bcc	@advance
	; already at the last entry (ABOUT) - wrap around to the start
	stz	cursorIndex
	stz	scrollTop
	jsr	DrawMenu
	bra	@done
	@advance:
	inc	cursorIndex
	lda	cursorIndex
	sec
	sbc	scrollTop
	cmp	#VISIBLE_SONGS
	bcc	@redraw
	inc	scrollTop
	@redraw:
	jsr	DrawMenu
	@done:

	plp
	rts


MenuMoveUp:
	php
	sep	#A_8BIT

	lda	cursorIndex
	bne	@retreat
	; already at the first entry - wrap around to the end (ABOUT)
	lda	#(TOTAL_ENTRIES-1)
	sta	cursorIndex
	; scrollTop = max(0, TOTAL_ENTRIES - VISIBLE_SONGS), clamped at runtime
	; so this stays correct regardless of how NUM_SONGS compares to
	; VISIBLE_SONGS for any given album.
	lda	#TOTAL_ENTRIES
	sec
	sbc	#VISIBLE_SONGS
	bpl	@noClamp
	lda	#0
	@noClamp:
	sta	scrollTop
	jsr	DrawMenu
	bra	@done
	@retreat:
	dec	cursorIndex
	lda	cursorIndex
	cmp	scrollTop
	bcs	@redraw
	dec	scrollTop
	@redraw:
	jsr	DrawMenu
	@done:

	plp
	rts


MenuSelect:
	php
	sep	#A_8BIT

	lda	cursorIndex
	cmp	#NUM_SONGS
	bne	@songSelected

	; the unnumbered "ABOUT" entry was selected - no song to load
	lda	#APPSTATE_ABOUT
	sta	appState
	jsr	DrawAboutScreen
	bra	@done

	@songSelected:
	lda	cursorIndex
	jsr	LoadSPC
	sep	#(A_8BIT|XY_8BIT)

	lda	#$81
	sta	REG_NMI_TIMEN

	lda	#APPSTATE_PLAYING
	sta	appState

	; Draw the playing screen (the routine itself takes care of syncing with VBlank)
	jsr	DrawPlayingScreen

	@done:
	plp
	rts


; B returns from the About screen to the song list.
AboutHandleInput:
	php
	sep	#A_8BIT

	lda	joyPressedH
	and	#JOY_H_B
	beq	@done

	lda	#APPSTATE_MENU
	sta	appState
	jsr	DrawMenu

@done:
	plp
	rts


PlayingHandleInput:
	php
	sep	#A_8BIT

	lda	joyPressedH
	and	#JOY_H_B
	beq	@done

	; 1. Send the $FF exit signal to the patched SPC
	lda	#$FF
	sta	REG_APUI00

	; 2. Wait until the SPC mutes the sound, enables the IPL ROM, and signals back $AA
@waitIPL:
	lda	REG_APUI00
	cmp	#$AA
	bne	@waitIPL

	; 3. Switch the app state and redraw the menu
	lda	#APPSTATE_MENU
	sta	appState
	jsr	DrawMenu

@done:
	plp
	rts


; -------------------------------------------------------------------------
; Drawing
; -------------------------------------------------------------------------

; Clears the off-screen tilemap buffer (not VRAM directly - see
; tilemapBuffer above).
ClearBG1:
	php
	rep	#(A_8BIT|XY_8BIT)
	ldx	#0
	lda	#0
	@loop:
	sta.w	tilemapBuffer,x
	inx
	inx
	cpx	#2048
	bne	@loop
	plp
	rts


; Blasts the whole 2048-byte tilemap buffer to VRAM (BG1's 32x32 map at
; word address $2000) via DMA channel 0. A transfer this size comfortably
; finishes well within one vblank period, so - unlike writing tile-by-tile
; with the CPU - this never spills into active-display time and never gets
; silently dropped by the PPU. No need to force the screen blank for this.
CommitTilemapToVRAM:
	php
	sep	#A_8BIT

	stz	REG_VRAM_ADDR_L
	lda	#$20
	sta	REG_VRAM_ADDR_H
	lda	#VRAM_WORD_ACCESS
	sta	REG_VRAM_INC

	lda	#$01			; 2 B-bus regs, write each once (word pattern)
	sta	REG_DMAP0
	lda	#<REG_VRAM_DATAW1
	sta	REG_BBAD0
	stz	REG_A1B0		; source bank 0 (WRAM)

	rep	#A_8BIT
	lda	#tilemapBuffer
	sta	REG_A1T0L
	lda	#2048
	sta	REG_DAS0L
	sep	#A_8BIT

	lda	#$01
	sta	REG_MDMAEN

	plp
	rts


; Redraws the header + the currently-visible page of the song list, in the
; form:
;   01. Author
;       Title
;   02. Author 2
;       Title 2
;
;   ABOUT
; ("ABOUT" is an extra, unnumbered entry appended after the last song -
; see TOTAL_ENTRIES / MenuSelect / DrawAboutScreen.)
DrawMenu:
	php
	jsr	ClearBG1
	rep	#XY_8BIT
	sep	#A_8BIT

	lda	#0
	ldx	#albumTitleText
	ldy	#($2000+1*32+1)
	jsr	PutsSingle

	lda	scrollTop
	sta	menuRowSong
	stz	menuRowCounter

	@rowLoop:
	lda	menuRowSong
	cmp	#TOTAL_ENTRIES
	bcs	@rowLoop_next		; past the end of the list: leave the rows blank

	lda	menuRowSong
	cmp	cursorIndex
	bne	@normalPal
	lda	#1			; highlighted/selected entry palette (yellow)
	bra	@gotPal
	@normalPal:
	lda	#2			; normal entry palette (white)
	@gotPal:
	sta	menuRowPalette

	lda	menuRowSong
	cmp	#NUM_SONGS
	beq	@aboutRow

	; author row: word_addr = $2000 + (LIST_TOP_ROW + menuRowCounter*ENTRY_HEIGHT)*32 + 1
	jsr	CalcEntryRowAddr
	sty	savedRowAddr		; Y gets clobbered by BuildAuthorLine below, so stash it
	lda	menuRowSong
	jsr	BuildAuthorLine
	lda	menuRowPalette
	ldx	#lineBuffer
	ldy	savedRowAddr
	jsr	PutsSingle

	; title row: one tile row below the author row, indented by 4 columns
	jsr	CalcEntryRowAddr
	rep	#A_8BIT
	tya
	clc
	adc	#32
	tay
	sep	#A_8BIT
	sty	savedRowAddr		; Y gets clobbered by BuildTitleLine below, so stash it
	lda	menuRowSong
	jsr	BuildTitleLine
	lda	menuRowPalette
	ldx	#lineBuffer
	ldy	savedRowAddr
	jsr	PutsSingle
	bra	@rowLoop_next

	; the unnumbered "ABOUT" entry - single line, no author/title split
	@aboutRow:
	jsr	CalcEntryRowAddr
	rep	#A_8BIT
	tya
	clc
	adc	#32
	tay
	sep	#A_8BIT
	lda	menuRowPalette
	ldx	#aboutMenuText
	jsr	PutsSingle

	@rowLoop_next:
	inc	menuRowSong
	inc	menuRowCounter
	lda	menuRowCounter
	cmp	#VISIBLE_SONGS
	bne	@rowLoop

	@done:
	sep	#A_8BIT
	WaitVsync
	jsr	CommitTilemapToVRAM

	plp
	rts


; Computes the VRAM word address of the author row for the song entry
; currently being drawn (menuRowCounter), and returns it in Y.
; word_addr = $2000 + (LIST_TOP_ROW + menuRowCounter*ENTRY_HEIGHT)*32 + 1
CalcEntryRowAddr:
	php
	sep	#A_8BIT
	lda	menuRowCounter
	asl	a			; * ENTRY_HEIGHT (2)
	clc
	adc	#LIST_TOP_ROW
	rep	#A_8BIT
	and	#$00FF
	asl	a
	asl	a
	asl	a
	asl	a
	asl	a
	clc
	adc	#($2000+1)
	tay
	plp
	rts


; Shows which song is currently playing:
;   Author
;   Title
;   Game (only if present)
DrawPlayingScreen:
	php
	jsr	ClearBG1
	rep	#XY_8BIT
	sep	#A_8BIT

	lda	#0
	ldx	#playingHeaderText
	ldy	#($2000+1*32+1)		; Column 1 (8px from the left)
	jsr	PutsSingle

	; --- AUTHOR ---
	lda	cursorIndex
	jsr	GetAuthorPtr
	jsr	CopyToLineBuffer

	rep	#A_8BIT
	lda	#$0460				; Paleta 1 ($0400) | Kafelek ($0060)
	sta.w	tilemapBuffer + $00C2		; Row 3, Column 1 (3 * 32 + 1 = 97 words = $C2 bytes)
	sep	#A_8BIT

	lda	#2
	ldx	#lineBuffer
	ldy	#($2000+3*32+3)			; Row 3, Column 3 (spacing gap after the icon)
	jsr	PutsSingle

	; --- TITLE ---
	lda	cursorIndex
	jsr	GetTitlePtr
	jsr	CopyToLineBuffer

	rep	#A_8BIT
	lda	#$0462				; Paleta 1 ($0400) | Kafelek ($0062)
	sta.w	tilemapBuffer + $0102		; Row 4, Column 1 (4 * 32 + 1 = 129 words = $102 bytes)
	sep	#A_8BIT

	lda	#2
	ldx	#lineBuffer
	ldy	#($2000+4*32+3)			; Row 4, Column 3
	jsr	PutsSingle

	; --- GAME ---
	lda	cursorIndex
	jsr	GetGamePtr
	lda.w	$0000,x
	beq	@noGame
	jsr	CopyToLineBuffer

	rep	#A_8BIT
	lda	#$0463				; Paleta 1 ($0400) | Kafelek ($0063)
	sta.w	tilemapBuffer + $0142		; Row 5, Column 1 (5 * 32 + 1 = 161 words = $142 bytes)
	sep	#A_8BIT

	lda	#2
	ldx	#lineBuffer
	ldy	#($2000+5*32+3)			; Row 5, Column 3
	jsr	PutsSingle
	@noGame:

	lda	#0
	ldx	#playingHintText
	ldy	#($2000+7*32+1)
	jsr	PutsSingle

	sep	#A_8BIT

	; 1. Clear the VBlank flag before waiting
	lda	REG_RDNMI

	WaitVsync

	; 2. Wait for the auto-joypad-read to finish before the DMA transfer
@waitAutoRead:
	lda	REG_HVB_JOY
	and	#$01
	bne	@waitAutoRead

	; 3. Safe DMA transfer to VRAM right at the start of VBlank
	jsr	CommitTilemapToVRAM

	plp
	rts


; Shows the content of about.txt (read at build time - see build_album.py /
; aboutLineTable), one line per row starting below the header, with a "back"
; hint at a fixed row near the bottom of the screen.
DrawAboutScreen:
	php
	jsr	ClearBG1
	rep	#XY_8BIT
	sep	#A_8BIT

	lda	#0
	ldx	#aboutHeaderText
	ldy	#($2000+1*32+1)
	jsr	PutsSingle

	stz	menuRowCounter		; reused here as a simple 0-based line counter
	@lineLoop:
	lda	menuRowCounter
	cmp	#NUM_ABOUT_LINES
	bcs	@linesDone

	; word_addr = $2000 + (3+menuRowCounter)*32 + 1
	lda	menuRowCounter
	clc
	adc	#3
	rep	#A_8BIT
	and	#$00FF
	asl	a
	asl	a
	asl	a
	asl	a
	asl	a
	clc
	adc	#($2000+1)
	tay
	sep	#A_8BIT

	lda	menuRowCounter
	jsr	GetAboutLinePtr
	lda	#2			; white
	jsr	PutsSingle

	inc	menuRowCounter
	bra	@lineLoop
	@linesDone:

	lda	#0
	ldx	#aboutHintText
	ldy	#($2000+27*32+1)
	jsr	PutsSingle

	sep	#A_8BIT
	WaitVsync
	jsr	CommitTilemapToVRAM

	plp
	rts


; -------------------------------------------------------------------------
; Song text helpers
; -------------------------------------------------------------------------

; in: a = song index (0-based). Returns a pointer to the song's author
; string in X. Self-contained: caller's A/X/Y width is preserved.
GetAuthorPtr:
	php
	sep	#A_8BIT
	asl	a
	rep	#(A_8BIT|XY_8BIT)
	and	#$00FF
	tax
	lda.w	songAuthorTable,x
	tax
	plp
	rts

; in: a = song index (0-based). Returns a pointer to the song's title
; string in X.
GetTitlePtr:
	php
	sep	#A_8BIT
	asl	a
	rep	#(A_8BIT|XY_8BIT)
	and	#$00FF
	tax
	lda.w	songTitleTable,x
	tax
	plp
	rts

; in: a = song index (0-based). Returns a pointer to the song's game
; string in X (points at a single 0 byte if there is no game name).
GetGamePtr:
	php
	sep	#A_8BIT
	asl	a
	rep	#(A_8BIT|XY_8BIT)
	and	#$00FF
	tax
	lda.w	songGameTable,x
	tax
	plp
	rts


; in: a = about-screen line index (0-based). Returns a pointer to that
; line's string in X. Self-contained, same pattern as GetAuthorPtr etc.
GetAboutLinePtr:
	php
	sep	#A_8BIT
	asl	a
	rep	#(A_8BIT|XY_8BIT)
	and	#$00FF
	tax
	lda.w	aboutLineTable,x
	tax
	plp
	rts


; Copies the null-terminated string pointed to by X into lineBuffer,
; overwriting any previous content, null-terminated.
CopyToLineBuffer:
	php
	sep	#A_8BIT
	rep	#XY_8BIT
	ldy	#0
	jsr	AppendString
	lda	#0
	sta.w	lineBuffer,y
	plp
	rts


; Builds "NN. Author" into lineBuffer (NN = song index + 1, zero-padded to
; two digits; entries beyond 99 will lose their leading digit(s)).
; in: a = song index (0-based)
BuildAuthorLine:
	php
	sep	#A_8BIT
	rep	#XY_8BIT

	pha
	jsr	FormatTwoDigitNumber	; writes 2 digits + ". " into lineBuffer, returns y = write cursor
	pla

	jsr	GetAuthorPtr
	jsr	AppendString

	lda	#0
	sta.w	lineBuffer,y

	plp
	rts


; Builds "    Title" (4-space indent to line up under "NN. ") into
; lineBuffer.
; in: a = song index (0-based)
BuildTitleLine:
	php
	sep	#A_8BIT
	rep	#XY_8BIT

	pha
	ldy	#0
	lda	#' '
	sta.w	lineBuffer+0,y
	sta.w	lineBuffer+1,y
	sta.w	lineBuffer+2,y
	sta.w	lineBuffer+3,y
	ldy	#4
	pla

	jsr	GetTitlePtr
	jsr	AppendString

	lda	#0
	sta.w	lineBuffer,y

	plp
	rts


; Writes the two-digit, zero-padded decimal representation of (a+1),
; followed by ". ", into lineBuffer starting at offset 0.
; in: a = song index (0-based, 0..98)
; out: y = write cursor position after ". " (4)
FormatTwoDigitNumber:
	php
	sep	#A_8BIT
	rep	#XY_8BIT

	clc
	adc	#1
	stz	digitTens
	@tensLoop:
	cmp	#10
	bcc	@tensDone
	sec
	sbc	#10
	inc	digitTens
	bra	@tensLoop
	@tensDone:
	pha				; ones digit value

	lda	digitTens
	clc
	adc	#'0'
	ldy	#0
	sta.w	lineBuffer,y

	pla
	clc
	adc	#'0'
	ldy	#1
	sta.w	lineBuffer,y

	lda	#'.'
	ldy	#2
	sta.w	lineBuffer,y
	lda	#' '
	ldy	#3
	sta.w	lineBuffer,y

	ldy	#4

	plp
	rts


; Appends the null-terminated string pointed to by X to lineBuffer at
; offset Y (Y is advanced past the copied characters, not past the
; terminator, so multiple calls can be chained). Requires A=8-bit,
; X/Y=16-bit on entry (not self-contained, since it's called many times
; in a row from routines that already hold that width).
AppendString:
	@loop:
	lda.w	$0000,x
	beq	@done
	sta.w	lineBuffer,y
	inx
	iny
	bra	@loop
	@done:
	rts




; Draws a null-terminated ASCII string into the off-screen tilemap buffer
; (see tilemapBuffer). Nothing reaches actual VRAM until the next
; CommitTilemapToVRAM call.
; in:  a = palette index (0-3)
;      x = pointer to string (bank 0)
;      y = destination VRAM word address (as if writing straight to VRAM;
;          internally converted to a buffer offset)
PutsSingle:
	php
	sep	#A_8BIT
	asl	a
	asl	a
	sta	putsColor

	rep	#(A_8BIT|XY_8BIT)
	tya
	sec
	sbc	#$2000			; word address relative to the map base
	asl	a			; -> byte offset into tilemapBuffer (2B/tile)
	tay
	sep	#A_8BIT

	@loop:
	lda.w	$0000,x
	beq	@done
	sec
	sbc	#32			; font tile 0 = ASCII space
	sta.w	tilemapBuffer,y
	lda	putsColor
	sta.w	tilemapBuffer+1,y
	inx
	rep	#A_8BIT
	tya
	clc
	adc	#2
	tay
	sep	#A_8BIT
	bra	@loop
	@done:
	plp
	rts


 ; Needed to satisfy interrupt definition in "Header.inc".
 VBlank:
 	rti


palette:
.dw $0000,$8010,$801F,$8018,0,0,0,0,0,0,0,0,0,0,0,0 ; red
.dw $0000,$82B5,$83FF,$ABFF,0,0,0,0,0,0,0,0,0,0,0,0 ; yellow
.dw $0000,$C210,$FFFF,$FEFE,0,0,0,0,0,0,0,0,0,0,0,0 ; white

playingHeaderText:
 .db "Now Playing:", 0
playingHintText:
 .db "B: back to selection", 0

aboutMenuText:
 .db "About", 0
aboutHeaderText:
 .db "About", 0
aboutHintText:
 .db "B: back to selection", 0


font:
  .incbin "font.chr"
font_end:

; Per-song text (titles/authors/games), the pointer tables used to draw
; the song list, and the configurable header text. Included here (inside
; "MainCode") so it simply shares whatever free space is left in ROM
; bank 0.
.include "album_text.inc"

 .ends


; Per-song SPC/DSP register data and the 64KB music RAM dumps.
.include "album_data.asm"
