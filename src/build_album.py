#!/usr/bin/env python3
"""
build_album.py

Reads an album.txt playlist (and about.txt, if present) and generates the
WLA-DX source files needed to build a multi-song SPCPlay ROM:

  - album_config.inc   Defines that MUST be known before "snes.inc" is
                        included (song count, number of ROM banks, ROM size
                        header byte).
  - album_data.asm      Per-song SPC/DSP register blocks and the two 32KB
                        music RAM dumps per song.
  - album_text.inc      The menu header text, per-song title/author/game
                        strings and their pointer tables, and the ABOUT
                        screen's text (read from about.txt in the project
                        root - see read_about_lines()).

album.txt format
-----------------
Entries are separated by one or more blank lines. Each entry has up to
4 lines:

    path/to/song.spc
    Song title
    Author name
    Game name            <- optional; leave empty (or omit) for "no game"

Lines starting with ';' or '#' are treated as comments and ignored.

Usage
-----
    python3 tools/build_album.py [album.txt] [output_dir]

Defaults: album.txt = music/album.txt, output_dir = . (project root)
"""

import sys
import os
import math

SPC_REG_OFFSET = 0x00025   # 8 bytes: PC, A, X, Y, PSW, SP (raw copy, same as original)
SPC_REG_LEN = 0x0008
SPC_DSP_OFFSET = 0x10100   # 128 bytes of DSP register values
SPC_DSP_LEN = 0x0080
SPC_RAM_OFFSET = 0x00100   # 64KB SPC RAM dump
SPC_RAM_LEN = 0x10000

CODE_BANKS = 2              # bank 0 = program code, bank 1 = SPC reg/DSP data
BANKS_PER_SONG = 2          # each song's 64KB RAM dump = two 32KB ROM banks
SPC_DATA_BANK = 2           # must match SPC_DATA_BANK in loadspc.asm

MAX_LOROM_DATA_BANKS = 123  # banks 2..124 -> conservative practical LoROM ceiling

ABOUT_PATH = "about.txt"    # read from the project root (cwd), like make_player.bat
ABOUT_MAX_LINES = 22        # keeps the last line safely above the "B: back" hint
ABOUT_MAX_WIDTH = 30        # tilemap is 32 cols wide; leaves a 1-col margin each side


def read_about_lines(path=ABOUT_PATH):
    """Reads about.txt from the project root and returns a list of display
    lines (already truncated to fit the screen). Missing file -> a single
    placeholder line rather than a hard error, since About is optional
    polish, not something that should break the build."""
    if not os.path.isfile(path):
        return ["(about.txt not found)"]
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw_lines = f.read().splitlines()
    lines = [ln.rstrip()[:ABOUT_MAX_WIDTH] for ln in raw_lines[:ABOUT_MAX_LINES]]
    if not lines:
        lines = [""]
    return lines


def parse_album(path):
    with open(path, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()

    # --- Optional custom header/title line ------------------------------
    # A line anywhere in the file of the form "title: Some text" sets the
    # text shown at the top of the on-screen menu. Only the first such line
    # is used; it is removed from further (song-entry) parsing.
    header_text = "SPC Player"
    remaining_lines = []
    title_seen = False
    for line in raw_lines:
        stripped = line.strip()
        if not title_seen and stripped.lower().startswith("title:"):
            header_text = stripped[len("title:"):].strip()
            title_seen = True
        else:
            remaining_lines.append(line)

    lines = []
    for line in remaining_lines:
        stripped = line.rstrip("\n").rstrip("\r")
        if stripped.strip().startswith(";") or stripped.strip().startswith("#"):
            continue
        lines.append(stripped)

    # Split into blocks separated by one or more blank lines.
    blocks = []
    current = []
    for line in lines:
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)

    songs = []
    for block in blocks:
        if len(block) < 3:
            raise ValueError(
                "Album entry needs at least a path, a title and an author. "
                "Offending entry: %r" % block
            )
        path_ = block[0].strip()
        title = block[1].strip()
        author = block[2].strip()
        game = block[3].strip() if len(block) >= 4 else ""
        songs.append({"path": path_, "title": title, "author": author, "game": game})
    return songs, header_text


def asm_string_literal(text):
    """Very small escaper: only handles double quotes, since the WLA-DX
    .db "..." syntax has no escape sequences of its own."""
    if '"' in text:
        raise ValueError("Song text cannot contain a double-quote character: %r" % text)
    return text


def rom_size_code(total_bytes):
    """Standard SNES header ROM size byte: 2^n KB where n = code + 10."""
    size = 1
    while size < total_bytes:
        size *= 2
    code = int(math.log2(size)) - 10
    return max(code, 0x08)


def main():
    album_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join("music", "album.txt")
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "."

    songs, header_text = parse_album(album_path)
    num_songs = len(songs)
    if num_songs == 0:
        raise SystemExit("album.txt contains no songs.")

    if num_songs > MAX_LOROM_DATA_BANKS // BANKS_PER_SONG:
        print(
            "WARNING: %d songs need %d ROM banks; plain LoROM mapping only "
            "has room for roughly %d data banks (~%d songs). The build may "
            "fail or the ROM may not fit on real hardware."
            % (
                num_songs,
                num_songs * BANKS_PER_SONG,
                MAX_LOROM_DATA_BANKS,
                MAX_LOROM_DATA_BANKS // BANKS_PER_SONG,
            ),
            file=sys.stderr,
        )

    num_rombanks = CODE_BANKS + BANKS_PER_SONG * num_songs
    total_bytes = num_rombanks * 32768
    size_code = rom_size_code(total_bytes)

    config_path = os.path.join(out_dir, "album_config.inc")
    data_path = os.path.join(out_dir, "album_data.asm")
    text_path = os.path.join(out_dir, "album_text.inc")

    with open(config_path, "w", encoding="utf-8") as f:
        f.write("; Auto-generated by tools/build_album.py - do not edit by hand.\n")
        f.write("; Regenerate this file whenever music/album.txt changes.\n\n")
        f.write(".define NUM_SONGS %d\n" % num_songs)
        f.write(".define NUM_ROMBANKS %d\n" % num_rombanks)
        f.write(".define ROMSIZE_CODE $%02X\n" % size_code)

    with open(data_path, "w", encoding="utf-8") as f:
        f.write("; Auto-generated by tools/build_album.py - do not edit by hand.\n")
        f.write("; Regenerate this file whenever music/album.txt changes.\n\n")

        # --- SPC register + DSP register data, packed into bank 1 -------
        f.write("; Per-song SPC/DSP register snapshots (see loadspc.asm for\n")
        f.write("; the indexing scheme: spcSongNr*8 for the small registers,\n")
        f.write("; spcSongNr*128 for the DSP registers).\n")
        f.write(".bank 1 slot 0\n")
        for i, song in enumerate(songs):
            reg_org = i * SPC_REG_LEN
            dsp_org = 0x4000 + i * SPC_DSP_LEN
            f.write(".org $%04X\n" % reg_org)
            f.write('.incbin "%s" skip $%05X read $%04X\n' % (song["path"], SPC_REG_OFFSET, SPC_REG_LEN))
            f.write(".org $%04X\n" % dsp_org)
            f.write('.incbin "%s" skip $%05X read $%04X\n' % (song["path"], SPC_DSP_OFFSET, SPC_DSP_LEN))
        f.write("\n")

        # --- 64KB SPC RAM dumps, two 32KB banks per song -----------------
        f.write("; Full 64KB SPC RAM dump per song, split across two 32KB banks.\n")
        for i, song in enumerate(songs):
            bank_low = SPC_DATA_BANK + i * BANKS_PER_SONG
            bank_high = bank_low + 1
            f.write(".bank %d\n" % bank_low)
            f.write('.section "musicData%dLow"\n' % i)
            f.write('.incbin "%s" skip $%05X read $8000\n' % (song["path"], SPC_RAM_OFFSET))
            f.write(".ends\n")
            f.write(".bank %d\n" % bank_high)
            f.write('.section "musicData%dHigh"\n' % i)
            f.write('.incbin "%s" skip $%05X read $8000\n' % (song["path"], SPC_RAM_OFFSET + 0x8000))
            f.write(".ends\n")
        f.write("\n")

    # --- Text tables (title/author/game), used by the song list ---------
    # This is included *inside* spcplay.asm's "MainCode" section (before its
    # .ends), rather than living in its own bank/section, so it simply
    # shares whatever free space MainCode already has in ROM bank 0.
    with open(text_path, "w", encoding="utf-8") as f:
        f.write("; Auto-generated by tools/build_album.py - do not edit by hand.\n")
        f.write("; Regenerate this file whenever music/album.txt changes.\n")
        f.write("; Included from inside spcplay.asm's \"MainCode\" section.\n\n")

        f.write("albumTitleText:\n .db \"%s\",0\n\n" % asm_string_literal(header_text))

        for i, song in enumerate(songs):
            f.write("songTitle%d:\n .db \"%s\",0\n" % (i, asm_string_literal(song["title"])))
            f.write("songAuthor%d:\n .db \"%s\",0\n" % (i, asm_string_literal(song["author"])))
            f.write("songGame%d:\n .db \"%s\",0\n" % (i, asm_string_literal(song["game"])))
            f.write("\n")

        f.write("songTitleTable:\n .dw ")
        f.write(", ".join("songTitle%d" % i for i in range(num_songs)))
        f.write("\n")
        f.write("songAuthorTable:\n .dw ")
        f.write(", ".join("songAuthor%d" % i for i in range(num_songs)))
        f.write("\n")
        f.write("songGameTable:\n .dw ")
        f.write(", ".join("songGame%d" % i for i in range(num_songs)))
        f.write("\n\n")

        # --- ABOUT screen text (content of about.txt in the project root) ---
        about_lines = read_about_lines()
        f.write("; ABOUT screen content, read from '%s' in the project root.\n" % ABOUT_PATH)
        f.write(".define NUM_ABOUT_LINES %d\n" % len(about_lines))
        for i, line in enumerate(about_lines):
            if line:
                f.write("aboutLine%d:\n .db \"%s\",0\n" % (i, asm_string_literal(line)))
            else:
                f.write("aboutLine%d:\n .db 0\n" % i)  # blank line: bare terminator
        f.write("aboutLineTable:\n .dw ")
        f.write(", ".join("aboutLine%d" % i for i in range(len(about_lines))))
        f.write("\n")

    print("Wrote %s, %s and %s" % (config_path, data_path, text_path))
    print("%d song(s), %d ROM banks (%d KB), ROMSIZE header byte $%02X"
          % (num_songs, num_rombanks, total_bytes // 1024, size_code))


if __name__ == "__main__":
    main()
