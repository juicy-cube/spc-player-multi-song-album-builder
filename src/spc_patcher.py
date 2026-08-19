# -*- coding: utf-8 -*-
import os
import sys
import glob

# Lengths (in bytes) of all 256 SPC700 opcodes - needed to walk linearly
# through the driver code, correctly aligned to instruction boundaries
# (used e.g. when scanning for writes that would collide with our High RAM -
# see find_high_ram_writes below).
OPCODE_LENGTHS = [
    1,1,2,3,2,3,1,2,2,3,3,2,3,1,3,1,
    2,1,2,3,2,3,3,2,3,1,2,2,1,1,3,3,
    1,1,2,3,2,3,1,2,2,3,3,2,3,1,3,2,
    2,1,2,3,2,3,3,2,3,1,2,2,1,1,2,3,
    1,1,2,3,2,3,1,2,2,3,3,2,3,1,3,2,
    2,1,2,3,2,3,3,2,3,1,2,2,1,1,3,3,
    1,1,2,3,2,3,1,2,2,3,3,2,3,1,3,1,
    2,1,2,3,2,3,3,2,3,1,2,2,1,1,2,1,
    1,1,2,3,2,3,1,2,2,3,3,2,3,2,1,3,
    2,1,2,3,2,3,3,2,3,1,2,2,1,1,1,1,
    1,1,2,3,2,3,1,2,2,3,3,2,3,2,1,1,
    2,1,2,3,2,3,3,2,3,1,2,2,1,1,1,1,
    1,1,2,3,2,3,1,2,2,3,3,2,3,2,1,1,
    2,1,2,3,2,3,3,2,2,2,2,2,1,1,3,1,
    1,1,2,3,2,3,1,2,2,3,3,2,3,1,1,1,
    2,1,2,3,2,3,3,2,2,2,3,2,1,1,2,1,
]

# Opcodes that WRITE to an absolute address (3 bytes: opcode, lo, hi) -
# meaning they could land in the High RAM area reserved for trampolines
# (see TRAMPOLINE_REGION_START below, currently $FF00-$FFFF) with nothing
# to do with port $F4. Covers the usual MOV !a,reg forms plus the
# read-modify-write instructions on !a (those end in a write too).
ABS_WRITE_OPCODES = {
    0xC5,  # MOV !a, A
    0xC9,  # MOV !a, X
    0xCC,  # MOV !a, Y
    0xD5,  # MOV !a+X, A
    0xD6,  # MOV !a+Y, A
    0x0C,  # ASL !a
    0x2C,  # ROL !a
    0x4C,  # LSR !a
    0x6C,  # ROR !a
    0x8C,  # DEC !a
    0xAC,  # INC !a
    0x0E,  # TSET1 !a
    0x4E,  # TCLR1 !a
}


def find_high_ram_writes(ram, lo=0x0200, hi=0x3FFF, region_start=0xFF00):
    """Walks linearly (aligned to instruction boundaries) through the driver
    code and returns the set of addresses in the trampoline area ($FF00-
    $FFFF by default) that the ORIGINAL, unpatched code writes to - those
    addresses have to be avoided when placing trampolines, since it's been
    observed empirically (full SPC700 simulation) that some drivers use
    High RAM for their own purposes, completely unrelated to port $F4 - and
    overwriting such an address means the driver itself, as part of its
    normal operation, writes back over a chunk of our code."""
    reserved = set()
    addr = lo
    while addr < hi:
        op = ram[addr]
        length = OPCODE_LENGTHS[op]
        if op in ABS_WRITE_OPCODES and addr + 2 < len(ram):
            target = ram[addr+1] | (ram[addr+2] << 8)
            if region_start <= target <= 0xFFFF:
                reserved.add(target)
        addr += length
    return reserved

def patch_spc(filepath, output_dir="music"):
    with open(filepath, 'rb') as f:
        data = bytearray(f.read())

    if len(data) < 0x10100:
        print(f"[SKIP] {os.path.basename(filepath)} - Invalid file size.")
        return False

    ram = data[0x100:0x10100]

    # High RAM addresses the original driver code uses for its own purposes
    # (see find_high_ram_writes) - trampolines have to avoid them.
    reserved_high_ram = find_high_ram_writes(ram, region_start=0xFF00)

    # CRITICAL FIX: the DSP echo buffer. The DSP writes echo samples
    # DIRECTLY into SPC RAM, entirely autonomously (no CPU involvement) -
    # no CPU execution trace will ever show this, since it isn't caused by
    # any instruction. Buffer range: [ESA*$100, ESA*$100 + EDL*$800).
    # Observed empirically on real hardware (Mesen-S): for a song with
    # ESA=$A0, EDL=$0C the echo buffer is EXACTLY $A000-$FFFF - covering
    # the ENTIRE trampoline area ($FF00-$FFFF, see TRAMPOLINE_REGION_START
    # below)!
    # Effect: after tens/hundreds of frames of stable operation, the DSP,
    # as part of ordinary echo processing, was overwriting our trampolines
    # with "garbage" audio samples - manifesting as stuck/garbled audio
    # exactly as reported by the user, even though the patch code itself
    # was already correct at that point.
    #
    # Fix: each EDL unit is about 16ms of echo delay and exactly $800
    # (2048) bytes of buffer. If the echo buffer overlaps $FF00-$FFFF (the
    # whole area reserved for trampolines), we shrink EDL (writing the
    # modified value back into the DSP register block in the output file)
    # by just enough units to free that area - this shortens the echo time
    # by a fraction of a unit (usually inaudible next to a completely
    # broken sound).
    TRAMPOLINE_REGION_START = 0xFF00
    DSP_REG_OFFSET = 0x10100
    ESA_REG, EDL_REG = 0x6D, 0x7D
    esa = data[DSP_REG_OFFSET + ESA_REG]
    edl = data[DSP_REG_OFFSET + EDL_REG]
    echo_start = esa * 0x100
    echo_end = echo_start + edl * 0x800  # exclusive
    if edl > 0 and echo_end > TRAMPOLINE_REGION_START and echo_start <= 0xFFFF:
        # smallest EDL at which the buffer no longer reaches the trampoline area
        max_edl_free = max(0, (TRAMPOLINE_REGION_START - echo_start) // 0x800)
        new_edl = min(edl, max_edl_free)
        if new_edl < edl:
            freed_from = echo_start + new_edl * 0x800
            print(f"  [INFO] Echo buffer (ESA=${esa:02X} EDL=${edl:02X}) reaches "
                  f"${echo_end-1:04X}, overlapping the trampoline area "
                  f"${TRAMPOLINE_REGION_START:04X}-$FFFF. Reducing EDL from "
                  f"${edl:02X} to ${new_edl:02X} (frees ${freed_from:04X}-$FFFF), "
                  f"to avoid the echo samples continuously overwriting the trampolines.")
            data[DSP_REG_OFFSET + EDL_REG] = new_edl
            if new_edl == 0 and freed_from > TRAMPOLINE_REGION_START:
                print(f"  [WARNING] Even EDL=0 doesn't fully free "
                      f"${TRAMPOLINE_REGION_START:04X}-$FFFF (ESA=${esa:02X} "
                      f"is too high) - trampolines may still collide with echo.")

    # 1. DSP mute routine + $AA signal for the SNES + enable IPL ROM + jump to $FFC9
    #
    # CRITICAL FIX: DSP register FLG has bit 6 = MUTE (full mute of all
    # output, including echo) and bit 5 = "disable echo write" (blocks
    # ONLY new writes to the echo buffer - does NOT mute what's already
    # in there!). An earlier version wrote $20 to FLG (bit 5 only) -
    # freezing the echo buffer, but its STATIC content kept being read
    # back and mixed into the output on every loop of the buffer, giving
    # a "short looping fragment" effect after stopping (observed
    # empirically for a song with active echo; songs without echo didn't
    # have anything to loop, so they weren't affected). $60 sets BOTH
    # bits: immediate full mute (bit 6) + disable echo writes (bit 5,
    # recommended by the DSP docs when stopping, to avoid stray writes
    # corrupting memory).
    exit_routine = bytearray([
        0x8F, 0x6C, 0xF2,  # MOV $F2, #$6C (DSP FLG)
        0x8F, 0x60, 0xF3,  # MOV $F3, #$60 (MUTE [bit6] + disable echo write [bit5])
        0x8F, 0x0C, 0xF2,  # MOV $F2, #$0C (MVOL_L = 0)
        0x8F, 0x00, 0xF3,  # MOV $F3, #$00
        0x8F, 0x1C, 0xF2,  # MOV $F2, #$1C (MVOL_R = 0)
        0x8F, 0x00, 0xF3,  # MOV $F3, #$00
        0x8F, 0x5C, 0xF2,  # MOV $F2, #$5C (Key Off)
        0x8F, 0xFF, 0xF3,  # MOV $F3, #$FF
        0x8F, 0xAA, 0xF4,  # MOV $F4, #$AA (signal for the SNES releasing the @waitIPL loop)
        0x8F, 0x80, 0xF1,  # MOV $F1, #$80 (enable IPL ROM)
        0x5F, 0xC9, 0xFF   # JMP $FFC9
    ])

    # 2. Trampolines in the safe High RAM area ($FF00-$FFFF, see
    #    TRAMPOLINE_REGION_START). exit_routine and SCRATCH_ADDR are
    #    allocated dynamically in this same area (see alloc_trampoline
    #    below), not at hardcoded addresses.
    #
    # IMPORTANT - architecture change vs. earlier versions: this used to
    # use TCALL 0-3 (replacing a port read with "TCALL n, NOP" and
    # taking over the hardware TCALL vector table at $FFD8-$FFDF). That
    # turned out to be a bad assumption: real, commercial SNES sound
    # drivers VERY OFTEN use TCALL 0-3 themselves as their own compact
    # subroutine-call mechanism. We use PCALL instead of TCALL - also
    # exactly 2 bytes, but it calls the fixed address $FF00+u DIRECTLY,
    # without going through any shared vector table.
    #
    # CRITICAL FIX (this patch): every trampoline below reads PORT $F4
    # EXACTLY ONCE and buffers the result instead of reading a second
    # time when "replaying" the original instruction. An earlier version
    # read $F4 TWICE (once to test for $FF, again to replay the original
    # operation) - harmless on a static simulation, but on real SNES
    # hardware the SNES writes to that port actively and asynchronously
    # at any time. If the value changed BETWEEN our two reads, the driver
    # got inconsistent data and effectively "dropped" a real game command
    # (e.g. a note) - observed as stuck/garbled audio right after
    # playback starts. Registers the original instruction did NOT touch
    # (e.g. A during "MOV X,$F4") are now also correctly preserved
    # (PUSH/POP), so as not to corrupt state the rest of the driver code
    # depends on.
    #
    # SECOND CRITICAL FIX (this patch): some drivers write to their own
    # addresses in High RAM as part of normal, unrelated work (observed
    # empirically in song2.spc: explicit "MOV !$FFDE,A" / "MOV !$FFDF,A"
    # in the original code). Trampolines are now placed skipping such
    # addresses (see reserved_high_ram / find_high_ram_writes above) -
    # otherwise the driver itself, as part of its own logic, would
    # overwrite part of our code while running, which showed up as
    # stuck/looping audio.
    #
    # All trampolines (fixed and dynamic) are allocated IN SEQUENCE by one
    # shared function - zero manual address arithmetic, zero risk of
    # overlap if any piece's length changes, and with automatic skipping
    # of addresses in reserved_high_ram. The area was widened to
    # $FF00-$FFFF (256 bytes instead of the original 128 under
    # $FF80-$FFFF) - 128 bytes empirically turned out to be too little
    # for files with many "d,#i"/CBNE occurrences at once.
    next_free_addr = [TRAMPOLINE_REGION_START]

    class OutOfHighRAM(RuntimeError):
        pass

    def alloc_trampoline(body: bytes) -> int:
        addr = next_free_addr[0]
        while True:
            end = addr + len(body)
            if end > 0xFFFF:
                raise OutOfHighRAM(
                    f"Ran out of High RAM space for trampolines "
                    f"(${TRAMPOLINE_REGION_START:04X}-$FFFF)."
                )
            conflict = next((a for a in range(addr, end) if a in reserved_high_ram), None)
            if conflict is None:
                break
            addr = conflict + 1   # skip past the reserved address and try again
        ram[addr:end] = body
        next_free_addr[0] = end
        return addr

    EXIT_ROUTINE_ADDR = alloc_trampoline(bytes(exit_routine))

    # SCRATCH_ADDR: 1 byte, exclusively ours - a safe buffer for the
    # cached port value (direct-page addressing can't reach High RAM, so
    # we use absolute !a addressing wherever we need it). Allocated
    # through the same mechanism as everything else - avoids manually
    # picking a fixed address that could collide with the widened
    # trampoline area.
    SCRATCH_ADDR = alloc_trampoline(bytes([0x00]))

    # CRITICAL FIX (this patch): every trampoline used to jump to
    # exit_routine via BEQ - a relative branch with a range of only
    # +-127 bytes. That was always enough with the 128-byte area
    # ($FF80-$FFFF), but the area is now 256 bytes - for trampolines
    # placed far from exit_routine (near $FFFF, when exit_routine sits
    # near $FF00) a BEQ could physically fail to reach the target, which
    # would assemble into a wrong/garbage branch. Every place that checks
    # "is the value we read $FF" now uses the pattern "BNE +3 / JMP
    # !EXIT_ROUTINE_ADDR" instead (5 bytes, but with no range limit): if
    # equal (Z=1), BNE does NOT branch and falls through into the JMP ->
    # exit; if not equal, BNE jumps over the JMP.
    def branch_to_exit_template():
        return bytearray([0xD0, 0x03, 0x5F, 0x00, 0x00])

    def patch_exit_jmp(addr, jmp_opcode_offset):
        jmp_pc = addr + jmp_opcode_offset
        assert ram[jmp_pc] == 0x5F, "wrong JMP-to-exit offset in trampoline"
        ram[jmp_pc + 1] = EXIT_ROUTINE_ADDR & 0xFF
        ram[jmp_pc + 2] = (EXIT_ROUTINE_ADDR >> 8) & 0xFF

    # 3. Trampoline for MOV A, $F4 -> jump to exit_routine
    #    The value is already in A after the single read - we only need
    #    to reproduce the N/Z flags a plain MOV would have set (OR A,#0
    #    doesn't touch memory or C, it just recomputes N/Z from A's
    #    current value).
    TRAMP_A_ADDR = alloc_trampoline(bytes([
        0xE4, 0xF4,             # MOV A, $F4        (the ONE read)
        0x68, 0xFF,             # CMP A, #$FF       (0x68 = CMP A,#imm; NOTE:
                                # 0xC8 is CMP X,#imm, not A - an easy mistake
                                # to make, verified via simulation)
        *branch_to_exit_template(),
        0x08, 0x00,              # OR A, #$00        (recompute N/Z from the
                                 # value already in A, without re-reading the port)
        0x6F                     # RET
    ]))
    patch_exit_jmp(TRAMP_A_ADDR, 6)

    # 4. Trampoline for MOV X, $F4 -> jump to exit_routine
    #    The original instruction does NOT touch A - it has to be preserved.
    TRAMP_X_ADDR = alloc_trampoline(bytes([
        0x2D,                    # PUSH A       (the original leaves A alone - we preserve it)
        0xE4, 0xF4,              # MOV A, $F4   (the ONE read)
        0x68, 0xFF,              # CMP A, #$FF
        *branch_to_exit_template(),
        0x5D,                    # MOV X, A     (move the buffered value across)
        0xAE,                    # POP A        (restore the original A)
        0x3D,                    # INC X        (recompute N/Z from X's value
        0x1D,                    # DEC X        without touching memory or C; net: no change)
        0x6F                     # RET
    ]))
    patch_exit_jmp(TRAMP_X_ADDR, 7)

    # 5. Trampoline for MOV Y, $F4 -> jump to exit_routine
    #    The original instruction does NOT touch A - it has to be preserved.
    TRAMP_Y_ADDR = alloc_trampoline(bytes([
        0x2D,
        0xE4, 0xF4,              # MOV A, $F4   (the ONE read)
        0x68, 0xFF,
        *branch_to_exit_template(),
        0xFD,
        0xAE,
        0xFC,
        0xDC,
        0x6F
    ]))
    patch_exit_jmp(TRAMP_Y_ADDR, 7)

    # 6. Trampoline for CMP A, $F4 -> jump to exit_routine
    #    The original compares the CALLER'S A against $F4 - we buffer the
    #    read port value in SCRATCH_ADDR (absolute addressing, since it's
    #    High RAM) so we can compare the CALLER'S A against that same,
    #    single read value.
    TRAMP_CMP_A_ADDR = alloc_trampoline(bytes([
        0x2D,                    # PUSH A       (preserve the CALLER'S A)
        0xE4, 0xF4,              # MOV A, $F4   (the ONE read)
        0x68, 0xFF,
        *branch_to_exit_template(),
        0xC5, SCRATCH_ADDR & 0xFF, (SCRATCH_ADDR>>8)&0xFF,  # MOV !SCRATCH,A
        0xAE,                    # POP A        (restore the CALLER'S A)
        0x65, SCRATCH_ADDR & 0xFF, (SCRATCH_ADDR>>8)&0xFF,  # CMP A,!SCRATCH
        0x6F                     # RET
    ]))
    patch_exit_jmp(TRAMP_CMP_A_ADDR, 7)

    PCALL_A = bytes([0x4F, TRAMP_A_ADDR & 0xFF])
    PCALL_X = bytes([0x4F, TRAMP_X_ADDR & 0xFF])
    PCALL_Y = bytes([0x4F, TRAMP_Y_ADDR & 0xFF])
    PCALL_CMP_A = bytes([0x4F, TRAMP_CMP_A_ADDR & 0xFF])

    # The "d,#i" family (direct page + immediate value): opcode -> its
    # "A,#imm" equivalent (differs by -0x10, e.g. CMP d,#i=$78 -> CMP
    # A,#i=$68). A driver can check port $F4 DIRECTLY with an instruction
    # like "CMP $F4,#$80" (without loading the value into any register) -
    # a completely different, 3-byte pattern. Each occurrence can have a
    # different immediate value, so we generate a separate, tiny
    # trampoline for each one.
    #
    # Read the port only ONCE (into A), then compute <op> A,#imm instead
    # of <op> $F4,#imm - the numeric result is identical (ALU flags only
    # depend on the values, not the addressing mode), with no second trip
    # to hardware. Instructions other than CMP physically MODIFY memory
    # (they write the result back to d) - for d=$F4 that's a write to the
    # OUTPUT half of the port (which is hardware-independent from the
    # input half, so the write doesn't interfere with the read) - we
    # reproduce that with a single write at the end.
    DPI_OPS = {
        0x18: 'OR', 0x38: 'AND', 0x58: 'EOR',
        0x78: 'CMP', 0x98: 'ADC', 0xB8: 'SBC',
    }

    def make_dpi_trampoline(op_opcode: int, imm: int) -> int:
        a_imm_opcode = op_opcode - 0x10   # e.g. CMP d,#i($78) -> CMP A,#i($68)
        body = bytearray([
            0xE4, 0xF4,           # MOV A, $F4     (the ONE read)
            0x68, 0xFF,           # CMP A, #$FF
            *branch_to_exit_template(),
            a_imm_opcode, imm,    # <OP> A, #imm   (on the buffered value)
        ])
        if op_opcode != 0x78:  # CMP does not write its result back to memory
            body += bytes([0xC4, 0xF4])  # MOV $F4, A  (write the result - only
                                          # the output half of the port, safe)
        body += bytes([0x6F])            # RET
        addr = alloc_trampoline(bytes(body))
        patch_exit_jmp(addr, 6)
        return addr

    dpi_trampoline_cache = {}      # (op_opcode, imm) -> trampoline addr

    def pcall_for_dpi(op_opcode, imm):
        key = (op_opcode, imm)
        if key not in dpi_trampoline_cache:
            addr = make_dpi_trampoline(op_opcode, imm)
            dpi_trampoline_cache[key] = addr
        addr = dpi_trampoline_cache[key]
        return bytes([0x4F, addr & 0xFF])

    # CBNE $F4, r - "compare A with $F4, branch if not equal" in a single
    # instruction (opcode 0x2E, 3 bytes: opcode, d, r). Found as a real
    # mechanism for checking the port in song2.spc (among other things,
    # as the gate recognizing "is there a new command from the SNES",
    # right before the CALL to the actual handler).
    #
    # Three traps I fell into myself on earlier attempts:
    #  1) The relative branch offset (r) is tied to the ADDRESS the
    #     instruction jumps FROM - it can't simply be copied into a
    #     trampoline in High RAM (a different address means a different
    #     target). Fix: compute the REAL target address once, at patch
    #     time, and use a far jump (JMP !a) instead of trying to
    #     reproduce a relative branch.
    #  2) Re-reading $F4 to reproduce the original comparison (instead of
    #     buffering) risks reading a different value than the first time,
    #     if the SNES manages to write something in between the two
    #     reads - so we buffer the port value in SCRATCH_ADDR and compare
    #     the CALLER'S A against that buffer, not against the port a
    #     second time.
    #  3) THE MOST IMPORTANT ONE (stack leak): PCALL, like every call,
    #     pushes a 2-byte return address onto the stack. When the
    #     trampoline finishes via RET, that address is popped normally -
    #     fine. But when the condition is met and the trampoline instead
    #     does a far JMP to the original target (SKIPPING RET), those 2
    #     bytes are NEVER popped off the stack! This is EXACTLY the
    #     branch that runs on EVERY real command from the game (the most
    #     common case during normal play, not just on exit) - so the
    #     stack was leaking 2 bytes on every single note, quickly
    #     corrupting itself and soon leading to execution completely
    #     derailing (observed as stuck/looping audio right after playback
    #     starts). Fix: before the far jump, explicitly pop (discard)
    #     those 2 bytes by adjusting SP by +2 (MOV X,SP / INC X / INC X /
    #     MOV SP,X) - as if the trampoline had never been called via CALL
    #     at all, exactly like the original, stackless CBNE relative
    #     branch would behave.
    def make_cbne_trampoline(original_target: int) -> int:
        body = bytearray([
            0x2D,              # PUSH A            (preserve the CALLER'S A)
            0xE4, 0xF4,        # MOV A, $F4        (the ONE read)
            0x68, 0xFF,        # CMP A, #$FF
            *branch_to_exit_template(),  # -> exit_routine (the stack from
                               # PUSH A is left "abandoned" - exit_routine
                               # never returns anyway, so that's harmless)
            0xC5, SCRATCH_ADDR & 0xFF, (SCRATCH_ADDR>>8)&0xFF,  # MOV !SCRATCH,A
            0xAE,              # POP A             (restore the CALLER'S A)
            0x65, SCRATCH_ADDR & 0xFF, (SCRATCH_ADDR>>8)&0xFF,  # CMP A,!SCRATCH
            0xF0, 0x00,        # BEQ skip_jmp (local, nearby - offset filled
                               # in below; equal -> the original wouldn't
                               # have branched -> normal RET)
            0x9D,              # MOV X, SP         \
            0x3D,              # INC X              | discard the 2-byte
            0x3D,              # INC X              | return address PCALL
            0xBD,              # MOV SP, X         /  pushed (see above)
            0x5F, 0x00, 0x00,  # JMP !a            (not equal -> far jump to
                               # the real original target; address filled in below)
            0x6F,              # RET               (equal -> normal return)
        ])
        addr = alloc_trampoline(bytes(body))
        patch_exit_jmp(addr, 7)
        beq2_pc = addr + 17             # address of the second BEQ's opcode
                               # (skip_jmp, local - its target is always
                               # nearby so BEQ, not JMP, is enough here)
        assert ram[beq2_pc] == 0xF0, "wrong BEQ#2 offset in CBNE trampoline"
        skip_jmp_target = addr + 26     # address of the RET byte at the end
        assert ram[skip_jmp_target] == 0x6F, "wrong RET address computed in CBNE trampoline"
        pc_after2 = beq2_pc + 2
        rel2 = (skip_jmp_target - pc_after2) & 0xFF
        ram[beq2_pc + 1] = rel2
        jmp_pc = addr + 23               # address of the JMP !a opcode (0x5F)
        assert ram[jmp_pc] == 0x5F, "wrong JMP offset in CBNE trampoline"
        ram[jmp_pc + 1] = original_target & 0xFF
        ram[jmp_pc + 2] = (original_target >> 8) & 0xFF
        return addr

    cbne_trampoline_cache = {}     # original_target -> trampoline addr

    def pcall_for_cbne(original_target):
        if original_target not in cbne_trampoline_cache:
            cbne_trampoline_cache[original_target] = make_cbne_trampoline(original_target)
        addr = cbne_trampoline_cache[original_target]
        return bytes([0x4F, addr & 0xFF])

    # Scan and replace instructions in the driver code ($0200 - $3FFF)
    patched_count = 0
    cursor = 0x0200
    just_patched_end = -1  # position right after the last patched chunk
    while cursor < 0x3FFF:
        chunk = ram[cursor:cursor+2]
        if chunk == b'\xE4\xF4':
            ram[cursor:cursor+2] = PCALL_A   # MOV A, $F4
            patched_count += 1
            cursor += 2
            just_patched_end = cursor
        elif chunk == b'\xE5\xF4':
            # MOV A, !abs $00F4 - still reads into A (3-byte form of the
            # same read as E4,F4 above), NOT a read into X.
            ram[cursor:cursor+2] = PCALL_A   # MOV A, !$00F4
            patched_count += 1
            cursor += 2
            just_patched_end = cursor
        elif chunk == b'\xF8\xF4':
            # MOV X, $F4 - the real 2-byte direct-page read into X (E5 above
            # is NOT this; it reads into A).
            ram[cursor:cursor+2] = PCALL_X   # MOV X, $F4
            patched_count += 1
            cursor += 2
            just_patched_end = cursor
        elif chunk == b'\xEB\xF4':
            ram[cursor:cursor+2] = PCALL_Y   # MOV Y, $F4
            patched_count += 1
            cursor += 2
            just_patched_end = cursor
        elif chunk == b'\x64\xF4':
            ram[cursor:cursor+2] = PCALL_CMP_A  # CMP A, $F4
            patched_count += 1
            cursor += 2
            just_patched_end = cursor
        elif ram[cursor] in DPI_OPS and cursor+2 < 0x3FFF and ram[cursor+2] == 0xF4:
            # <OP> $F4, #imm  (3 bytes: opcode, imm, dp=$F4)
            op_opcode = ram[cursor]
            imm = ram[cursor+1]
            try:
                pcall_bytes = pcall_for_dpi(op_opcode, imm)
            except OutOfHighRAM:
                print(f"  [WARNING] Out of High RAM space - skipping the "
                      f"'d,#i' occurrence at ${cursor:04X} (left unpatched).")
                cursor += 1
                continue
            ram[cursor:cursor+2] = pcall_bytes
            ram[cursor+2] = 0x00  # NOP (3rd byte of the original instruction)
            patched_count += 1
            cursor += 3
            just_patched_end = cursor
        elif (ram[cursor] == 0x2E and cursor+1 < 0x3FFF and ram[cursor+1] == 0xF4
              and cursor != just_patched_end):
            # CBNE $F4, r  (3 bytes: opcode, dp=$F4, r). If this occurrence
            # sits RIGHT AFTER something we already patched (the typical
            # "MOV A,$F4 / CBNE $F4,rel" idiom - read + debounce), it does
            # NOT need patching on its own: on exit, the BEQ in the
            # preceding trampoline already intercepts execution earlier
            # (this CBNE never runs), and on a normal read the trampoline
            # replays the original instruction and returns right here -
            # this CBNE is left untouched and works correctly on its own
            # (it compares the port's live state directly). So we only
            # patch STANDALONE occurrences (cursor != just_patched_end),
            # saving precious space for dynamic trampolines in High RAM.
            r_original = ram[cursor+2]
            r_signed = r_original - 256 if r_original >= 128 else r_original
            original_target = (cursor + 3 + r_signed) & 0xFFFF
            try:
                pcall_bytes = pcall_for_cbne(original_target)
            except OutOfHighRAM:
                print(f"  [WARNING] Out of High RAM space - skipping the "
                      f"CBNE at ${cursor:04X} (left unpatched).")
                cursor += 1
                continue
            ram[cursor:cursor+2] = pcall_bytes
            ram[cursor+2] = 0x00  # NOP (3rd byte of the original instruction)
            patched_count += 1
            cursor += 3
            just_patched_end = cursor
        else:
            cursor += 1

    if patched_count == 0:
        print(f"[SKIP] {os.path.basename(filepath)} - No reads of port $F4 found.")
        return False

    data[0x100:0x10100] = ram
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, os.path.basename(filepath))

    with open(out_path, 'wb') as f:
        f.write(data)

    print(f"[OK]   {os.path.basename(filepath)} -> Patched {patched_count} read site(s).")
    return True

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    print(f"Working directory: {script_dir}")

    input_dir = "_music_org"
    output_dir = "music"

    if not os.path.exists(input_dir):
        os.makedirs(input_dir)
        print(f"Created directory '{input_dir}'. Put your .spc files in it and run this script again.")
        input("\nPress Enter to exit...")
        sys.exit(0)

    files = list(set(glob.glob(os.path.join(input_dir, "*.spc")) + glob.glob(os.path.join(input_dir, "*.SPC"))))

    if not files:
        print(f"ERROR: No .spc/.SPC files found in directory '{input_dir}'!")
    else:
        print(f"Found {len(files)} file(s). Starting patching...\n")
        for spc in files:
            patch_spc(spc, output_dir=output_dir)

    input("\nPress Enter to exit...")
