#!/usr/bin/env python

# z80emu_build.py

from cffi import FFI

ffibuilder = FFI()

common_defs = """
typedef unsigned char byte;

typedef struct {
    unsigned int PC;
    unsigned int SP;
    unsigned int AF;
    unsigned int BC;
    unsigned int DE;
    unsigned int HL;
    unsigned int IX;
    unsigned int IY;

    unsigned int AF2;
    unsigned int BC2;
    unsigned int DE2;
    unsigned int HL2;

    unsigned int IFF1;
    unsigned int IFF2;
    unsigned int HALT;
    unsigned int IM;
    unsigned int I;
    unsigned int R;
    unsigned int R2;
} z80emu_regs_t;

void z80emu_reset(void);

unsigned long z80emu_step(void);
unsigned long z80emu_run_steps(unsigned long n);
unsigned int z80emu_get_pc(void);

byte z80emu_mem_rd(unsigned int addr);
void z80emu_mem_wr(unsigned int addr, byte value);
void z80emu_mem_set_prot(unsigned int start, unsigned int end, byte value);

void z80emu_mem_set_track_mask(unsigned int start, unsigned int end, byte mask);
void z80emu_mem_unset_track_mask(unsigned int start, unsigned int end, byte mask);
              
int z80emu_mem_dis(unsigned int pc, char *out, unsigned long out_size);

void z80emu_get_regs(z80emu_regs_t *out);

byte *z80emu_memory(void);
byte *z80emu_memory_prot(void);

typedef byte (*z80emu_in_cb_t)(byte port);
typedef void (*z80emu_out_cb_t)(byte port, byte value);

void z80emu_set_io_callbacks(z80emu_in_cb_t in_cb, z80emu_out_cb_t out_cb);

"""

ffibuilder.cdef(common_defs)

extra_compile_args = [
    "-O2",
    "-Wall",
    "-fomit-frame-pointer",
    "-DLSB_FIRST",
]

ffibuilder.set_source(
    "_z80emu_cffi",
    """
    #include <stdint.h>
    """ + common_defs, 

    sources=[
        "z80emu_core.c",
        "z80/Z80.c",
        "z80/Z80Dasm.c",
    ],
    include_dirs=[
        ".",
        "z80",
    ],
    extra_compile_args=extra_compile_args,
)

if __name__ == "__main__":
    ffibuilder.compile(verbose=True)

