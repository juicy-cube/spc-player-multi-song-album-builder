# SPCPlay – album edition

A SNES ROM that plays back a list of `.spc` music rips, picked from an on-screen menu,
built on top of the original single-song SPCPlay (/Mic, 2010). It loads the song list from
`album.txt`, shows a scrollable numbered menu with icons and colored text, an ABOUT screen,
and can actually **stop** a currently-playing song and return to the menu — which the
original single-song `.spc` playback protocol has no built-in way to do, so a companion
tool (`spc_patcher.py`) patches each `.spc` file to add that ability before it's built into
the ROM.

## Features

- Song list read from `music/album_patched.txt` (path, title, author, optional game per
  song), with a configurable on-screen header (`title: ...`).
- Scrollable, numbered song list (`VISIBLE_SONGS` = 10 entries on screen at once):
  ```
  01. Author
      Title
  02. Author 2
      Title 2
  ```
  Normal entries are white, the highlighted/selected entry is yellow, headers and hints
  are red — each a single solid color (no shading tricks needed to read them).
- An unnumbered **ABOUT** entry at the end of the list (see below).
- Up/Down **wraps around**: pressing Down on the last entry (ABOUT) jumps back to the
  first song; pressing Up on the first song jumps to the last entry (ABOUT).
- A/Start plays the highlighted song. The "Now Playing" screen shows Author / Title / Game
  (if given), each with a small icon to its left, and a "B: back to selection" hint.
- **B actually stops playback** and returns to the menu, instead of just switching screens
  while the previous song keeps running underneath (see "Stopping playback" below) — you
  can freely start another song right after.
- Flicker-free redraws: every screen is built in an off-screen buffer in WRAM and pushed to
  VRAM in a single DMA transfer per frame, timed to avoid colliding with the SNES's
  auto-joypad-read.
- The ROM's bank count and header ROM-size byte are sized automatically to the number of
  songs in the album.

### ABOUT screen

Selecting the last, unnumbered "ABOUT" entry shows the contents of `about.txt` (read from
the project root at build time — see `read_about_lines()` in `build_album.py`), one line
per row, with a "B: back to selection" hint at the bottom. B returns to the song list.
Edit `about.txt` and rebuild to change the text. Lines are truncated to 30 characters and
the file is capped at 22 lines (`ABOUT_MAX_WIDTH` / `ABOUT_MAX_LINES` in `build_album.py`)
to keep everything safely on screen; there's no word-wrapping, so keep lines short.

### Icons on the "Now Playing" screen

The font (`font.chr`) has three extra glyphs added past the normal ASCII range, at tile
indices `$60` (author icon), `$62` (title icon), and `$63` (game icon). `DrawPlayingScreen`
draws each one directly into the tilemap buffer (bypassing the normal ASCII `char-32`
text path — see "Known limitations" below) in yellow, one column to the left of the
corresponding line, with the text itself starting two columns further in.

### Stopping playback

The original single-song SPCPlay had no way to interrupt a playing `.spc` short of
resetting the console: once its main loop starts driving the sound engine, there is no
in-band way for the SNES to make it stop or hand control back. Picking a *different* song
from the menu still works (`LoadSPC` just force-uploads a fresh 64KB SPC RAM image over
whatever was there), but there was no clean way to stop and go back to the menu with
nothing playing.

`spc_patcher.py` closes that gap by patching each `.spc` file, before it's built into the
ROM, so that its own driver code will voluntarily stop and hand control back when the SNES
asks it to. See "The SPC exit patcher" below for how.

On the SNES side, `PlayingHandleInput` (in `spcplay.asm`) implements the other half: on B,
it writes `$FF` to the SPC communication port (`$2140`), then blocks on `REG_APUI00` until
the SPC signals back `$AA` (meaning it muted the DSP and parked itself waiting for a new
upload), then switches to the menu. Selecting a new song from there calls `LoadSPC` exactly
as before — the exit patch's job is only to get the SPC back to that "ready for a new
upload" state cleanly, not to change how loading a song works.

## Building manually

1. Drop your `.spc` files into `_music_org/` and run `spc_patcher.py` (see "The SPC exit
   patcher" below) — it writes the patched copies into `music/`.
2. Fill in `music/album_patched.txt` (this is the file `make_player.bat` actually points
   `build_album.py` at):

   ```
   title: SPC Player - select a song

   music/song1.spc
   Song 1
   Author 1
   Game 1

   music/song2.spc
   Song 2
   Author 2
   Game 2
   ```

   `title: ...` is optional and sets the menu header text (defaults to "SPC Player"). The
   game-name line is optional too — leave it empty (or omit it) for "no game", and that line
   (and its icon) just won't appear on the "Now Playing" screen. Lines starting with `;` or
   `#` are comments.
3. Optionally edit `about.txt` in the project root to change the ABOUT screen's text.
4. Run `make_player.bat` (needs Python 3 and a reasonably recent `wla-65816`/`wlalink` — the
   current code was built and verified against WLA-DX 10.7). It regenerates
   `album_config.inc`, `album_text.inc`, and `album_data.asm` from `album_patched.txt` (and
   `about.txt`), then assembles and links `spcplay.smc`.

   Those three generated files are just that — generated. Don't hand-edit them; they're
   rebuilt from scratch on every build.

## How it works (architecture)

- `spc_patcher.py` — patches each `.spc` file so its driver can be told to stop (see
  below). Run once per song, before the SNES-side build.
- `build_album.py` parses `album_patched.txt` and `about.txt`, and generates:
  - `album_config.inc` — `NUM_SONGS`, `NUM_ROMBANKS`, `ROMSIZE_CODE` (needed by `header.asm`,
    so this file must be included **before** `snes.inc`),
  - `album_data.asm` — per song: the 8-byte SPC register snapshot and 128-byte DSP register
    snapshot (bank 1, indexed by `spcSongNr*8` / `spcSongNr*128`, matching the scheme
    `loadspc.asm` uses), plus the full 64KB SPC RAM dump split across two 32KB ROM banks
    (banks `2+2*i`, `3+2*i` for song `i` — again, exactly what `loadspc.asm` expects),
  - `album_text.inc` — the header text, titles/authors/games and their pointer tables, and
    the ABOUT screen's line table, all included inside `spcplay.asm`'s `MainCode` section.
- `loadspc.asm` — the SPC upload/boot routine. Accepts a song number (`spcSongNr`) and
  computes bank/register addresses from it, so it already supported multiple songs in
  principle; DSP register initialization now also holds the DSP fully muted (`FLG = $60`,
  MUTE + echo-write-disable) for the whole duration of the upload/setup sequence instead of
  only disabling echo writes, so nothing audible leaks out while volumes and other
  registers are still being zeroed (see the comment at the top of `LoadSPC`). The song's own
  captured FLG value (normally *not* muted) is restored right at the end, once everything
  else is ready, by the small init routine (`spc700_init_code`) that runs on the SPC itself.
- `header.asm` — `.ROMBANKS` and `ROMSIZE` come from `album_config.inc` instead of being
  hardcoded.
- `spcplay.asm` — the menu screen (`DrawMenu`, with wraparound + the ABOUT entry), the
  playing screen (`DrawPlayingScreen`, with icons), the ABOUT screen (`DrawAboutScreen`),
  text-building helpers (`BuildAuthorLine`, `BuildTitleLine`, `FormatTwoDigitNumber`), pad
  handling (`ReadJoypad`, `MenuHandleInput`, `PlayingHandleInput`, `AboutHandleInput`), and
  DMA-based rendering (`tilemapBuffer`, `CommitTilemapToVRAM`).

### The SPC exit patcher (`spc_patcher.py`)

Every `.spc` file is a snapshot of a game's actual sound driver, not a generic player — so
there's no single, universal way to tell an arbitrary driver "stop now". The patcher works
by finding every place in the driver's own code where it reads the SNES communication port
($F4) and rewriting each one so that if the SNES ever writes the value `$FF` there, the
driver jumps to a small added routine that mutes the DSP, signals back to the SNES, and
parks itself waiting for a new song upload — otherwise, the patched code behaves exactly as
the original instruction would have, and the song plays completely normally. Concretely, it
recognizes and patches:

- direct reads of `$F4` into a register (`MOV A/X/Y,$F4`, plus the 3-byte absolute form
  `MOV A,!$00F4`),
- direct compares against `$F4` (`CMP A,$F4`),
- the `d,#i` family checking `$F4` against a constant with no register at all (e.g.
  `CMP $F4,#$80` — real drivers do use this to poll the port directly),
- `CBNE $F4,r` (compare-and-branch against the port in one instruction) — but only where it
  isn't immediately preceded by an already-patched read, since in that case an exit is
  already caught upstream and the `CBNE` keeps working unmodified on its own.

Each patched site is redirected with `PCALL` (a 2-byte call fixed to page `$FF00-$FFFF`) to
a small trampoline placed in that same page. A trampoline reads the port **once**, checks
it against `$FF`, and — if it doesn't match — replays the original instruction's effect
(never re-reading hardware a second time, since the SNES can write a new value at any time)
before returning exactly as if it had never been patched. Register(s) the original
instruction didn't touch are preserved across the call. If it matches `$FF`, execution
jumps to a shared exit routine that mutes the DSP (`FLG = $60`: MUTE + disable echo write —
*not* `$20`, which only stops new echo writes and leaves whatever's already in the echo
buffer looping audibly), zeroes the main volume, key-offs every voice, signals `$AA` back
on the port, and hands control back to the SPC's IPL boot ROM to wait for the next upload.

A few things about that page ($FF00–$FFFF) turned out to matter a lot in practice, since
it's shared with things well outside the patcher's control:

- Real drivers can use `TCALL 0-3` for their own subroutine calls, so the patcher can't
  repurpose the hardware TCALL vector table there — hence `PCALL`, which calls a fixed
  address directly with no shared vector table in the way.
- Some drivers write to fixed addresses in that page for their own, unrelated reasons.
  `find_high_ram_writes()` scans the driver code up front for any such writes and the
  trampoline allocator (`alloc_trampoline`) skips over them automatically.
- The DSP's **echo buffer** is hardware, driven purely by `ESA`/`EDL` (the echo start
  address and delay length), and gets written continuously by the DSP itself — completely
  independent of the CPU, so nothing in the driver's code would ever show it happening. If
  a song's echo buffer overlaps the trampoline page, `patch_spc()` shortens `EDL` by just
  enough to free that page (a small, usually inaudible reduction in echo length) rather
  than let the DSP silently overwrite the trampolines while the song plays.
- Trampolines that need to jump to the shared exit routine use `BNE +3` / `JMP !exit_addr`
  instead of a plain `BEQ`, since a simple relative branch can't reliably reach across the
  whole 256-byte page.
- The `CBNE` trampoline's "keep playing" path has to explicitly discard the 2-byte return
  address that `PCALL` pushed before it jumps back into the driver's code (rather than
  `RET`-ing), since it isn't actually returning to its caller — it's redirecting to a
  completely different point in the driver, and leaving that return address on the stack
  would otherwise leak 2 bytes on every single note the song plays.

Run it as `python spc_patcher.py` from its own folder: it reads every `.spc` in
`_music_org/` and writes the patched copies to `music/`, printing how many read sites it
patched (and, for songs with echo enabled, whether it had to shorten `EDL`). Songs it can't
find any `$F4` read at all in are skipped rather than silently shipped unpatched.

## Known limitations

- **Song text is ASCII-only.** `PutsSingle` computes the tile index as `char - 32`, so
  titles/authors/games/menu text are limited to standard ASCII (32–126). The three "Now
  Playing" icons work around this by writing their tile indices directly into the tilemap
  buffer instead of going through `PutsSingle`, so they aren't limited by this — but general
  text still is.
- **Song count ceiling.** Each song costs 2 ROM banks (64KB of audio) plus a small slice of
  bank 1 (registers). Plain LoROM mapping tops out around ~60 songs (~4MB ROM) in practice;
  `build_album.py` prints a warning if you're approaching that. List numbering is two digits
  (01–99); past 99 songs the numbering will look wrong (cosmetic only).
- **No text wrapping.** Titles/authors/games longer than roughly 27–31 characters (tilemap
  width minus indent) will simply run off/overlap in VRAM. The ABOUT screen avoids this by
  truncating lines at build time (see `ABOUT_MAX_WIDTH`); song list/Now Playing text isn't
  truncated.
- **`Snes_Init.asm` casing.** `snes.inc` includes it as `"Snes_Init.asm"` (capitalized),
  while the file on disk is `snes_init.asm`. Harmless on Windows (case-insensitive
  filesystem); on Linux/macOS you'll need to either rename the file or fix the include's
  casing.
- **The exit patch depends on finding the driver's own reads of the communication port.**
  For a driver whose code genuinely never reads `$F4` at all (playing entirely from
  internal sequence data with no live SNES communication), `spc_patcher.py` has nothing to
  patch and will skip the file — B on such a song wouldn't be able to stop it. This hasn't
  been observed in practice on the songs this was built/tested against, but it's a
  theoretical gap in the approach worth knowing about.
- **Trampoline space is finite.** All patched sites share a single 256-byte page
  (`$FF00-$FFFF`) for their trampolines. If a driver needs more space than fits after
  working around its own High RAM usage and/or its echo buffer, `spc_patcher.py` prints a
  warning and leaves that specific site unpatched rather than failing the whole file.
