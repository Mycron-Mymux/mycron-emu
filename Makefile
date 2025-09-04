.PHONY: all clean z80lib

CC = gcc
LD = gcc

CFLAGS	= -Wall -O2 -fomit-frame-pointer -DLSB_FIRST -DDEBUG

TARGS = z80lib z80emu.so


all: $(TARGS)

clean:
	rm z80/*.o c-emu

# should create the z80 directory, extract the files and patch it
z80: z80dist/fetch-z80.sh
	./z80dist/fetch-z80.sh

z80lib: z80
	make -C z80


z80emu.so: z80emu.c setup.py z80/Z80.o Makefile
	python setup.py build	

