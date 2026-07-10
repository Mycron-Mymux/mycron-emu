// z80emu_core.c
// Machine re-generated code (based on old library) that is slightly modified.

#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

#include "z80/Z80.h"
#include "z80/Z80Dasm.h"

#define MEM_SIZE (64 * 1024)

static byte memory[MEM_SIZE];
static byte memory_prot[MEM_SIZE];
static byte memory_track[MEM_SIZE];

int Z80_IRQ = 0;

const int trace_ops = 0;

enum Track_States {
    TRACK_NONE = 0,
    TRACK_RD   = 0x01,
    TRACK_WR   = 0x02,
    TRACK_EXEC = 0x04,
};

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


typedef byte (*z80emu_in_cb_t)(byte port);
typedef void (*z80emu_out_cb_t)(byte port, byte value);

static z80emu_in_cb_t cb_io_in = NULL;
static z80emu_out_cb_t cb_io_out = NULL;

void z80emu_set_io_callbacks(z80emu_in_cb_t in_cb, z80emu_out_cb_t out_cb)
{
    cb_io_in = in_cb;
    cb_io_out = out_cb;
}


int Z80_Step(void);
Z80_Regs *Z80_GetRegs_Ref(void);

static Z80_Regs *z80_regs_ref = NULL;


// Required by this Z80 core.
void Z80_Reti(void) {}
void Z80_Retn(void) {}
void Z80_Patch(Z80_Regs *regs) {}


static inline unsigned int mask_addr(unsigned int addr)
{
    return addr & 0xffff;
}


static unsigned long rd_word(dword A)
{
    A &= 0xffff;
    return memory[A] | (memory[(A + 1) & 0xffff] << 8);
}


void Z80_RegisterDump_oneline(void)
{
    int i;
    Z80_Regs *R = z80_regs_ref;

    printf(
        "AF:%04X HL:%04X DE:%04X BC:%04X PC:%04X SP:%04X IX:%04X IY:%04X ",
        R->AF.W.l,
        R->HL.W.l,
        R->DE.W.l,
        R->BC.W.l,
        R->PC.W.l,
        R->SP.W.l,
        R->IX.W.l,
        R->IY.W.l
    );

    printf("STACK: ");
    for (i = 0; i < 10; ++i) {
        printf("%04lX ", rd_word(R->SP.D + i * 2));
    }
    puts("");
}


// Called periodically by the Z80 core.
// For now preserve current behavior: ignore interrupts.
int Z80_Interrupt(void)
{
    if (trace_ops) {
        printf("Z80_interrupt: icount %4x iperiod %4x ", Z80_ICount, Z80_IPeriod);
        Z80_RegisterDump_oneline();
    }

    return Z80_IGNORE_INT;
}


// Z80 IN instruction.
byte Z80_In(byte port)
{
    if (cb_io_in == NULL) {
        printf("Z80_In %02x\n", port);
        return 0;
    }

    return cb_io_in(port);
}


// Z80 OUT instruction.
void Z80_Out(byte port, byte value)
{
    if (cb_io_out == NULL) {
        printf("Z80_Out %02x <- %02x\n", port, value);
        return;
    }

    cb_io_out(port, value);
}

// Z80 memory read.
unsigned Z80_RDMEM(dword A)
{
    A &= 0xffff;

    unsigned val = memory[A];

    if (memory_track[A] & TRACK_RD) {
        char txt[120];
        unsigned long pc = z80_regs_ref->PC.D & 0xffff;
        Z80_Dasm(&memory[pc], txt, pc);

        printf(
            "RD %04lx -> %02x   --- icount %02x iperiod %02x pc %04lx instr: %s\n",
            (unsigned long)A,
            val,
            Z80_ICount,
            Z80_IPeriod,
            pc,
            txt
        );
    }

    if (trace_ops) {
        printf("RD %04lx -> %02x   --- icount %x iperiod %x\n",
               (unsigned long)A, val, Z80_ICount, Z80_IPeriod);
    }

    return val;
}


// Z80 memory write.
void Z80_WRMEM(dword A, byte V)
{
    A &= 0xffff;

    if (memory_track[A] & TRACK_WR) {
        char txt[120];
        unsigned long pc = z80_regs_ref->PC.D & 0xffff;
        Z80_Dasm(&memory[pc], txt, pc);

        printf(
            "WR %04lx : %02x -> %02x   --- icount %02x iperiod %02x pc %04lx instr: %s\n",
            (unsigned long)A,
            memory[A],
            V,
            Z80_ICount,
            Z80_IPeriod,
            pc,
            txt
        );
    }

    if (memory_prot[A]) {
        printf("WR---ignore write to protected addr %04lx wr %02x\n",
               (unsigned long)A,
               V);
        return;
    }

    if (trace_ops) {
        printf("WR %04lx %02x -> %02x\n", (unsigned long)A, memory[A], V);
    }

    memory[A] = V;
}


void z80emu_mem_set_prot(unsigned int start, unsigned int end, byte value)
{
    /*
       Note: this uses a Python-style half-open interval:

           [start, end)

       pyemu.py currently calls:

           mem_set_prot(start, start + len, 1)

       so half-open is the least surprising behavior.
    */

    start &= 0xffff;

    if (end > MEM_SIZE) {
        end = MEM_SIZE;
    }

    printf("Setting memory protection for region 0x%x to 0x%x to %d\n",
           start,
           end,
           value);
    fflush(stdout);

    for (unsigned int a = start; a < end; a++) {
        memory_prot[a] = value;
    }
}


void z80emu_mem_set_track_mask(unsigned int start, unsigned int end, byte mask)
{
    start &= 0xffff;

    if (end > MEM_SIZE) {
        end = MEM_SIZE;
    }

    for (unsigned int a = start; a < end; a++) {
        memory_track[a] |= mask;
    }
}


void z80emu_mem_unset_track_mask(unsigned int start, unsigned int end, byte mask)
{
    start &= 0xffff;

    if (end > MEM_SIZE) {
        end = MEM_SIZE;
    }

    byte nmask = ~mask;

    for (unsigned int a = start; a < end; a++) {
        memory_track[a] &= nmask;
    }
}


unsigned long z80emu_step(void)
{
    return Z80_Step();
}


static unsigned long track_step(void)
{
    unsigned long pc = z80_regs_ref->PC.D & 0xffff;

    if (memory_track[pc] & TRACK_EXEC) {
        char txt[120];
        Z80_Dasm(&memory[pc], txt, pc);

        printf("TRACK_EXEC: about to execute instruction at %04lx: %s\n",
               pc,
               txt);
    }

    return Z80_Step();
}


unsigned long z80emu_run_steps(unsigned long n)
{
    unsigned long running = 1;

    for (unsigned long i = 0; i < n && running; i++) {
        running = track_step();
    }

    return running;
}


unsigned int z80emu_get_pc(void)
{
    if (!z80_regs_ref) {
        return 0;
    }

    return z80_regs_ref->PC.D & 0xffff;
}


byte z80emu_mem_rd(unsigned int addr)
{
    return memory[addr & 0xffff];
}


// Debugger/backdoor write.
// This intentionally bypasses write protection, like the old py_mem_wr().
void z80emu_mem_wr(unsigned int addr, byte value)
{
    memory[addr & 0xffff] = value;
}


int z80emu_mem_dis(unsigned int pc, char *out, unsigned long out_size)
{
    pc &= 0xffff;

    char txt[120];
    int len = Z80_Dasm(&memory[pc], txt, pc);

    if (out_size > 0) {
        snprintf(out, out_size, "%s", txt);
    }

    return len;
}


void z80emu_get_regs(z80emu_regs_t *out)
{
    Z80_Regs *regs = Z80_GetRegs_Ref();

    out->PC = regs->PC.D;
    out->SP = regs->SP.D;
    out->AF = regs->AF.D;
    out->BC = regs->BC.D;
    out->DE = regs->DE.D;
    out->HL = regs->HL.D;
    out->IX = regs->IX.D;
    out->IY = regs->IY.D;

    out->AF2 = regs->AF2.D;
    out->BC2 = regs->BC2.D;
    out->DE2 = regs->DE2.D;
    out->HL2 = regs->HL2.D;

    out->IFF1 = regs->IFF1;
    out->IFF2 = regs->IFF2;
    out->HALT = regs->HALT;
    out->IM = regs->IM;
    out->I = regs->I;
    out->R = regs->R;
    out->R2 = regs->R2;
}


byte *z80emu_memory(void)
{
    return memory;
}


byte *z80emu_memory_prot(void)
{
    return memory_prot;
}


static void init_memory(void)
{
    memset(memory, 0, MEM_SIZE);
    memset(memory_prot, 0, MEM_SIZE);
    memset(memory_track, TRACK_NONE, MEM_SIZE);
}


void z80emu_reset(void)
{
    Z80_IRQ = 0;
    init_memory();

    Z80_Reset();
    z80_regs_ref = Z80_GetRegs_Ref();

    /*
       Preserve the current startup tracking behavior.

       Original z80emu.c had:

           mem_set_track_mask(0x9000, 0xffff, TRACK_EXEC);

       Use half-open interval, so end should be 0x10000.
    */
    z80emu_mem_set_track_mask(0x9000, 0x10000, TRACK_EXEC);
}

