#!/usr/bin/env python

from setuptools import setup

setup(
    name="mycronemu",
    version="0.0.1",
    py_modules=[
        "diskcontroller",
        "diskimage",
        "embedded_console",
        "emu_logging",
        "emu_trace",
        "emuconfig",
        "iodevice",
        "pyemu",
        "z80emu",
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

