Mycron emulator
================

This is an emulator for the Mycro-1 computer with the Z80 CPU and
PROMs from the DIM-1003 CPU card.

It may be possible to emulate the earlier DIM-1001 CPU card that uses
the i8080 CPU, but that would require some modifications to only load
one PROM region. This is not tested.

The emulator is written as a tool for exploring and understanding the
Mycron computers, so it is not quite turn-key. It shouldn't be too
hard to play around with, but I haven't tested it on non-Linux
systems.

A more turn-key system could perhaps use some of the findings here to
add Mycron support to other existing emulators that are capable of
emulating similar systems like the Altair.



## Compiling the Z-80 library

The z-80 library uses a z-80 emulator that is found here: 

- https://www.komkon.org/~dekogel/misc.html
- https://www.komkon.org/~dekogel/files/misc/z80em.zip

I have tried contacting the author about including a modified version
of the source code in this repository, but the e-mail address doesn't
appear to work any more due to a missing MX for the domain
(apparently).

Until I find a better solution, a shell script is included here to
download the library from the web page and patch it before make can
continue.

All of this should be handled by the makefile and the script 
included in the z80dist/ directory. 

To compile the project on a Linux box (tested on Ubuntu 24.04): 

- run make
- make a symbolic link to the compiled emulator library: 
  `ln -s build/lib.linux-x86_64-cpython-312/z80emu.cpython-312-x86_64-linux-gnu.so z80emu.so`
  
The reason for the last manual step is that the makefile only runs
setup.py build and not install. This has just been a manual workaround
for me so far. A cleaner method is considered TODO material. 



## Using the emulator

Before starting the emulator, you need the following: 

### A method for setting up a virtual pty

I'm using the following: 

    `socat -d -d pty,rawer,echo=0 pty,rawer,echo=0`

This should print out the device names of the two ends of the two-way
pipeline that is provided. Provide one of them to the emulator and the
other to the terminal program.

### A serial communication program

I'm just using minicom, but most should work. 

    `minicom -D /dev/pty/<xxx> -b 9600`

There are some problems with some UTF characters crashing the emulator. 
I haven't dug into this yet. 

If you want a nice and cozy feeling while playing with the emulator,
it may be an idea to look into cool-retro-term and run minicom inside
that. I have had mixed results with running the apt version of
cool-retro-term, but recomipiling the binary myself seemed to work
better (incorrect draing of window backgrounds, for instance).


### A run directory with prom dumps and diskette images

A starting directory is provided in this project. 
The idea is to move more of the config options to the config file 
included in the directory. This is on the TODO list.

The main things that needs to be included here are: 

- the config file
- PROM dumps 
- diskette images (if you intend to use diskette simulation)


## Other things to know about

### Swapping diskette images while running

The emulator opens and closes the image files for every read and write
operation. The simplest way of simulating swapping a diskette is to
just copy a file over the corresponding diskette image in the run
directory.

Disk images are named disk-00.img to disk-07.img. 




