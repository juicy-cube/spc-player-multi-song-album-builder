.MEMORYMAP                      ; Begin describing the system architecture.
  SLOTSIZE $8000                ; The slot is $8000 bytes in size. More details on slots later.
  DEFAULTSLOT 0                 ; There's only 1 slot in SNES, there are more in other consoles.
  SLOT 0 $8000                  ; Defines Slot 0's starting address.
.ENDME          ; End MemoryMap definition

.ROMBANKSIZE $8000              ; Every ROM bank is 32 KBytes in size
.ROMBANKS NUM_ROMBANKS           ; set from album_config.inc: 2 fixed banks
                                  ; (code + SPC/DSP reg data) + 2 banks per song

.SNESHEADER
  ID "SNES"                     ; 1-4 letter string, just leave it as "SNES"
  
  NAME "SPC PLAYER           "  ; Program Title - can't be over 21 bytes,
  ;    "123456789012345678901"  ; use spaces for unused bytes of the name.

  SLOWROM
  LOROM

  CARTRIDGETYPE $00             ; $00 = ROM only, see WLA documentation for others
  ROMSIZE ROMSIZE_CODE          ; set from album_config.inc based on song count
  SRAMSIZE $00                  ; No SRAM         see WLA doc for more..
  COUNTRY $01                   ; $01 = U.S.  $00 = Japan, that's all I know
  LICENSEECODE $00              ; Just use $00
  VERSION $00                   ; $00 = 1.00, $01 = 1.01, etc.
.ENDSNES

.SNESNATIVEVECTOR               ; Define Native Mode interrupt vector table
  COP EmptyHandler
  BRK EmptyHandler
  ABORT EmptyHandler
  NMI VBlank
  IRQ EmptyHandler
.ENDNATIVEVECTOR

.SNESEMUVECTOR                  ; Define Emulation Mode interrupt vector table
  COP EmptyHandler
  ABORT EmptyHandler
  NMI EmptyHandler
  RESET Start                   ; where execution starts
  IRQBRK EmptyHandler
.ENDEMUVECTOR

.BANK 0 SLOT 0                  ; Defines the ROM bank and the slot it is inserted in memory.
.ORG 0                          ; .ORG 0 is really $8000, because the slot starts at $8000
.SECTION "EmptyVectors" SEMIFREE

EmptyHandler:
       rti

.ENDS

.EMPTYFILL $00                  ; fill unused areas with $00, opcode for BRK.  
                                ; BRK will crash the snes if executed.
