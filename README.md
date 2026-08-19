# SPC Player Multi-song Album Builder
Mic's SPC Player for SNES expanded with multi-song support &amp; custom album builder

This new package allows you to drop multiple different SPC files into a single ROM and switch between them, creating your own custom SNES album.
The two new files are:

* spc_patcher.py - patches known SPC files, standardizing exit calls
* builder_gui.py - a simple GUI that allows you to easily build an album from scratch

I tested the code on 30+ files, mostly from Nintendo games and those created by SNESMOD.
Many other SPC files (using custom replay routines) may not work or cause errors, and SPC Patcher should be extended for these cases.

Builder GUI reads tags from SPC files, but you can edit them by double clicking each info part (title, author, game name).
By clicking BUILD it saves all patched files in `music` folder as song1.spc, song2.spc etc. keeping your original SPCs untouched.
It also builds two files: album.txt and album_patched.txt.
The first one (album.txt) contains original file paths and is helpful when you want to change some things later, the second (album_patched.txt) is used to build the ROM.
Builder requires Python 3.14 and PyQt6 installed.

Output ROM file name is spcplay.smc

All player/patcher code written and debugged by Claude Sonnet 5 AI model. 
Builder GUI written by Gemini Pro AI model.

See README_full for detailed info and explanations written by LLM itself.

==== CHANGELOG

2026.08: v1.0 Initial release
