#!/usr/bin/env python3

"""
This uses Python to:
- control the emulation
- emulate some I/O devices (serial port and disk)
- scripted input (for easy debugging, testing and tracing)

TODO:
- Cleanups (this is a figure-it-out-hack)
- copy to pty for read/write (not just terminal and scripted)
- move debug output to logging, which can be to a (optionally specified) file.
- add serial ports for the serial card mymux is using.
- Check timer interrupt. Mymux etc may need it.
- better org and separation between cpu board and computer (with multiple board types)

Ideas:
- maybe move some of the emulation to C later to make this portable to smaller devices?
"""

import time
import sys
import select
import heapq
from collections import deque
from contextlib import contextmanager
from pathlib import Path
import argparse
import logging
from types import NoneType
from typing import dataclass_transform
import z80emu
from z80emu import set_in_callback, set_out_callback, mem_dis, mem_rd, get_regs, mem_set_prot
import diskimage
from emuconfig import read_config, read_console_script
from embedded_console import start_pty_console
from emu_logging import configure_logging
import emu_trace
from emu_trace import regs_stack_str, regs_str, pc_disasm_str, PC_OFFSET_STD_IO
from iodevice import IODevice, IOIgnore, IOPrint
from diskcontroller import IODiskController


log = logging.getLogger("mycron.status")
io_log = logging.getLogger("mycron.io")
disk_log = logging.getLogger("mycron.disk")

io_trace = logging.getLogger("mycron.trace.io")
disk_trace = logging.getLogger("mycron.trace.disk")


# Port 0x14 on the Z80 cards.
# This port is "reverse enginered" from tracing signals on the DIM-1003 motherboard.
# See the DIM-1003-mapping document for more details.
# - 74S288 mapping of io address space to chip selects.
#
# There is a series of chips involved, but it boils down to:
# - Writing a 1 to port 0x14 enables the PROM chips
# - Writing a 0 to port 0x14 disables the PROM chips
# - Writing a 2 to port 0x14 (bit 1) seems to do something related to signals, but
#   I haven't observed this so far.
#
# Reading from port 0x14 has a different meaning: it reads the LS245 octal bus tranceiver
# between the 8-bit dip switch and the CTC.
#
# Dip switch pins 3-8 are connected to an input buffer, corresponding to bit 2-7
# -> 9b would then mean switch  8+5+4+2+1 high. But I can't find the connections for pins 1-2,
#    maybe something else than the dip switch.
#    Alternatively, the dip switches are grounded, so it may be a pull-up signal.
#    Looks like there might be a resistor pack connected
#    along the lines up to the input side of the port
#
# Observed behaviour:
#
# The monitor prom writes a 1 to it and then reads 0x9b.
# - The CPM boot programs write a 0 to this port to turn off the PROMS.
# - There is a MYCROP.COM program on one of the CPM floppies that writes a 1 to this port and
#   then jumps back to mycrop.
# - Some of the CPM booting code temporarily turns on the PROMS to run support functions
#   and then turns them off again.
#
class IOP14(IODevice):
    Ax14 = 0x14
    Ax15 = 0x15
    Ax16 = 0x16
    Ax17 = 0x17
    PORTS = (Ax14, Ax15, Ax16, Ax17)

    # TODO: the default rval seems to influence the values written to IOCTC port 2: 80 with 9b, 20 with 1b
    def __init__(self, board, default_rval=0x9b):
        super().__init__(default_rval=default_rval)
        self.board = board
        io_log.info(f"IO14 device initialized with default rval={self.default_rval:#x}. TODO: see comments.")

    def write(self, port, val):
        emu_trace.write(f"IO14_OUT [{port:02x}] = {val:02x}", pc_offset=PC_OFFSET_STD_IO)
        self.board.set_proms_enabled(val & 1)

    def read(self, port):
        emu_trace.write(f"IO14_INP [{port:02x}] : 0x{self.default_rval:x}", pc_offset=PC_OFFSET_STD_IO)
        return self.default_rval


class IOCTC(IOIgnore):
    """Counter/Timer
    Control word (from z80 CTC datasheet):
    - D7 - interrupt (1=enable)
    - D6 - Mode (0 = timer mode, 1 counter mode)
    - D5 - prescaler value* (1 = 256, 0=16)
    - D4 - CLK/TRQ edge selection (0 = falling edge, 1 = rising edge)
    - D3 - Timer trigger* (0 = automatic trigger when time constant is loaded, 1 = clk/trg pulse starts timer)
    - D2 - Time constant (0 = no time constant follows, 1 time constant follows)
    - D1 - RESET (0 = continued operation, 1 = software reset)
    - D0 - Control or vector (0 = vector, 1 = control word)
    * Timer mode only

    Time constant word:
    D7-D0 -> TC7-TC0
    - 0 is interpreted as 256
    - time interval is CLK (system clock) * P (prescaler factor) * T (Time constant)
      - max time is 256 * CLK * 256, which is 16.4ms with a 4MHz clock

    Interrupt vector word:
    - D7-D3 - V7-V3 supplied by user
    - D2-D1 - Channel ident. Automatically inserted by CTC
    - D0 - (0 = interrupt vector word, 1 = control word)

    """
    CH0 = 0x0
    CH1 = 0x2
    CH2 = 0x1
    CH3 = 0x3
    PORTS = (CH0, CH1, CH2, CH3)

    def write(self, port, val):
        emu_trace.write(f"IOCTC OUT [{port:02x}] = {val:02x}", pc_offset=PC_OFFSET_STD_IO)
        if val & 0x1:
            # control
            if val & 0x80:
                io_log.warning("-- NB: wanted interrupt")
    def read(self, port):
        emu_trace.write(f"IOCTC INP [{port:02x}] : 0x{self.default_rval:x}", pc_offset=PC_OFFSET_STD_IO)
        return self.default_rval


class IOPar(IOIgnore):
    """Parallel I/O"""
    AD = 0x4   # Port A data
    BD = 0x5   # Port B data
    AC = 0x6   # Port A control
    BC = 0x7   # Port B control
    PORTS = (AC, AD, BC, BD)

    def write(self, port, val):
        emu_trace.write(f"IOPAR OUT [{port:02x}] = {val:02x}", pc_offset=PC_OFFSET_STD_IO)
    def read(self, port):
        emu_trace.write(f"IOPAR INP [{port:02x}] : 0x{self.default_rval:x}", pc_offset=PC_OFFSET_STD_IO)
        return self.default_rval


# TODO: generalize to support more UARTs / serial boards.
# - Mymux needs a 4-port serial card for the multicomputer code.
# The manual is a bit messy. You have to read through loads of stuff of how to do things before
# it tells you how to read and write to the various registers. It would have been nice to know
# that _before_ talking about the sequences of the registers.
# With the exception of WR0, each register write requires two bytes:
# - one byte with D2-D0 selecting a register
# - the next byte with the value for the register
# z80 peripherals page 293 (of 330)
#
class IOSerial(IODevice):
    """Serial ports on the DIM-1003.
    Not sure what port A is used for or whether it is actually used.
    Port B is the one that seems to be mapped to c-onsole I/O.
    """
    BC = 0xf
    BD = 0xe
    AC = 0xd
    AD = 0xc
    PORTS = (AC, AD, BC, BD)

    def __init__(self, output, **kvals):
        super().__init__(**kvals)
        self.output = output
        self.input_bytes = deque()
        self.scheduled_input = []
        self.started_at = time.monotonic()
        # next register to write if write-reg contains data pointers
        self.next_reg = {
            self.AC: 0,
            self.BC: 0
        }

    def queue_bytes(self, data):
        """Queue bytes delivered"""
        if isinstance(data, str):
            data = data.encode("ascii")
        self.input_bytes.extend(data)

    def schedule_bytes(self, delay, data):
        """Queue bytes to be delivered after 'delay' seconds"""
        if isinstance(data, str):
            data = data.encode("ascii")
        heapq.heappush(
            self.scheduled_input,
            (float(delay) + time.monotonic(), bytes(data)))

    def _release_scheduled_input(self):
        tnow = time.monotonic()
        while (self.scheduled_input
               and self.scheduled_input[0][0] <= tnow):
            _, data = heapq.heappop(self.scheduled_input)
            self.input_bytes.extend(data)

    def _write_serial_ctrl(self, port, val):
        """Emulate write to console serial port's ctrl register"""
        # A bit clumsy as a first take on the register write sequences
        # This is incomplete and is just there to detect if something interesting is set up on the serial channel.
        if val != 0 or self.next_reg[self.BC] > 0:
            # 0 is typically used when polling
            emu_trace.write(f"IOSER  OUT[{port:2x}] = {val:2x},  ser_BC reg was {self.next_reg[self.BC]:2x}: ",
                            pc_offset=PC_OFFSET_STD_IO)
            # io_trace.debug(f"IOSER write CB reg {self.next_reg[self.BC]} - {port:2x} {val:2x}")
            if self.next_reg[self.BC] == 0:
                if val & 0x7 > 0:
                    # io_trace.debug(f"IOSER --- next reg is {val&0x7}")
                    self.next_reg[self.BC] = val & 0x7
            else:
                self.next_reg[self.BC] = 0
        else:
            self.next_reg[self.BC] = 0


    def write(self, port, val):
        """Emulate a write to a console"""
        match port:
            case self.BD:
                # Emulate write to console (sio B).
                if self.output is not None:
                    self.output.write(bytes([val]))
                    self.output.flush()
            case self.BC:
                self._write_serial_ctrl(port, val)

    def read(self, port):
        self._release_scheduled_input()

        if port == self.BC:
            # For polling mode, just indicate that it's always ready to transmit more (4) +
            # whether there is data queued (1 or 0).
            return 4 | bool(self.input_bytes)
        if port == self.BD:
            if self.input_bytes:
                return self.input_bytes.popleft()
            io_log.warning(f"read from empty serial port {port}. Returning 0 {pc_disasm_str()}")
            return 0

        io_log.warning(f"({self}): unknown port {port}. Supporting one of {self.BD=} {self.BC=} {pc_disasm_str()}")
        return 0


class IOSerialDim1001(IOSerial):
    # The serial port for the console on DIM 1001 uses a different UART and different ports.
    # TODO: __init__ might reference parent vals
    BC = 1
    BD = 0
    PORTS = (BC, BD)



def check_console(board, ch_in, poller):
    # Keyboard / Console input
    if not poller.poll(0):
        return

    data = ch_in.read(1)
    if not data:
        poller.unregister(ch_in.fileno())
        return

    # The console doesn't like 8-bit ascii, so limit it to 7-bit.
    ch = data[0] & 0x7f

    if ch == 0xa:
        # doesn't understand newline (\n), so translate to CR (\r)
        ch = 0xd

    board.sport.queue_bytes(bytes([ch]))


# Used to pause and continue the simulator.
sim_paused = False

def sim_pause():
    global sim_paused
    sim_paused = True
    log.info("Sim paused")

def sim_cont():
    global sim_paused
    sim_paused = False
    log.info("Sim continued")


@contextmanager
def sim_paused_context():
    """Safer handling of sim state"""
    old_state = sim_paused
    try:
        sim_pause()
        yield
    finally:
        if not old_state:
            # should be un-paused
            sim_cont()


def run_sim(board, ch_in, ch_in_p, steps_per_chunk=1000):
    """Starts the simulator
    steps_per_chunk is how many steps the z80 C library should run before returning to Python
    for another iteration. Too many steps means the console and some other functions
    (like the console) may become less responsive. Too few steps add overhead.
    """
    N = 3_550_000     # report on performance once after >= N iterations
    tstart = time.time()
    iters = 0
    while True:
        if sim_paused:
            time.sleep(0.1)
            continue
        check_console(board, ch_in, ch_in_p)
        z80emu.run_steps(steps_per_chunk)

        # Performance monitoring at startup
        iters += steps_per_chunk
        if iters > N and tstart > 0:
            tstop = time.time()
            log.info(f"Ran {iters:_} steps in {tstop-tstart:.3f} seconds ({iters/(tstop-tstart):_} steps/s)")
            tstart = 0  # disable


def dump_mem(fname):
    """Write a memory dump to a file"""
    print(f"Dumping memory to {fname}. Pausing simulator.")
    with sim_paused_context():
        Path(fname).write_bytes(z80emu.memory_snapshot())
        print(f" - done dumping memory to {fname}. Restoring pause state of simulator.")


class PromRegion:
    """Emulates PROM chips as well as the memory covering the same address space when PROM chips are turned off.
    Some Z80 programs (like the CPM loaders) turn off the PROM region, leaving RAM to cover the same region.
    The tricky thing is that some functions can flip the PROM back on and off again to temporarily run
    support functions from the PROM.
    This class handles the logic of emulating the PROM flipping by keeping track of both RAM and PROM data
    and updating the memory view depending on the current PROM configuration.
    """
    def __init__(self, fname, start_addr):
        self.fname = fname
        self.start_addr = start_addr
        self.raw_data = open(fname, 'rb').read()
        self.ram_vals = bytes(len(self.raw_data))
        self.is_on = False
        self.turn_on()

    def __len__(self):
        return len(self.raw_data)

    def _write_region(self, data, backup=False, protect=0):
        """Writes data to the region, optionally storing the old values to self.ram_vals"""
        mem = z80emu.raw_memory()
        rng = slice(self.start_addr, self.start_addr + len(self))
        if backup:
            self.ram_vals = bytes(mem[rng])
        if data is not None:
            # Only update memory and set protection if data is there. Used by save_ram
            mem[rng] = data
            mem_set_prot(self.start_addr, self.start_addr + len(self), protect)

    def set_enabled(self, enabled):
        enabled = bool(enabled)

        if enabled == self.is_on:
            # No change
            return

        if enabled:
            # RAM is visible. Save it before replacing it with PROM.
            self._write_region(self.raw_data, backup=True, protect=1)
        else:
            # Restore the saved RAM.
            self._write_region(self.ram_vals, backup=False, protect=0)

        self.is_on = enabled

    def turn_on(self):
        self.set_enabled(True)

    def turn_off(self):
        self.set_enabled(False)


class Board:
    def __init__(self, config, d_imgs, console_output):
        self.config = config
        self.d_imgs = d_imgs
        self.console_output = console_output
        # Set up I/O address space
        self.io_ports = {}
        self.unknown_io = IOPrint()
        self.proms = []

    def set_proms_enabled(self, enabled):
        log.info("-- NB: Board setting proms to %s", "enabled" if enabled else "disabled")
        for prom in self.proms:
            prom.set_enabled(enabled)

    def io_in(self, port):
        return self.io_ports.get(port, self.unknown_io).read(port)

    def io_out(self, port, val):
        self.io_ports.get(port, self.unknown_io).write(port, val)


class Board1001(Board):
    def __init__(self, config, d_imgs, console_output):
        super().__init__(config, d_imgs, console_output)
        self.sport = IOSerialDim1001(console_output)
        self.sport.register_ports(self.io_ports)

        # Dim 1001 uses only one prom chip.
        self.proms.append(PromRegion(Path(config['run-dir'], config['prom0']), 0x0))

        self.dsk = IODiskController(d_imgs)
        self.dsk.register_ports(self.io_ports)


class Board1003(Board):
    def __init__(self, config, d_imgs, console_output):
        super().__init__(config, d_imgs, console_output)
        self.sport = IOSerial(console_output)

        self.sport.register_ports(self.io_ports)
        self.pport = IOPar()
        self.pport.register_ports(self.io_ports)

        self.ctcdev = IOCTC()
        self.ctcdev.register_ports(self.io_ports)

        self.iop14 = IOP14(self)
        self.iop14.register_ports(self.io_ports)

        # Dim 1003 uses two proms. The second one is mapped at 0x1000, leaving a region of RAM between the chips.
        self.proms.append(PromRegion(Path(config['run-dir'], config['prom0']), 0x0))
        self.proms.append(PromRegion(Path(config['run-dir'], config['prom1']), 0x1000))

        self.dsk = IODiskController(d_imgs)
        self.dsk.register_ports(self.io_ports)


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("-t", help="Console I/O. Use if input and output are to the same device.")
    parser.add_argument("-ti", help="Console input")
    parser.add_argument("-to", help="Console output")
    parser.add_argument("--script", metavar="FILE", help="Console script file; overrides the run-directory configuration")
    parser.add_argument("--send", metavar="TEXT", help="Script to send to the console after startup.")
    # parser.add_argument("--disk", nargs='+')
    parser.add_argument("-c", default="run-tst", help="Config directory path (and where disk images are stored)")
    parser.add_argument("-ec", help="path to connect embedded python console to")
    args = parser.parse_args(argv)
    return args

def make_config(argv=None):
    """Reads config file and adds information from args to the config"""
    args = parse_args(argv)
    if args.t:
        # Use the same pty for both input and output
        args.ti = args.t
        args.to = args.t

    if (not args.ti) or (not args.to):
        raise SystemExit(f"Please specify -t, or specify both -ti and -to")

    config = read_config(args.c)

    # Maybe not the cleanest yet, but this is a step towards a single
    # config environment without having to deal with arg parsing.
    config['run-dir'] = args.c
    config['console_in'] = args.ti
    config['console_out'] = args.to
    config['script_arg']      = args.script
    config['send']        = args.send
    config['embedded_console'] = args.ec
    log.debug(config)
    return config


def schedule_config_script(board, config):
    script_name = config.get("script")
    if not script_name:
        return

    script_path = Path(config["run-dir"]) / script_name
    log.info("Loading console script %s", script_path)

    for delay, data in read_console_script(script_path):
        board.sport.schedule_bytes(delay, data)


BOARD_TYPES = {
    'dim-1001' : Board1001,
    'dim-1003' : Board1003
}


def main():
    configure_logging(trace_enabled=True)
    config = make_config()

    # TODO: Hack since there is currently no clean way of failing if a
    # non-existing disk is requested in the controller. The emulator currently
    # just crashes.
    # This adds some default images, with write-through if any image is modified.
    # Non-existing images will be created on the disk on the first write.
    log.info(f"Using disk image(s) from {config['disk-images']}")
    d_imgs = [diskimage.DiskImage.from_file(dname) for dname in config['disk-images']]

    if len(d_imgs) > 8:
        raise ValueError(f"disk controller supports at most 8 images, got {len(d_imgs)}")

    for drivenum in range(len(d_imgs), 8):
        path = Path(config['run-dir']) / f"disk-{drivenum:02}.img"
        d_imgs.append(diskimage.DiskImage.empty_image(path))

    with open(config["console_in"], "rb", buffering=0) as console_in, \
         open(config["console_out"], "wb", buffering=0) as console_out:

        if (board_type := BOARD_TYPES.get(config['board'], None)) is None:
            supported = ", ".join(sorted(BOARD_TYPES))
            raise ValueError(f"unsupported board {config['board']!r}; expected one of: {supported}")

        board = board_type(config, d_imgs, console_out)

        set_in_callback(board.io_in)
        set_out_callback(board.io_out)

        if config['send']:
            board.sport.schedule_bytes(0.3, config['send'])
        if config['script_arg']:
            for delay, data in read_console_script(config['script_arg']):
                board.sport.schedule_bytes(delay, data)
        schedule_config_script(board, config)

        # Slightly hacky, but this lets us read from the serial port and put it in the
        # queue of the emulator's serial port.
        # NB: need to set it to binary + unbuffered, otherwise terminal input will be buffered and not work properly
        console_in_p = select.poll()
        console_in_p.register(console_in.fileno(), select.POLLIN)

        # If embedded console:
        if (ec_fn := config['embedded_console']):
            # Give it everything
            console_server = start_pty_console(ec_fn, globals() | locals())

        # track cpm loading
        # z80emu.mem_set_track_mask(0xee00, 0xffff, z80emu.TRACK_EXEC)
        try:
            run_sim(board, console_in, console_in_p)
        except KeyboardInterrupt:
            print("\nEmulator stopped by keyboard interrupt.", file=sys.stderr)
            return 130  # interrupted by SIGINT (128 + SIGINT = 2)

    return 0


if __name__ == "__main__":
    z80emu.reset()
    raise SystemExit(main())
