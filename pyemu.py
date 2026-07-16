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
import z80emu
from z80emu import set_in_callback, set_out_callback, mem_dis, mem_rd, get_regs, mem_set_prot
import diskimage
from emuconfig import read_config
from embedded_console import start_pty_console

# most instructions of the type  IN A, OUT A should be 2 bytes.
# This should cover most io cases
PC_OFFSET_STD_IO = -2


def regs_str(regs=None):
    """Returns a dict with regs and a string rep of regs with hex values"""
    rnames = ['PC', 'SP', 'AF', 'BC', 'DE', 'HL']
    if regs is None:
        regs = get_regs()
    vals = ",".join(
        f"{name}={regs[name]:x}"
        for name in rnames
        if name in regs)
    return f"REGS_hex({vals})"


def regs_stack_str():
    regs = get_regs()
    s = regs_str(regs)
    sp = regs['SP']
    s += " Stack: ["
    s += " ".join([f"{mem_rd(sp + i):02x}" for i in range(10)])
    s += "]"
    return s


def pc_disasm_str(pc_offset=0):
    """
    Return a short string with current PC and disassembly at PC.

    Note: during an IN/OUT callback, depending on the Z80 backend, PC may
    already point at the next instruction rather than the IN/OUT instruction.
    Still useful for context.

    To address this, you can add pc_offset=PC_OFFSET_STD_IO, which should address
    most cases.
    """
    try:
        regs = get_regs()
        pc = (regs["PC"] + pc_offset) & 0xffff
        offs = "" if pc_offset == 0 else f" PC offset {pc_offset}"
        _, asm = mem_dis(pc)
        return f"PC={pc:04x} {asm:14}{offs}"
    except Exception as e:
        return f"PC=???? <disasm failed: {e}>"


def trace_write(msg, include_regs=False, include_stack=False, pc_offset=0):
    """Trace write..
    NB: see notes about pc_disasm_str(). It might return the _next_ instruction.
    """
    s = f"{msg} {pc_disasm_str(pc_offset)}"
    if include_stack:
        # also includes regs
        s += "  " + regs_stack_str()
    elif include_regs:
        s += "  " + regs_str()
    print(s)


class IODevice:
    PORTS = ()
    def __init__(self, default_rval=0):
        self.default_rval = default_rval

    def write(self, port, val):
        pass

    def read(self, port):
        return self.default_rval

    def register_ports(self, port_registry, ports=None):
        """Register this io device to the port list in the class or the provided port list"""
        for p in (self.PORTS if ports is None else ports):
            port_registry[p] = self


class IOIgnore(IODevice):
    """Ignores read or write requests, always returning a 0 if
    a write is requested"""


class IOPrint(IODevice):
    """Ignores read or write requests, always returning a 0 if
    a write is requested.
    Additionally prints OUT and INP to the console for debugging."""
    def write(self, port, val):
        trace_write(f"OUT [{port:02x}] = {val:02x}", pc_offset=PC_OFFSET_STD_IO)

    def read(self, port):
        trace_write(f"INP [{port:02x}] : 0x{self.default_rval:x}", pc_offset=PC_OFFSET_STD_IO)
        return self.default_rval


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
        print(f"IO14 device initialized with default rval={self.default_rval:#x}. TODO: see comments.")

    def write(self, port, val):
        trace_write(f"IO14_OUT [{port:02x}] = {val:02x}", pc_offset=PC_OFFSET_STD_IO)
        self.board.set_proms_enabled(val & 1)

    def read(self, port):
        trace_write(f"IO14_INP [{port:02x}] : 0x{self.default_rval:x}", pc_offset=PC_OFFSET_STD_IO)
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
        trace_write(f"IOCTC OUT [{port:02x}] = {val:02x}", pc_offset=PC_OFFSET_STD_IO)
        if val & 0x1:
            # control
            if val & 0x80:
                print("-- NB: wanted interrupt")
    def read(self, port):
        trace_write(f"IOCTC INP [{port:02x}] : 0x{self.default_rval:x}", pc_offset=PC_OFFSET_STD_IO)
        return self.default_rval


class IOPar(IOIgnore):
    """Parallel I/O"""
    AD = 0x4   # Port A data
    BD = 0x5   # Port B data
    AC = 0x6   # Port A control
    BC = 0x7   # Port B control
    PORTS = (AC, AD, BC, BD)

    def write(self, port, val):
        trace_write(f"IOPAR OUT [{port:02x}] = {val:02x}", pc_offset=PC_OFFSET_STD_IO)
    def read(self, port):
        trace_write(f"IOPAR INP [{port:02x}] : 0x{self.default_rval:x}", pc_offset=PC_OFFSET_STD_IO)
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
            trace_write(f"IOSER  OUT[{port:2x}] = {val:2x},  ser_BC reg was {self.next_reg[self.BC]:2x}: ", pc_offset=PC_OFFSET_STD_IO)
            # print(f"IOSER write CB reg {self.next_reg[self.BC]} - {port:2x} {val:2x}")
            if self.next_reg[self.BC] == 0:
                if val & 0x7 > 0:
                    # print(f"IOSER --- next reg is {val&0x7}")
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
            print(f"WARNING: read from empty serial port {port}. Returning 0 {pc_disasm_str()}")
            return 0

        print(f"WARNING ({self}): unknown port {port}. Supporting one of {self.BD=} {self.BC=} {pc_disasm_str()}")
        return 0


class IOSerialDim1001(IOSerial):
    # The serial port for the console on DIM 1001 uses a different UART and different ports.
    # TODO: __init__ might reference parent vals
    BC = 1
    BD = 0
    PORTS = (BC, BD)



class DiskDrive:
    # Based on dim-1030 info
    SECTOR_D_SIZE = 128
    MARK_HDR = 0xfe
    MARK_DATA = 0xfb

    ST_INACTIVE = "inactive"
    ST_HDR = "hdr"
    ST_DATA = "data"

    def __init__(self, dno, image):
        self.disk_img = image
        self.dno = dno
        self.track = 0
        self.sector = 1
        self.reset()

    def reset(self):
        # when reading, positions in header or data
        self.hpos = -1
        self.dpos = -2
        self.mark_seen_by_status = False
        self.set_at_state(True, False)   # Before first read, there is an address mark
        self.state = self.ST_INACTIVE

    def set_at_state(self, at_mark, at_crc):
        self.at_mark = at_mark
        self.at_crc = at_crc

    def start(self):
        """Called before first read of sector head or sector data"""
        self.mark_seen_by_status = False
        match self.state:
            case self.ST_INACTIVE:
                self.state = self.ST_HDR
                self.set_at_state(True, False)

            case self.ST_HDR:
                self.state = self.ST_DATA
                # NB: Have to set the mark here as read_hdr doesn't try to consume bytes, it only waits for status.
                # This is probably a side effect of the way the controller is observing the spinning disk and that they
                # didn't use the same scan_start function for both parts. Old assembly coding....
                self.set_at_state(True, False)

            case self.ST_DATA:
                self.state = self.ST_INACTIVE
                self.set_at_state(False, False)

    def status_read(self):
        # Called by the controller when STATUS is read.
        # If the CPU has actually seen the address-mark bit, the next data read
        # may return the mark byte.
        if self.at_mark:
            self.mark_seen_by_status = True

    def done(self):
        return self.dpos > self.SECTOR_D_SIZE + 2

    def _read_header(self):
        # DIM-1001 does:
        #   IN DATA     ; dummy/prime read
        #   IN STATUS   ; sees AM
        #   IN DATA     ; expects FE
        #
        # DIM-1003 does:
        #   IN STATUS   ; sees AM
        #   IN DATA     ; expects FE
        #
        # Therefore, a data read before the AM status has been observed must not
        # consume the FE header mark.
        if self.hpos == -1 and self.at_mark and not self.mark_seen_by_status:
            return 0

        self.hpos += 1
        # corresponds to the header part of budr
        match self.hpos:
            case 0:
                self.set_at_state(True, False)
                return self.MARK_HDR
            case 1:
                self.set_at_state(False, False)
                return self.track
            case 2:
                return 0
            case 3:
                return self.sector
            case 4:
                # next to read is crc, so set it here
                self.set_at_state(False, True)
                return 0
            # crc checksum - report it as ok
            case 5:
                return 0
            case 6:
                return 0
        # emulate the 17 0 bytes after the crc
        self.set_at_state(False, False)
        if self.hpos == 16:
            # prepare for the next to be the mark
            self.set_at_state(True, False)
        return 0

    # After reading a header, if it doesn't want to read that data packet, it just runs another 41 49
    # and scans until it finds the header address mark. This means that the above state machine should notice
    # the data done state and skip to the next...
    def _read_data(self):
        # - The prom only checks status for every second byte read until it finds address mark.
        #   read+check, read, read+check...
        # - After seeing the addressmark flag, it reads a single byte that is later used as return value
        #   with some bit fiddling  (basically, it looks like 0xfb address mark would give a return value of 0)
        if self.dpos < -1:
            # read-data always reads one byte before the mark is set
            self.dpos += 1
            self.set_at_state(True, False)
            return 0

        if self.dpos == 0:
            # Make sure there is a freshly read sector here (to enable swapping disks)
            # TODO: could perhaps just move this to "start"
            print(f"RD_SECTOR dsk={self.dno} sector {self.track:02}.{self.sector:02} .. {pc_disasm_str(PC_OFFSET_STD_IO)}")
            self.data = self.disk_img.read_sector(self.track, self.sector)

        if self.dpos < self.SECTOR_D_SIZE:
            if self.dpos < 0:
                ret = self.MARK_DATA
            else:
                self.set_at_state(False, False)
                ret = self.data[self.dpos]
            self.dpos += 1
            if self.dpos == self.SECTOR_D_SIZE:
                self.set_at_state(False, True)
                # print(f"DSK_READ_SECT_COMPLETE {self.track:02}.{self.sector:02} : {self.data} {pc_disasm_str(PC_OFFSET_STD_IO)}")
            return ret

        # TODO: potential for infinite loop, but prom seems to start a new read after crc
        self.dpos += 1
        return 0

    def read(self):
        """Reads one byte."""
        # print("DSK_read", self)
        match self.state:
            case self.ST_HDR:
                return self._read_header()
            case self.ST_DATA:
                return self._read_data()
        return 0

    def prepare_write(self):
        """Can't write to a byte buffer directly, so make this temp buffer first and we can apply it later"""
        self.wbuf = []

    def commit_write(self):
        if len(self.wbuf) != self.SECTOR_D_SIZE:
            raise RuntimeError(
                f"expected {self.SECTOR_D_SIZE} bytes, got {len(self.wbuf)}"
            )
        self.data = bytes(self.wbuf)
        # print(f"DSK_WRITE_SECT_COMPLETE {self.track:02}.{self.sector:02} : {self.data}")
        self.disk_img.write_sector(self.track, self.sector, self.data, flush=True)

    def write_add_byte(self, bval):
        if len(self.wbuf) >= self.SECTOR_D_SIZE:
            raise RuntimeError("sector write buffer overflow")
        self.wbuf.append(bval)

    def __repr__(self):
        return f"Disk({self.dno}, {self.track:02}.{self.sector:02}, m={self.at_mark}, crc={self.at_crc}, {self.state}, {self.hpos}, {self.dpos})"

    def set_sector(self, track, sector):
        self.track = track
        self.sector = sector


class IODiskController(IODevice):
    # Based on information from the DIM-1030 disc controller.
    O_CW1 = 0x88
    O_CW2 = 0x89
    O_DATA = 0x8a
    I_STATUS = 0x98
    I_DATA = 0x9a
    PORTS = (O_CW1, O_CW2, O_DATA, I_STATUS, I_DATA)
    pnames = {
        O_CW1 : "O_CW1",
        O_CW2 : "O_CW2",
        O_DATA : "O_DATA",
        I_STATUS : "I_STATUS",
        I_DATA : "I_DATA",
    }

    # from 1030 manual
    STATUS_FI  = 0x80    # 1 when FILE INOPERABLE is sent from drive
    STATUS_IXM = 0x40    # 1 when hard index mark is detected
    STATUS_CRC = 0x20    # 1 when CRC is ok
    STATUS_T0  = 0x10    # 1 when drive is at track Zero
    STATUS_A1  = 0x08    # Displays which addressmark has been
    STATUS_A0  = 0x04    # detected.
    STATUS_AM  = 0x02    # 1 when addressmark is dtected
    STATUS_DRY = 0x01    # 1 when drive is ready

    # not including the NC signals.
    # n : signal on low
    CW1_WR     = 0x80
    CW1_nLD    = 0x40
    CW1_WG     = 0x20
    CW1_nRAM   = 0x08
    CW1_CRC_ON = 0x04
    CW1_CRC_1  = 0x02
    CW1_CRC_0  = 0x01

    CW2_FI_RES  = 0x80
    CW2_LH      = 0x40
    CW2_S       = 0x20
    CW2_DIR_SEL = 0x10
    CW2_DRIVE_2 = 0x04
    CW2_DRIVE_1 = 0x02
    CW2_DRIVE_0 = 0x01

    RD_ST_OFF = "off"
    RD_ST_1   = "s1"   # prepare for run
    RD_ST_RUN = "run"  # running - can read data from sector

    WR_ST_OFF = "off"
    WR_ST_1   = "w1"
    WR_ST_2   = "w2"
    WR_ST_3   = "w3"
    WR_ST_4   = "w4"
    WR_ST_DATA = "wdata"  # next up is a stream of data on the input

    N_TRACKS = diskimage.TRACKS   # track 0..76
    N_SECTS = diskimage.SECTORS   # sector 1..26

    verbose = False

    def __init__(self, disk_imgs, **kvals):
        super().__init__(**kvals)
        self.disk_imgs = disk_imgs
        # a particular sequence of writes to a specific port will move the head out or in.
        # state can
        # - always be ok + whether track is 0
        # - mark if at the mark positions on each sector
        # - crc ok when passing the crc bytes.
        # data reads:
        # - fake head and data mark ids as well as sector and track numbers
        self.drive_no = 0
        # TODO: maybe move track and sector to the drive
        self.track = 0
        self.sector = 1
        self.drive = None
        self.rd_state = self.RD_ST_OFF    # 0 if not spitting out a sector, 1 if warming up and 2 if last init command issued
        self.wr_state = self.WR_ST_OFF
        self.drives = {i : DiskDrive(i, img) for i, img in enumerate(self.disk_imgs)}

    def set_sector(self, dno, track, sector):
        self.drive = self.drives[dno]
        self.drive.set_sector(track, sector)

    def pname(self, port):
        return self.pnames.get(port, "?? unk ??")

    def _next_sector(self):
        if self.drive is not None:
            self.drive.reset()
            # The start selects the next (if necessary) sector
            if self.drive.track != self.track:
                self.sector = 1
            else:
                self.sector += 1
                if self.sector > self.N_SECTS:
                    self.sector = 1

    def write_cw1(self, val):
        # print(f"DSK_WRITE_CW1 f{val:#02x}")
        match val:
            # NB: both read_hdr and read__data run the 41 49 sequence!
            case 0x41:
                self.rd_state = self.RD_ST_1
            case 0x49:
                self.rd_state = self.RD_ST_RUN
                if self.drive is None:
                    print("WARNING: drive was none in write_cw1")
                    self.drive = self.drives[self.drive_no]
                # TODO: self.drive should never be none here.
                if self.drive.track != self.track or self.drive.sector != self.sector:
                    self.drive.set_sector(self.track, self.sector)
                    self.drive.reset()
                if self.drive.state == DiskDrive.ST_DATA:
                    # About to make int inactive, so pick the next sector
                    self._next_sector()
                    self.drive.set_sector(self.track, self.sector)
                    self.drive.reset()
                self.drive.start()
            case 0xc9:
                # C9 is WR=1, /LD=1, WG=0,
                # see dsk_write_sector_data. A write sector starts with
                # C9 to CW1, then C0 to CW2
                # print("TODO: Trying disk write (C9 to CW1)")
                self.wr_state = self.WR_ST_1
            case 0xa1:
                self.wr_state = self.WR_ST_3
                # print("TODO: wr_state now", self.wr_state)
            case 0xa8:
                self.wr_state = self.WR_ST_4
                # print("TODO: wr_state now", self.wr_state)
            case 0xa9:
                self.wr_state = self.WR_ST_DATA
                # print("TODO: wr_state now", self.wr_state)
                # Fetch current sector and prepare it for writing
                self.set_sector(self.drive_no, self.track, self.sector)
                self.drive.prepare_write()
            case 0xad:
                self.wr_state = self.WR_ST_OFF
                # print("TODO: wr_state now", self.wr_state, 'Write done, so commiting and ignoring the rest')
                self.drive.commit_write()


    # When trying to set down head, it might try hex: 41, 51, 71, 51, then 01 when it gives up.
    # The last nibble 1 is for drive 1.
    # This is where the controller seelcts which disk to work with for the next commands.
    # CW2 writes. 50, 70, 50 -> step d1,  60, 40 ->  step d0
    def write_cw2(self, val):
        self.drive_no = val & 7
        self.drive = self.drives[self.drive_no]
        # Simplified. Should really look for the sequence
        if (val & 0x70) == 0x70:
            # Next track
            self.track = min(self.track + 1, self.N_TRACKS - 1)
            self.sector = 1
            self.drive.reset()
        elif (val & 0x60) == 0x60:
            # Prev track
            self.track = max(self.track - 1, 0)
            self.sector = 1
            self.drive.reset()
        elif (val & 0xf0) == 0xc0:
            # TODO: strictly speaking, we could mask with 0xf0 since 0x8 is an NC bit
            self.wr_state = self.WR_ST_2

    def write(self, port, val):
        pre = self.pname(port)
        _org_rd_state = self.rd_state
        match port:
            # need two commands to start a read: 0x41 and then 0x49
            case self.O_CW1:
                self.write_cw1(val)
            case self.O_CW2:
                self.write_cw2(val)
            case self.O_DATA:
                if self.wr_state == self.WR_ST_DATA:
                    self.drive.write_add_byte(val)
            case _:
                print(f"DSK_WRITE_unknown: {port=:#x} {val=:#x} {pc_disasm_str(PC_OFFSET_STD_IO)}")
        if self.verbose:
            st = f"DSK_OUT {pre:10} [{port:02x}] = {val:02x}.  rdstate {_org_rd_state}->"
            st += f"{self.rd_state} wstate {self.wr_state} T={self.track} S={self.sector}  {self.drive_no}"
            trace_write(st, include_stack=True, pc_offset=PC_OFFSET_STD_IO)

    # A few simplifications compared to a real drive:
    # - instant track move and time to next pos
    # - always ready
    def _read_status(self):
        val = 0

        if self.track == 0:
            val |= self.STATUS_T0

        if self.drive is not None:
            if self.drive.at_mark:
                val |= self.STATUS_AM
                self.drive.status_read()

            if self.drive.at_crc:
                val |= self.STATUS_CRC

        val |= self.STATUS_DRY
        return val

    def _read_data(self):
        if self.rd_state != self.RD_ST_RUN or self.drive is None:
            return 0
        if self.drive.done():
            return 0
        return self.drive.read()

    def read(self, port):
        pre = self.pname(port)
        val = 0
        match port:
            case self.I_STATUS:
                val = self._read_status()
            case self.I_DATA:
                val = self._read_data()
        if self.verbose:
            trace_write(f"DSK_INP {pre:10} [{port:02x}] : {hex(val):6}",
                        include_stack=True, pc_offset=PC_OFFSET_STD_IO)
        return val


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
    print("Sim paused")

def sim_cont():
    global sim_paused
    sim_paused = False
    print("Sim continued")


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
            print(f"Ran {iters:_} steps in {tstop-tstart:.3f} seconds ({iters/(tstop-tstart):_} steps/s)")
            sys.stdout.flush()
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
        self.raw_data = open(fname, 'rb').read_bytes()
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
        print("-- NB: Board setting proms to", "enabled" if enabled else "disabled")
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

        # TODO: this probably won't work yet.
        # The disk controller appears to be mapped to the same ports
        # as for the one used in the 1003 proms, and the code
        # appears to be fairly similar. There are probably some minor
        # differences that the disk emulator doesn't catch yet.
        print("WARNING: disk support for dim-1001 boards probably don't work yet")
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
    parser = argparse.ArgumentParser(argv)
    parser.add_argument("-t", help="Console I/O. Use if input and output are to the same device.")
    parser.add_argument("-ti", help="Console input")
    parser.add_argument("-to", help="Console output")
    parser.add_argument("-script", help="Script to send to the console input.")
    # parser.add_argument("--disk", nargs='+')
    parser.add_argument("-c", default="run-tst", help="Config directory path (and where disk images are stored)")
    parser.add_argument("-ec", help="path to connect embedded python console to")
    args = parser.parse_args()
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
    config['script']      = args.script
    config['embedded_console'] = args.ec
    print(config)
    return config


BOARD_TYPES = {
    'dim-1001' : Board1001,
    'dim-1003' : Board1003
}


def main():
    config = make_config()

    # TODO: Hack since there is currently no clean way of failing if a
    # non-existing disk is requested in the controller. The emulator currently
    # just crashes.
    # This adds some default images, with write-through if any image is modified.
    # Non-existing images will be created on the disk on the first write.
    print("Using disk image(s) from", config['disk-images'])
    d_imgs = [diskimage.DiskImage.from_file(dname) for dname in config['disk-images']]

    if len(d_imgs) > 8:
        raise ValueError(f"disk controller supports at most 8 images, got {len(d_imgs)}")

    for drivenum in range(len(d_imgs), 8):
        path = Path(config['run-dir']) / f"disk-{i:02}.img")
        d_imgs.append(diskimage.DiskImage.empty_image(path))

    with open(config["console_in"], "rb", buffering=0) as console_in, \
         open(config["console_out"], "wb", buffering=0) as console_out:

        if (board_type := BOARD_TYPES.get(config['board'], None)) is None:
            supported = ", ".join(sorted(BOARD_TYPES))
            raise ValueError(f"unsupported board {config['board']!r}; expected one of: {supported}")

        board = board_type(config, d_imgs, console_out)

        set_in_callback(board.io_in)
        set_out_callback(board.io_out)

        if config['script'] in ["t", "true"]:
            # Add some strings to automate testing
            # NB: no return after the command as the keypress would abort the dump before completion
            board.sport.schedule_bytes(0.1, "D" + "1000" + "1020")
            # L needs a "return" to work.
            # NB: L loads AND runs the program!
            board.sport.schedule_bytes(1.0, "L1CPM2.2W\r")
            board.sport.schedule_bytes(2.0, "dir\r")
            board.sport.schedule_bytes(3.0, "mycrop\r")
            board.sport.schedule_bytes(4.0, "L1CPM2.2W\r")
            # Calls 110e (BDO)
            # Emulates a disk write of data from 0x3000 to disk 04... TODO: this is broken. Use z80asm to create a new one.
            # board.sport.schedule_bytes(4, "S2000F5D5C53E0432D70FCD0E113E0406020E0216001E00210030CD8500F53AD70FCD4911F101CD0000\r")
            # board.sport.schedule_bytes(5, "G2000\r")
        elif config['script']:
            print("Adding script", config['script'])
            board.sport.schedule_bytes(0.3, config['script'])
        # sport.schedule_bytes(0.1, "D" + "1000" + "1010")

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
