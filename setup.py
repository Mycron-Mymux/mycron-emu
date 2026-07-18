#!/usr/bin/env python

from setuptools import find_packages, setup

setup(
    name="mycron_emu",
    version="0.0.1",
    package_dir={"" : "src"},
    packages=find_packages("src"),
    setup_requires=[
        "cffi>=1.15",
    ],
    install_requires=[
        "cffi>=1.15",
    ],
    cffi_modules=[
        "z80emu_build.py:ffibuilder",
    ],
    entry_points={
        "console_scripts": [
            "mycron=mycron_emu.cli.mycron:main",
            "makedisk=mycron_emu.cli.makedisk:main",
        ],
    },
)
