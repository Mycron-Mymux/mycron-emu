.PHONY: all clean z80lib

CC = gcc
LD = gcc

CFLAGS	= -Wall -O2 -fomit-frame-pointer -DLSB_FIRST -DDEBUG

TARGS = _z80emu_cffi.abi3.so

all: $(TARGS)

clean:
	rm -rf z80/*.o c-emu build

# should create the z80 directory, extract the files and patch it
z80: z80dist/fetch-z80.sh
	./z80dist/fetch-z80.sh

z80lib: z80
	make -C z80

_z80emu_cffi.abi3.so: setup.py z80/Z80.o Makefile z80emu.py z80emu_build.py z80emu_core.c
	python setup.py build_ext --inplace
