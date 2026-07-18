#!/usr/bin/env python

"""
z80emu_build.py

This started out as machine generated code by ChatGPT.
It helpted re-write the interface between the Z80-emulator and python to use cffi.
"""

from pathlib import Path
from cffi import FFI

ROOT = Path(__file__).resolve().parent
CSRC = ROOT / "csrc"

ffibuilder = FFI()

# cffi doesn't like pragma once, so we need to strip that one
header = (CSRC / "z80emu_core.h").read_text()
common_core_h = "\n".join([
    l for l in header.splitlines()
    if "#pragma once" not in l])

ffibuilder.cdef(common_core_h)

extra_compile_args = [
    "-O2",
    "-Wall",
    "-fomit-frame-pointer",
    "-DLSB_FIRST",
]

ffibuilder.set_source(
    "_z80emu_cffi",
    '#include "z80emu_core.h"',   # let generated C code include the actual header
    sources=[
        str(CSRC / "z80emu_core_z80ex.c"),
    ],
    include_dirs = [
        str(CSRC),
    ], 
    libraries=[
        "z80ex",
        "z80ex_dasm",
    ],
    extra_compile_args=extra_compile_args,
)

if __name__ == "__main__":
    ffibuilder.compile(verbose=True)
