# SPC Player Multi-song Album Builder
Mic's SPC Player for SNES expanded with multi-song support &amp; custom album builder

This new package allows you to drop multiple different SPC files into a single ROM and switch between them, creating your own custom SNES album.
The two new files are:

* spc_patcher.py - patches known SPC files, standardizing exit calls
* builder_gui.py - a simple GUI that allows you to easily build an album from scratch

I tested the code on 30+ files, mostly from Nintendo games and those created by SNESMOD.
Many other SPC files (using custom replay routines) may not work or cause errors, and SPC Patcher should be extended for these cases.

==== CHANGELOG

XX.2026: Initial release
