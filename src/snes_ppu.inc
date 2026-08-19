
.macro WaitVsync
	-:
        bit     REG_RDNMI
        bmi 	-
	-:
        bit     REG_RDNMI
        bpl 	-
.endm
 

;macro for loading palette data into the CGRAM
;only use if SIZE is less than 256 bytes
;syntax SetPalette LABEL CGRAM_ADDRESS SIZE
.macro SetPalette
pha
php

rep	#$20		; 16bit A
lda	#<\3
sta	$4305		; # of bytes to be copied
lda	#>\3
sta	$4306		; # of bytes to be copied
lda	#\1		; offset of data into 4302, 4303
sta	$4302
sep	#$20		; 8bit A

lda	#:\1		; bank address of data in memory(ROM)
sta	$4304	
lda	#\2
sta	REG_CGRAM_ADDR	; address of CGRAM to start copying graphics to

stz	$4300		; 0= 1 byte increment (not a word!)
lda	#<REG_CGRAM_DATAW
sta	$4301		; destination 21xx   this is 2122 (CGRAM Gate)

lda	#$01		; turn on bit 1 (corresponds to channel 0) of DMA channel reg.
sta	$420b		;   to enable transfer

plp
pla
.endm



;macro for loading graphics data into the VRAM
;syntax LoadVRAM LABEL  VRAM_ADDRESS  SIZE
.macro LoadVRAM
pha
php

sep	#$20
lda	#<(\2 / 2)
sta	REG_VRAM_ADDR_L	; address for VRAM write(or read)
lda	#>(\2 / 2)
sta	REG_VRAM_ADDR_H	; address for VRAM write(or read)
lda	#<\3
sta	$4305		; number of bytes to be copied
lda	#>\3
sta	$4306		; number of bytes to be copied
rep	#$20
lda	#\1		
sta	$4302		; data offset in memory
sep	#$20		; 8bit A

lda	#:\1		; bank address of data in memory
sta	$4304	
lda	#$80  
sta	REG_VRAM_INC	; VRAM address increment value designation
lda	#$01
sta	$4300		; 1= word increment
lda	#<REG_VRAM_DATAW1
sta	$4301		; 2118 is the VRAM gate

lda	#$01		; turn on bit 1 (channel 0) of DMA
sta	$420b
 
plp
pla
.endm



