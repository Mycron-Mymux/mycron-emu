.PHONY: all clean

CC = gcc
LD = gcc
SHELL = bash
CFLAGS	= -Wall -O2 -fomit-frame-pointer -DLSB_FIRST -DDEBUG

TARGS = _z80emu_cffi.abi3.so

all: $(TARGS)

clean:
	rm -rf build _z80*.so __pycache__

test:
	python -m pytest

verbose_test:
	python -m pytest -vv

_z80emu_cffi.abi3.so: setup.py Makefile z80emu_build.py csrc/*.h csrc/*.c
	python setup.py build_ext --inplace

install_local:
	python3 -m pip install -e .

# while experimenting with llm tools, not intended for the distribution
snapshot:
	python zzextra/make_snapshot.py 
	@head -40 zzextra/tmp/project-snapshot.txt
	wc zzextra/tmp/project-snapshot.txt


wc:
	@wc *.py $(shell find src -iname \*.py) $(shell find csrc -iname \*.h -o -iname \*.c)
