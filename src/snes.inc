.include "header.asm"
.include "Snes_Init.asm"
.include "snes_io.inc"
.include "snes_ppu.inc"

 .define XY_8BIT $10
 .define A_8BIT  $20
 
; These definitions are needed to satisfy some lines in "Snes_Init.asm".
 .define BG1MoveH $7E1A25
 .define BG1MoveV $7E1A26
 .define BG2MoveH $7E1A27
 .define BG2MoveV $7E1A28
 .define BG3MoveH $7E1A29
 .define BG3MoveV $7E1A2A
 