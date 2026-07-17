/*
 * z80emu_core_z80ex.c
 * Note: ChatGPT did a major rewrite/reorg of this one to move it over to cffi and to use z80ex as a CPU emulator.
 */

#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h> // Required for va_start, va_arg, va_end, va_list

#include <z80ex/z80ex.h>
#include <z80ex/z80ex_dasm.h>
#include "z80emu_core.h"

static byte memory[Z80_MEM_SIZE];
static byte memory_prot[Z80_MEM_SIZE];
static byte memory_track[Z80_MEM_SIZE];

static z80emu_in_cb_t cb_io_in = NULL;
static z80emu_out_cb_t cb_io_out = NULL;

static Z80EX_CONTEXT *cpu = NULL;

// Simple interrupt state.
// For now, expose both "pulse IRQ once" and "hold IRQ line active".
static int irq_line_active = 0;
static byte irq_vector = 0xff;


// -------------------- diagnostics --------------------
static void emu_trace(const char *format, ...)
{
    va_list args;

    va_start(args, format);
    vfprintf(stderr, format, args);
    va_end(args);
}

static void emu_warn(const char *format, ...)
{
    va_list args;

    va_start(args, format);
    vfprintf(stderr, format, args);
    va_end(args);
}


// -------------------- io callbacks --------------------

void z80emu_set_io_callbacks(z80emu_in_cb_t in_cb, z80emu_out_cb_t out_cb)
{
    cb_io_in = in_cb;
    cb_io_out = out_cb;
}

// -------------------- z80ex dasm --------------------

static Z80EX_BYTE emu_dasm_readbyte_cb(Z80EX_WORD addr, void *user_data)
{
    (void)user_data;
    return memory[addr & 0xffff];
}


static int emu_disasm(unsigned int pc, char *out, unsigned long out_size)
{
    int t_states = 0;
    int t_states2 = 0;

    pc &= 0xffff;

    if (out_size == 0) {
        return 0;
    }

    out[0] = '\0';

    return z80ex_dasm(
        out,
        (int)out_size,
        0,                  // flags: 0 = default hex formatting
        &t_states,
        &t_states2,
        emu_dasm_readbyte_cb,
        (Z80EX_WORD)pc,
        NULL
    );
}


// -------------------- z80ex callbacks --------------------

static Z80EX_BYTE emu_mread_cb(
    Z80EX_CONTEXT *cpu_arg,
    Z80EX_WORD addr,
    int m1_state,
    void *user_data
)
{
    (void)cpu_arg;
    (void)m1_state;
    (void)user_data;

    unsigned int a = addr & 0xffff;
    byte val = memory[a];

    if (memory_track[a] & TRACK_RD) {
        unsigned int pc = 0;

        if (cpu) {
            pc = z80ex_get_reg(cpu, regPC) & 0xffff;
        }

        char txt[120];
        emu_disasm(pc, txt, sizeof(txt));

        emu_trace("RD %04x -> %02x pc %04x instr: %s\n",
                  a, val, pc, txt);
    }

    return val;
}


static void emu_mwrite_cb(
    Z80EX_CONTEXT *cpu_arg,
    Z80EX_WORD addr,
    Z80EX_BYTE value,
    void *user_data
)
{
    (void)cpu_arg;
    (void)user_data;

    unsigned int a = addr & 0xffff;
    byte v = value & 0xff;

    if (memory_track[a] & TRACK_WR) {
        unsigned int pc = 0;

        if (cpu) {
            pc = z80ex_get_reg(cpu, regPC) & 0xffff;
        }

        char txt[120];
        emu_disasm(pc, txt, sizeof(txt));

        emu_trace("WR %04x : %02x -> %02x pc %04x instr: %s\n",
               a, memory[a], v, pc, txt);
    }

    if (memory_prot[a]) {
        emu_trace("WR---ignore write to protected addr %04x wr %02x\n", a, v);
        return;
    }

    memory[a] = v;
}


static Z80EX_BYTE emu_pread_cb(
    Z80EX_CONTEXT *cpu_arg,
    Z80EX_WORD port,
    void *user_data
)
{
    (void)cpu_arg;
    (void)user_data;

    /*
     *  z80ex passes a 16-bit port.
     *  The old emulator used only the low 8 bits.
     */
    byte p = port & 0xff;

    if (cb_io_in == NULL) {
        emu_trace("Z80_In %02x\n", p);
        return 0;
    }

    return cb_io_in(p);
}


static void emu_pwrite_cb(
    Z80EX_CONTEXT *cpu_arg,
    Z80EX_WORD port,
    Z80EX_BYTE value,
    void *user_data
)
{
    (void)cpu_arg;
    (void)user_data;

    byte p = port & 0xff;
    byte v = value & 0xff;

    if (cb_io_out == NULL) {
        emu_trace("Z80_Out %02x <- %02x\n", p, v);
        return;
    }

    cb_io_out(p, v);
}


static Z80EX_BYTE emu_intread_cb(
    Z80EX_CONTEXT *cpu_arg,
    void *user_data
)
{
    (void)cpu_arg;
    (void)user_data;

    /*
     * IM 2: this is the vector byte.
     * IM 1: normally ignored.
     * IM 0: 0xff corresponds to RST 38h.
     */
    return irq_vector;
}

// -------------------- public cffi API --------------------

static void init_memory(void)
{
    memset(memory, 0, Z80_MEM_SIZE);
    memset(memory_prot, 0, Z80_MEM_SIZE);
    memset(memory_track, TRACK_NONE, Z80_MEM_SIZE);
}


void z80emu_reset(void)
{
    irq_line_active = 0;
    irq_vector = 0xff;

    init_memory();

    if (cpu != NULL) {
        z80ex_destroy(cpu);
        cpu = NULL;
    }

    cpu = z80ex_create(
        emu_mread_cb, NULL,
        emu_mwrite_cb, NULL,
        emu_pread_cb, NULL,
        emu_pwrite_cb, NULL,
        emu_intread_cb, NULL
    );

    if (cpu == NULL) {
        emu_warn("z80ex_create failed\n");
        abort();
    }

    z80ex_reset(cpu);
}


static unsigned int emu_get_pc(void)
{
    if (cpu == NULL) {
        return 0;
    }

    return z80ex_get_reg(cpu, regPC) & 0xffff;
}


static void emu_step(void)
{
    if (cpu == NULL) {
        z80emu_reset();
    }

    unsigned int pc = emu_get_pc();

    if (memory_track[pc] & TRACK_EXEC) {
        char txt[120];
        emu_disasm(pc, txt, sizeof(txt));

        emu_trace("TRACK_EXEC: about to execute instruction at %04x: %s\n",
                  pc, txt);
    }

    /*
     *  If you want a level-triggered IRQ line, call z80ex_int while active.
     *  z80ex_int() will only be accepted when the CPU state allows it.
     */
    if (irq_line_active) {
        z80ex_int(cpu);
    }

    /*
     * z80ex_step returns elapsed T-states.
     */
    z80ex_step(cpu);
}


void z80emu_run_steps(unsigned long n)
{
    for (unsigned long i = 0; i < n; i++) {
        emu_step();
    }
}


byte z80emu_mem_rd(unsigned int addr)
{
    return memory[addr & 0xffff];
}


/*
 * Debugger/backdoor write.
 *
 * This bypasses protection, matching the previous py_mem_wr behavior.
 * CPU writes still go through z80ex_mwrite_cb and obey memory_prot.
 */
void z80emu_mem_wr(unsigned int addr, byte value)
{
    memory[addr & 0xffff] = value;
}


void z80emu_mem_set_prot(unsigned int start, unsigned int end, byte value)
{
    /*
     *  Python-style half-open interval:
     *
     *     [start, end)
     */
    if (start > Z80_MEM_SIZE ||
        end > Z80_MEM_SIZE ||
        start > end) {
        emu_warn("WARNING: illegal mem_set_prot addr range: %d -> %d -- ignoring range\n", start, end);
        return;
    }

    emu_trace("Setting memory protection for region 0x%x to 0x%x to %d\n",
              start, end, value);

    for (unsigned int a = start; a < end; a++) {
        memory_prot[a] = value;
    }
}


void z80emu_mem_set_track_mask(unsigned int start, unsigned int end, byte mask)
{
    if (start > Z80_MEM_SIZE ||
        end > Z80_MEM_SIZE ||
        start > end) {
        emu_warn("WARNING: illegal mem_set_track_mask addr range: %d -> %d -- ignoring range\n", start, end);
        return;
    }

    for (unsigned int a = start; a < end; a++) {
        memory_track[a] |= mask;
    }
}


void z80emu_mem_unset_track_mask(unsigned int start, unsigned int end, byte mask)
{
    if (start > Z80_MEM_SIZE ||
        end > Z80_MEM_SIZE ||
        start > end) {
        emu_warn("WARNING: illegal mem_unset_track_mask addr range: %d -> %d -- ignoring range\n", start, end);
        return;
    }

    byte nmask = ~mask;

    for (unsigned int a = start; a < end; a++) {
        memory_track[a] &= nmask;
    }
}


int z80emu_mem_dis(unsigned int pc, char *out, unsigned long out_size)
{
    return emu_disasm(pc, out, out_size);
}


void z80emu_get_regs(z80emu_regs_t *out)
{
    if (cpu == NULL) {
        z80emu_reset();
    }

    memset(out, 0, sizeof(*out));

    out->PC = z80ex_get_reg(cpu, regPC);
    out->SP = z80ex_get_reg(cpu, regSP);

    out->AF = z80ex_get_reg(cpu, regAF);
    out->BC = z80ex_get_reg(cpu, regBC);
    out->DE = z80ex_get_reg(cpu, regDE);
    out->HL = z80ex_get_reg(cpu, regHL);

    out->IX = z80ex_get_reg(cpu, regIX);
    out->IY = z80ex_get_reg(cpu, regIY);

    out->AF2 = z80ex_get_reg(cpu, regAF_);
    out->BC2 = z80ex_get_reg(cpu, regBC_);
    out->DE2 = z80ex_get_reg(cpu, regDE_);
    out->HL2 = z80ex_get_reg(cpu, regHL_);

    out->IFF1 = z80ex_get_reg(cpu, regIFF1);
    out->IFF2 = z80ex_get_reg(cpu, regIFF2);
    out->IM = z80ex_get_reg(cpu, regIM);

    out->I = z80ex_get_reg(cpu, regI);
    out->R = z80ex_get_reg(cpu, regR);
    out->R2 = z80ex_get_reg(cpu, regR7);

    /*
     *  z80ex does not always expose HALT as a regular register.
     *  HALT has been removed from the exposed registers for now.
     */
}


byte *z80emu_memory(void)
{
    return memory;
}


byte *z80emu_memory_prot(void)
{
    return memory_prot;
}


// -------------------- interrupt helpers --------------------

void z80emu_set_irq_line(int active, byte vector)
{
    irq_line_active = active ? 1 : 0;
    irq_vector = vector;
}


int z80emu_pulse_irq(byte vector)
{
    if (cpu == NULL) {
        z80emu_reset();
    }

    irq_vector = vector;

    /*
       z80ex_int returns the number of T-states used by the interrupt
       acknowledge sequence in many z80ex versions.
    */
    return z80ex_int(cpu);
}


int z80emu_nmi(void)
{
    if (cpu == NULL) {
        z80emu_reset();
    }

    return z80ex_nmi(cpu);
}
