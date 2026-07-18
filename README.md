Mycron emulator
================

This is an emulator for the Mycro-1/Mycro-3 computers with a Z80 CPU and
PROMs from the DIM-1001 (i8080) and DIM-1003 (Z-80) CPU card.

The emulator is written as a tool for exploring and understanding the
Mycron computers, so it is not quite turn-key. It shouldn't be too
hard to play around with, but I haven't tested it on non-Linux
systems.

A more turn-key system could perhaps use some of the findings here to
add Mycron support to other existing emulators that are capable of
emulating similar systems like the Altair.


## Compiling the Z-80 library

The emulator uses z80ex to emulate the Z80 CPU. To bridge the C
library and the Python code, I'm using cffi. To install both on
Ubuntu, you can use:

```
sudo apt install libz80ex-dev python3-cffi
```

On some systems, you may need to fetch z80ex and cffi through other means.
It should be possible to install cffi with pip:

```
pip install cffi
```

To compile the project on a Linux box (tested on Ubuntu 26.04), just run:
```
make
```

## Building the emulator

If you want to modify or run it locally without making a "final" pip
install, try to run (from the main directory):

    `python -m pip install -e .`

Note the final dot in the command. It is necessary to tell pip to install
it as a module linked to the current directory.

You should now have two commands available:

- mycron : the main program for the emulator.
- makedisk : a utility program to make disk images.
  It supports empty disks and Mycron DATA and PROG disks.

## Using the emulator

Before starting the emulator, you need the following:

### A method for setting up a virtual pty

I'm using the following:

    `socat -d -d pty,rawer,echo=0 pty,rawer,echo=0`

This should print out the device names of the two ends of the two-way
pipe that is provided. Provide one of them to the emulator and the
other to the terminal program.

### A serial communication program

I'm just using minicom, but other programs should work.
Examples with minicom and tio:

- `minicom -D /dev/pts/<xxx> -b 9600`
- `tio -b 9600 /dev/pts/<xxx>`

If you want a nice and cozy feeling while playing with the emulator,
it may be an idea to look into cool-retro-term and run minicom inside
that. I have had mixed results with running the apt version of
cool-retro-term (incorrect drawing of window backgrounds, for
instance), but recompiling the binary myself seemed to work better.


### A run directory with prom dumps and diskette images

A starting directory is provided in this project.
The idea is to move more of the config options to the config file
included in the directory. This is on the TODO list.

The main things that needs to be included here are:

- the config file
- PROM dumps
- diskette images (if you intend to use diskette simulation)


# Other things to know about

## Using the Python debug console

An optional Python console has been added to the emulator.

You can set up an extra set of ptys using another socat similar to the
method used for the Mycron console above.

The emulator can then be pointed to one end of the pair using  the parameter
`-ec /dev/pts/<one end>` to the emulator.

Then you can use `minicom -D /dev/pts/<other end>` to get access to a
Python prompt.

Other options to connect to the console include (if 77 is the "other end"):
- `socat - /dev/pts/77,raw,echo=0`
- `rlwrap -a socat - /dev/pts/77,raw,echo=0`
- `tio /dev/pts/77`

The rlwrap version is probably the one that comes closest to a normal
Python console, although it is not a fully supported console with
readline and tab completion.

## Swapping diskette images while running

The emulator opens and closes the image files for every read and write
operation. The simplest way of simulating swapping a diskette is to
just copy a file over the corresponding diskette image in the run
directory.

Disk images are named disk-00.img to disk-07.img.
