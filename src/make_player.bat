python build_album.py music\album_patched.txt .
if errorlevel 1 goto :eof

wla-65816 -o test.o spcplay.asm
wlalink -b test.link spcplay.smc
del test.o
