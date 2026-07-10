#!/usr/bin/env python

from setuptools import setup

setup(
    name="z80emu",
    version="0.0.1",
    py_modules=[
        "z80emu",
        "pyemu",
        "diskimage",
        "emuconfig",
        "embedded_console",
    ],
    setup_requires=[
        "cffi>=1.15",
    ],
    install_requires=[
        "cffi>=1.15",
    ],
    cffi_modules=[
        "z80emu_build.py:ffibuilder",
    ],
)

# Extra hack. Need these flags to get the emultator to work correctly
# extra_compile_args += ["-fomit-frame-pointer", "-DLSB_FIRST"]

