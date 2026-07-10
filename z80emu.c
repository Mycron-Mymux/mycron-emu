/* 
 * Python library for z80 emulation
 * 
 * https://docs.python.org/3/extending/building.html
 * https://docs.python.org/3/extending/newtypes.html
 * https://docs.python.org/3/extending/extending.html#writing-extensions-in-c
 * https://docs.python.org/3.11/c-api/concrete.html
 * https://docs.python.org/3.11/c-api/index.html
 * https://docs.python.org/3/extending/extending.html#calling-python-functions-from-c
 * https://docs.python.org/3/extending/extending.html#building-arbitrary-values
 * 
 */

#include <string.h>
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdlib.h>
#include <poll.h>

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "z80/Z80.h"
#include "z80/Z80Dasm.h"

/* Current IRQ status. Checked after EI occurs */
int Z80_IRQ = 0;          

#define MEM_SIZE     (64 * 1024)
static const int MONITOR_SIZE = 2 * 1024;


PyObject *cb_io_in  = NULL; 
PyObject *cb_io_out = NULL; 


/* TODO functions */ 
void Z80_Reti(void) {}; 
void Z80_Retn(void) {}; 
void Z80_Patch(Z80_Regs *regs) {};   // Called for ED FE ... 


const int trace_ops = 0;
extern int Z80_Trace;

static byte memory[MEM_SIZE];
static byte memory_prot[MEM_SIZE];    // 0 if not write protected, non-zero if write protected.
static byte memory_track[MEM_SIZE];   // 0 if nothing, uses the below track states if any callback 

// When to use callbacks if a certain instruction is used 
enum Track_States {
    TRACK_NONE = 0,
    TRACK_RD   = 0x01,
    TRACK_WR   = 0x02,
    TRACK_EXEC = 0x04,
};    


int Z80_Step (void);
Z80_Regs *Z80_GetRegs_Ref();
static Z80_Regs * z80_regs_ref = NULL; 

unsigned long rd_word(dword A)
{
    return memory[A] | (memory[A+1] << 8);
}

/* one line version of the registerdump found in z80c */ 
void Z80_RegisterDump_oneline(void)
{
    int i;
    Z80_Regs *R = z80_regs_ref; 
    printf(
	"AF:%04X HL:%04X DE:%04X BC:%04X PC:%04X SP:%04X IX:%04X IY:%04X ",
	R->AF.W.l,R->HL.W.l,R->DE.W.l,R->BC.W.l,R->PC.W.l,R->SP.W.l,R->IX.W.l,R->IY.W.l
	); 
    printf ("STACK: ");
    for (i=0;i<10;++i)
	printf("%04lX ", rd_word(R->SP.D + i * 2));
    puts("");
#ifdef TRACE
    puts ("PC TRACE:");
    for (i=1;i<=256;++i) printf ("%04X\n",pc_trace[(pc_count-i)&255]);
#endif
}




// When using Z80_Exec, this is called after IPeriod T-STates
// This might be a way of adding clock interrupts, but it appears
// to be called periodically whether we need one or not.
// May need to modify the z80 state if one is to be flagged,
// and then the return value of this function should probably
// be the interrupt vector (in IM 2).
// TODO: callbacks to python?
// TODO: log PC and potentially relevant flags
int Z80_Interrupt(void)
{
    if (trace_ops) {
        printf("Z80_interrupt: icount %4x iperiod %4x ", Z80_ICount, Z80_IPeriod);
        Z80_RegisterDump_oneline();
    }
    // TODO: this just disables any interrupt handling as Interrupt in Z80.c just returns..
    return Z80_IGNORE_INT;
    // return 0xff; 
}

// I/O calls a python callback (if present)
byte Z80_In(byte port)
{
    if (cb_io_in == NULL) {
        printf("Z80_In %02x\n", port);
        return 0; 
    }
    PyObject *args = Py_BuildValue("(i)", port);
    PyObject *res  = PyObject_CallObject(cb_io_in, args);
    Py_DECREF(args);

    if (NULL == res) {
        if (NULL == PyErr_Occurred()) {
            fprintf(stderr, "Error occurred in z80-in callback\n"); 
        } else {
            PyErr_Print();
        }
        return 0; 
    } 
    unsigned int rv = 0;
    rv = PyLong_AsLong(res); 
    byte bres = (byte) rv;
    if (PyErr_Occurred()) {
        PyErr_Print();
    }
    // printf("Got back from python %d\n", (int) bres);
    Py_DECREF(res);
    return bres;
}


void Z80_Out(byte port, byte value)
{
    if (cb_io_out == NULL) {
        printf("Z80_Out %02x -> %02x\n", value, port);
        return;
    }
    PyObject *args = Py_BuildValue("(ii)", port, value);
    PyObject *res  = PyObject_CallObject(cb_io_out, args);
    Py_DECREF(args);
    if (res) {
        Py_DECREF(res);
    }
}

unsigned Z80_RDMEM(dword A)
{
    unsigned val  = memory[A];
    if (memory_track[A] & TRACK_RD) {
	char txt[120];
	unsigned long pc = z80_regs_ref->PC.D; 
	Z80_Dasm(&memory[pc], txt, pc);
	printf("RD %4x -> %2x   --- icount %2x iperiod %2x pc %4lx instr: %s\n",
	       A, val, Z80_ICount, Z80_IPeriod, pc, txt);
    }
    if (trace_ops) {
        printf("RD %4x -> %2x   --- icount %x iperiod %x\n", A, val, Z80_ICount, Z80_IPeriod);
    }
    return val; 
}

void Z80_WRMEM(dword A, byte V)
{
    if (memory_track[A] & TRACK_WR) {
	char txt[120];
	unsigned long pc = z80_regs_ref->PC.D; 
	Z80_Dasm(&memory[pc], txt, pc);
	printf("WR %4x : %2x -> %2x   --- icount %2x iperiod %2x pc %4lx instr: %s\n",
	       A, memory[A], V, Z80_ICount, Z80_IPeriod, pc, txt);
    }
    if (memory_prot[A]) {
    //if (A < MONITOR_SIZE) {
        printf("WR---ignore write to protected addr %4x wr %2x\n", A, V);
        return;
    }
    if (trace_ops) {
        printf("WR %4x %2x -> %2x\n", A, memory[A], V);
    }
    memory[A] = V & 0xff;
}


// Set memory protection for a region
void mem_set_protect(dword start, dword end, byte V)
{
    printf("Setting memory protection for region 0x%x to 0x%x to %d\n", start, end, V);
    fflush(stdout);
    for (dword a = start; a <= end; a++) {
	memory_prot[a] = V; 
    }
}

void mem_set_track_mask(dword start, dword end, byte mask)
{
    for (dword a = start; a <= end; a++) {
	memory_track[a] |= mask; 
    }
}

void mem_unset_track_mask(dword start, dword end, byte mask)
{
    byte nmask = ~mask; 
    for (dword a = start; a <= end; a++) {
	memory_track[a] &= nmask; 
    }
}


void print_instr_at(int pc)
{
    char txt[120];
    int len = Z80_Dasm(&memory[pc], txt, pc);
    printf("[");
    for (int i = 0; i < len; i++)  {
        printf(" %02x", memory[pc + i]);
    }
    for (int i = len; i < 5; i++)  {
        printf("   ");
    }
    printf("]  %s", txt);
}


/* ------------- tst ---------------- */ 

static PyObject*
pytst(PyObject* self, PyObject* n)
{
    unsigned long nl = PyLong_AsUnsignedLong(n);
    PyObject *res = PyLong_FromUnsignedLong(42 * nl);
    return res;
}
PyDoc_STRVAR(tst_doc, "docstring for tst function");


/* ------------- dis ---------------- */ 
static PyObject*
pydis(PyObject* self, PyObject* args)
{
    unsigned char *memory;
    Py_ssize_t memsize;
    unsigned long pc; 

    // https://docs.python.org/3/c-api/arg.html
    if (!PyArg_ParseTuple(args, "s#l", &memory, &memsize, &pc)) {
        return NULL;
    }

    char txt[120];
    int len = Z80_Dasm(&memory[pc], txt, pc);
    
    PyObject *res = PyTuple_New(2);
    PyTuple_SetItem(res, 0, PyLong_FromUnsignedLong(len));
    PyTuple_SetItem(res, 1, PyUnicode_FromStringAndSize(txt, strlen(txt)));
    return res;
}
PyDoc_STRVAR(dis_doc,
             "dis(mem, pc)\n"
             "- mem is a bytes object\n"
             "- pc is the offset to the instruction.\n"
             "returns (nbytes, 'assembly instruction')");


/* ------------- step, run ---------------- */ 


static PyObject*
pystep(PyObject* self, PyObject* args)
{
    // unsigned char *memory;
    // Py_ssize_t memsize;
    // unsigned long pc;

    // printf("PC start  %x ", z80_regs_ref->PC.D);
    // print_instr_at(z80_regs_ref->PC.D);
    unsigned long running = Z80_Step();
    unsigned long pc = z80_regs_ref->PC.D;
    // printf(" - after %x\n",  z80_regs_ref->PC.D); 
    
    // char txt[120];
    // int len = Z80_Dasm(&memory[pc], txt, pc);
    
    PyObject *res = PyTuple_New(2);
    PyTuple_SetItem(res, 0, PyLong_FromUnsignedLong(running));
    PyTuple_SetItem(res, 1, PyLong_FromUnsignedLong(pc));
    return res;
}
PyDoc_STRVAR(pystep_doc,
             "step()\n"
             "Runs one step of the emulator. The results are returned in a tuple as:\n"
             "- running\n"
             "- PC\n");


unsigned long track_step()
{
    unsigned long pc = z80_regs_ref->PC.D;
    if (memory_track[pc] & TRACK_EXEC) {
	// Treat it like a breakpoint. Get the callback before executing the instruction
	char txt[120];
	// int len = Z80_Dasm(&memory[pc], txt, pc);
	Z80_Dasm(&memory[pc], txt, pc);
	printf("TRACK_EXEC: about to execut instruction at %02lx: %s\n", pc, txt);

	// TODO: if callback is registered, call it with the current context.
	// TODO: let the callback modify pc or something else? 
    }
    unsigned long running = Z80_Step();
    // unsigned long next = z80_regs_ref->PC.D;
    return running;
}


static PyObject*
pyrun(PyObject* self, PyObject* args)
{
    unsigned long n;
    if (!PyArg_ParseTuple(args, "l", &n)) {
        return NULL;
    }

    unsigned long running = 1; 
    for (unsigned long i = 0; (i < n || n == 0) && running; i++) {
	if (0) {
	    running = Z80_Step();
	} else {
	    running = track_step(); 
	}
	// This is necessary to support control-c from the module.
	if (PyErr_CheckSignals()) {
	    break;
	}
    }
    unsigned long pc = z80_regs_ref->PC.D;
    
    PyObject *res = PyTuple_New(2);
    PyTuple_SetItem(res, 0, PyLong_FromUnsignedLong(running));
    PyTuple_SetItem(res, 1, PyLong_FromUnsignedLong(pc));
    return res;
}
PyDoc_STRVAR(pyrun_doc,
             "run(n)\n"
             "Runs multiple steps in the emulator. If n=0, runs indefinitely.\n"
             "The results are returned in a tuple as:\n"
             "- running\n"
             "- PC\n");

/* ------------- set callbacks 
 * (from https://docs.python.org/3/extending/extending.html#calling-python-functions-from-c) 
 * ---------------- 
 */ 


static PyObject *
py_set_in_callback(PyObject *dummy, PyObject *args)
{
    PyObject *result = NULL;
    PyObject *temp;

    if (!PyArg_ParseTuple(args, "O:set_in_callback", &temp)) {
        return NULL;
    }
    
    if (!PyCallable_Check(temp)) {
        PyErr_SetString(PyExc_TypeError, "parameter must be callable");
        return NULL;
    }
    Py_XINCREF(temp);         /* Add a reference to new callback */
    Py_XDECREF(cb_io_in);     /* Dispose of previous callback */
    cb_io_in = temp;          /* Remember new callback */
    /* Boilerplate to return "None" */
    Py_INCREF(Py_None);
    result = Py_None;
    return result;
}

static PyObject *
py_set_out_callback(PyObject *dummy, PyObject *args)
{
    PyObject *result = NULL;
    PyObject *temp;

    if (PyArg_ParseTuple(args, "O:set_out_callback", &temp)) {
        if (!PyCallable_Check(temp)) {
            PyErr_SetString(PyExc_TypeError, "parameter must be callable");
            return NULL;
        }
        Py_XINCREF(temp);         /* Add a reference to new callback */
        Py_XDECREF(cb_io_out);     /* Dispose of previous callback */
        cb_io_out = temp;          /* Remember new callback */
        /* Boilerplate to return "None" */
        Py_INCREF(Py_None);
        result = Py_None;
    }
    return result;
}

/* -----------  read and write memory  ---------*/
static PyObject *
py_mem_rd(PyObject* self, PyObject* args)
{
    unsigned long addr;
    if (!PyArg_ParseTuple(args, "l", &addr)) {
        return NULL;
    }
    byte val = memory[addr];
    return Py_BuildValue("i", val);
}


static PyObject *
py_mem_wr(PyObject* self, PyObject* args)
{
    unsigned long addr;
    unsigned long val;
    if (!PyArg_ParseTuple(args, "ll", &addr, &val)) {
        return NULL;
    }
    memory[addr] = val & 0xff;

    Py_INCREF(Py_None);
    return Py_None;
}


static PyObject *
py_mem_set_prot(PyObject* self, PyObject* args)
{
    unsigned long start;
    unsigned long end;
    unsigned long val;

    if (!PyArg_ParseTuple(args, "lll", &start, &end, &val)) {
        return NULL;
    }

    mem_set_protect(start, end, val);

    Py_INCREF(Py_None);
    return Py_None;
}



static PyObject*
py_mem_dis(PyObject* self, PyObject* args)
{
    unsigned long pc; 

    // https://docs.python.org/3/c-api/arg.html
    if (!PyArg_ParseTuple(args, "l", &pc)) {
        return NULL;
    }
    
    char txt[120];
    int len = Z80_Dasm(&memory[pc], txt, pc);
    
    PyObject *res = PyTuple_New(2);
    PyTuple_SetItem(res, 0, PyLong_FromUnsignedLong(len));
    PyTuple_SetItem(res, 1, PyUnicode_FromStringAndSize(txt, strlen(txt)));
    return res;
}

static PyObject*
py_get_regs(PyObject* self, PyObject* args)
{
    Z80_Regs *regs = Z80_GetRegs_Ref();
    // https://docs.python.org/3.11/extending/extending.html#building-arbitrary-values
    PyObject *res = Py_BuildValue(
        "{s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i}",
        "PC", regs->PC.D,
        "SP", regs->SP.D,
        "AF", regs->AF.D,
        "BC", regs->BC.D,
        "DE", regs->DE.D,
        "HL", regs->HL.D,
        "IX", regs->IX.D,
        "IY", regs->IY.D,
        "AF2", regs->AF2.D,
        "BC2", regs->BC2.D,
        "DE2", regs->DE2.D,
        "HL2", regs->HL2.D,
        "IFF1", regs->IFF1,
        "IFF2", regs->IFF2,
        "HALT", regs->HALT,
        "IM", regs->IM,
        "I", regs->I,
        "R", regs->R,
        "R2", regs->R2
        );
    return res;
}
    
    

PyMethodDef methods[] = {
    {"tst",  (PyCFunction) pytst,  METH_O,       tst_doc},
    {"dis",  (PyCFunction) pydis,  METH_VARARGS, dis_doc},
    {"step", (PyCFunction) pystep, METH_NOARGS,  pystep_doc},
    {"run",  (PyCFunction) pyrun,  METH_VARARGS, pyrun_doc},
    {"set_in_callback",  (PyCFunction) py_set_in_callback,  METH_VARARGS, "set in_port callback"},
    {"set_out_callback", (PyCFunction) py_set_out_callback, METH_VARARGS, "set out_port callback"},
    {"mem_rd",           (PyCFunction) py_mem_rd,           METH_VARARGS, "read mem from running simulator"},
    {"mem_wr",           (PyCFunction) py_mem_wr,           METH_VARARGS, "write to running simulator's memory"},
    {"mem_set_prot",     (PyCFunction) py_mem_set_prot,     METH_VARARGS, "set protection of memory region. 0 = no protection, 1 = protected"},
    {"mem_dis",          (PyCFunction) py_mem_dis,          METH_VARARGS, "disassembly of instruction in running simulator's memory"},
    {"get_regs",         (PyCFunction) py_get_regs,         METH_VARARGS, "register dump/copy as a dictionary"},
    // {"mem_set_track_mask", (PyCFunction) mem_set_track_mask
    {NULL},
};

PyDoc_STRVAR(
    z80emu_module_doc,
    "Z80 emulator utils.\n" 
    "Use 'step' (single instruction) or 'run' (multiple) to execute the simulation.\n" 
    "\n" 
);

PyModuleDef z80emu_module = {
    PyModuleDef_HEAD_INIT,
    "z80emu",
    z80emu_module_doc,
    -1,
    methods,
    NULL,
    NULL,
    NULL,
    NULL
};


void read_file(char *fname, int offset)
{
    int fd = open(fname, O_RDONLY);
    if (fd < 0) {
        printf("Could not open prom file %s\n", fname);
        exit(fd); 
    }
    // monitor/bios starts at addr 0
    int br = read(fd, &memory[offset], MONITOR_SIZE);
    if (br != MONITOR_SIZE) {
        printf("Could not read the full size monitor: %d\n", br);
        exit(-1); 
    }
}

void init_memory()
{
    memset(memory, 0, MEM_SIZE);
    memset(memory_prot, 0, MEM_SIZE);
    memset(memory_track, TRACK_NONE, MEM_SIZE);
    // read_file("prom0.bin", 0); 
    // read_file("prom1.bin", 0x1000);
    // mem_set_protect(0, MONITOR_SIZE - 1, 1);
    // mem_set_protect(0x1000,  0x1000 + MONITOR_SIZE - 1, 1);
}


void restart()
{
    Z80_IRQ = 0;
    init_memory(); 
    Z80_Reset();
    z80_regs_ref = Z80_GetRegs_Ref();
}


PyMODINIT_FUNC PyInit_z80emu(void)
{
    restart(); 
    PyObject *module = PyModule_Create(&z80emu_module);
    PyModule_AddIntConstant(module, "TRACK_NONE", TRACK_NONE);
    PyModule_AddIntConstant(module, "TRACK_RD",   TRACK_RD);
    PyModule_AddIntConstant(module, "TRACK_WR",   TRACK_WR);
    PyModule_AddIntConstant(module, "TRACK_EXEC", TRACK_EXEC);
    // mem_set_track_mask(0, 0x1000, TRACK_WR);
    // test for cpm loading
    // mem_set_track_mask(0xee00, 0xffff, TRACK_EXEC);
    mem_set_track_mask(0x9000, 0xffff, TRACK_EXEC);
    return module;
}


