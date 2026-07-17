#!/usr/bin/env python

import logging

import z80emu
from z80emu import mem_dis, get_regs


trace_log = logging.getLogger("mycron.trace")


def regs_str(regs=None):
    """Returns a dict with regs and a string rep of regs with hex values"""
    names = ("PC", "SP", "AF", "BC", "DE", "HL")
    if regs is None:
        regs = z80emu.get_regs()
    values = ",".join(
        f"{name}={regs[name]:04x}"
        for name in names
        if name in regs)
    return f"REGS_hex({values})"


def regs_stack_str():
    regs = get_regs()
    s = regs_str(regs)
    sp = regs['SP']
    s += " Stack: ["
    s += " ".join([f"{z80emu.mem_rd(sp + i):02x}" for i in range(10)])
    s += "]"
    return s


def pc_disasm_str(pc_offset=0):
    """
    Return a short string with current PC and disassembly at PC.

    Note: during an IN/OUT callback, depending on the Z80 backend, PC may
    already point at the next instruction rather than the IN/OUT instruction.
    Still useful for context.

    To address this, you can add pc_offset=PC_OFFSET_STD_IO, which should address
    most cases.
    """
    try:
        regs = z80emu.get_regs()
        pc = (regs["PC"] + pc_offset) & 0xffff
        offs = "" if pc_offset == 0 else f" PC offset {pc_offset}"
        _, asm = mem_dis(pc)
        return f"PC={pc:04x} {asm:14}{offs}"
    except Exception as error:
        return f"PC=???? <disassembly failed: {error}>"


def write(message, *, include_regs=False, include_stack=False, pc_offset=0):
    """Trace write..
    NB: see notes about pc_disasm_str(). It might return the _next_ instruction.
    """
    text = f"{message} {pc_disasm_str(pc_offset)}"

    if include_stack:
        text += " " + regs_stack_str()
    elif include_regs:
        text += "  " + regs_str()

    trace_log.debug(text)
