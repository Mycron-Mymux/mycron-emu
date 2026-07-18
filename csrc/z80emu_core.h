#pragma once

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
    unsigned int IM;
    unsigned int I;
    unsigned int R;
    unsigned int R2;
} z80emu_regs_t;

enum Track_States {
    TRACK_NONE = 0,
    TRACK_RD   = 0x01,
    TRACK_WR   = 0x02,
    TRACK_EXEC = 0x04,
};


/* cffi doesn't like defines, so use enums*/
enum {
    Z80_MEM_SIZE = (64 * 1024)
};

void z80emu_reset(void);

void z80emu_run_steps(unsigned long n);

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

void z80emu_set_irq_line(int active, byte vector);
int z80emu_pulse_irq(byte vector);
int z80emu_nmi(void);
