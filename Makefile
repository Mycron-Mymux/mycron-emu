.PHONY: all clean

CC = gcc
LD = gcc

CFLAGS	= -Wall -O2 -fomit-frame-pointer -DLSB_FIRST -DDEBUG

TARGS = _z80emu_cffi.abi3.so

all: $(TARGS)

clean:
	rm -rf build _z80*.so

_z80emu_cffi.abi3.so: setup.py Makefile z80emu.py z80emu_build.py z80emu_core_z80ex.c z80emu_core.h
	python setup.py build_ext --inplace
