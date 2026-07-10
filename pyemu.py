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
from collections import defaultdict
import pathlib
from pathlib import Path
import argparse
import z80emu
from z80emu import step, set_in_callback, set_out_callback, mem_dis, mem_rd, mem_wr, get_regs, mem_set_prot
import diskimage
from emuconfig import read_config
from embedded_console import start_pty_console


# TODO: make a more flexible thing that can optionally write to a pty for output and also read from it
USE_PTY = True


def regs_str():
    """Returns a regsn and a string rep of regs with hex values"""
    regs = get_regs()
    s = "REGS_hex("
    for k, v in regs.items():
        s += f"{k}={v:x},"
    s += ")"
    return (regs, s)


def regs_stack_str():
    regs = get_regs()
    s = "REGS_hex("
    for rn in ['PC', 'SP', 'AF', 'BC', 'DE', 'HL']:
        s += f"{rn}={regs[rn]:4x},"
    s += ") Stack: ["
    sp = regs['SP']
    s += " ".join([f"{mem_rd(sp + i):02x}" for i in range(10)])
    s += "]"
    return s


class IODevice:
    ALL = []
    def __init__(self, default_rval=0):
        self.default_rval = default_rval

    def write(self, port, val):
        ...

    def read(self, port):
        return self.default_rval

    def register_ports(self, port_registry, port_list=None):
        """Register this io device to the port list in the class or the provided port list"""
        lst = self.ALL if port_list is None else port_list
        for p in lst:
            port_registry[p] = self


class IOIgnore(IODevice):
    """Ignores read or write requests, always returning a 0 if
    a write is requested"""


class IOPrint(IODevice):
    """Ignores read or write requests, always returning a 0 if
    a write is requested.
    Additionally prints OUT and INP to the console for debugging."""
    def write(self, port, val):
        print(f"OUT [{port:02x}] = {val:02x}")

    def read(self, port):
        print(f"INP [{port:02x}] : 0x{self.default_rval:x}")
        return self.default_rval


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
# -> 9b would then mean switch  8+5+4+2+1 high. But I can't find the connections for pins 1-2, maybe something else than the dip switch.
#    alternatively, the dip switches are grounded, so it may be a pull-up signal. Looks like there might be a resistor pack connected
#    along the lines up to the input side of the port
# 
# Observed behaviour:
# 
# The monitor prom writes a 1 to it and then reads 0x9b.
# - NYCPM (oja2 disk 08) writes a 0 here. Could this be disabling the PROMS!?
#   NYCPM then hangs asking for a pascal diskette
# - CPM on disk02 continuously writes 00 to that port, but doesn't seem to get further than that.
# - BOOTCPM - same. Slightly different start. Also doesn't work properly.
# 
# TODO: unprotect the PROM memory when a 0 is written to this port 
# 
class IOP14(IODevice):
    Ax14 = 0x14
    Ax15 = 0x15
    Ax16 = 0x16
    Ax17 = 0x17
    ALL = [Ax14, Ax15, Ax16, Ax17]

    # TODO: the default rval seems to influence the values written to IOCTC port 2: 80 with 9b, 20 with 1b
    def __init__(self, default_rval=0x9b):
        super().__init__(default_rval=default_rval)
        print(f"IO14 device initialized with default rval={self.default_rval:#x}. TODO: see comments.")

    def write(self, port, val):
        print(f"IO14_OUT [{port:02x}] = {val:02x}")
        match val & 0x1:
            case 0:
                board.proms_off()
            case 1:
                board.proms_on()

    def read(self, port):
        print(f"IO14_INP [{port:02x}] : 0x{self.default_rval:x}")
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
    ALL = [CH0, CH1, CH2, CH3]

    def write(self, port, val):
        print(f"IOCTC OUT [{port:02x}] = {val:02x}")
        if val & 0x1:
            # control
            if val & 0x80:
                print("-- NB: wanted interrupt")
    def read(self, port):
        print(f"IOCTC INP [{port:02x}] : 0x{self.default_rval:x}")
        return self.default_rval


class IOPar(IOIgnore):
    """Parallel I/O"""
    AD = 0x4   # Port A data
    BD = 0x5   # Port B data
    AC = 0x6   # Port A control
    BC = 0x7   # Port B control
    ALL = [AC, AD, BC, BD]

    def write(self, port, val):
        print(f"IOPAR OUT [{port:02x}] = {val:02x}")
    def read(self, port):
        print(f"IOPAR INP [{port:02x}] : 0x{self.default_rval:x}")
        return self.default_rval


# TODO: generalize to support more UARTs / serial boards
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
    ALL = [AC, AD, BC, BD]

    def __init__(self, **kvals):
        super().__init__(**kvals)
        self.s = ""
        self.queue = []
        # next register to write if write-reg contains data pointers
        self.next_reg = {
            self.AC: 0,
            self.BC: 0
        }

    def queue_string(self, at, s):
        self.queue.append([at, s])

    def write(self, port, val):
        """Emulate a write to a console"""
        match port:
            case self.BD:
                # Emulate write to console (sio B)
                # print(chr(val), end="")
                # sys.stdout.flush()
                if args.to:
                    with open(args.to, 'w') as f:
                        f.write(chr(val))
            case self.BC:
                # A bit clumsy as a first take on the register write sequences
                # This is incomplete and is just there to detect if something interesting is set up on the serial channel.
                if val != 0 or self.next_reg[self.BC] > 0:
                    # 0 is typically used when polling
                    print(f"IOSER write CB reg {self.next_reg[self.BC]} - {port:2x} {val:2x}")
                    if self.next_reg[self.BC] == 0:
                        if val & 0x7 > 0:
                            # print(f"IOSER --- next reg is {val&0x7}")
                            self.next_reg[self.BC] = val & 0x7
                    else:
                        self.next_reg[self.BC] = 0
                else:
                    self.next_reg[self.BC] = 0
                ...

    def read(self, port):
        tnow = time.time() - tstart
        if len(self.queue) > 0 and tnow > self.queue[0][0]:
            # ready to add that string to the output string
            self.s += self.queue.pop(0)[1]

        match port:
            case self.BC:
                # For polling mode, just indicate that it's always ready to transmit more + whether there is data queued.
                has_data = 1 if len(self.s) > 0 else 0   # naughty to use int(len(self.s) > 0) ?
                return 4 | has_data
            case self.BD:
                if len(self.s) > 0:
                    c = self.s[0]
                    self.s = self.s[1:]
                    return ord(c)

                
class IOSerialDim1001(IOSerial):
    # The serial port for the console on DIM 1001 uses a different UART and different ports.
    BC = 1
    BD = 0
    ALL = [BC, BD]
    

                
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
        self.set_at_state(True, False)   # Before first read, there is an address mark
        self.state = self.ST_INACTIVE

    def set_at_state(self, at_mark, at_crc):
        self.at_mark = at_mark
        self.at_crc = at_crc

    def start(self):
        """Called before first read of sector head or sector data"""
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

    def done(self):
        return self.dpos > self.SECTOR_D_SIZE + 2

    def _read_header(self):
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
            print(f"RD_SECTOR dsk={self.dno} sector {self.track:02}.{self.sector:02}")
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
                # print(f"DSK_READ_SECT_COMPLETE {self.track:02}.{self.sector:02} : {self.data}")
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
        assert len(self.wbuf) == 128
        self.data = bytes(self.wbuf)
        # print(f"DSK_WRITE_SECT_COMPLETE {self.track:02}.{self.sector:02} : {self.data}")
        self.disk_img.write_sector(self.track, self.sector, self.data, flush=True)

    def write_add_byte(self, bval):
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
    ALL = [O_CW1, O_CW2, O_DATA, I_STATUS, I_DATA]
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

    N_TRACKS = 78  # track 0..77
    N_SECTS = 26   # sector 1..26

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
        st = f"DSK_OUT {pre:10} [{port:02x}] = {val:02x}.  rdstate {self.rd_state}->"
        match port:
            # need two commands to start a read: 0x41 and then 0x49
            case self.O_CW1:
                self.write_cw1(val)
            case self.O_CW2:
                self.write_cw2(val)
            case self.O_DATA:
                if self.wr_state == self.WR_ST_DATA:
                    self.drive.write_add_byte(val)
                    # print(f"Writing byte to disk T={self.track} S={self.sector} val={val:02x}. Len of buf now {len(self.cur_sec.wbuf)}")
                else:
                    ...
                    # print(f"WRITE_ODATA state {self.wr_state} not {self.WR_ST_DATA}  {port=:#x} {val=:#x} - probably ok (prewrite stage)")
            case _:
                print(f"DSK_WRITE_unknown: {port=:#x} {val=:#x}")
        st += f"{self.rd_state} wstate {self.wr_state} T={self.track} S={self.sector}  {self.drive_no} {regs_stack_str()}"
        if self.verbose:
            print(st)

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
            print(f"DSK_INP {pre:10} [{port:02x}] : {hex(val):6} {regs_stack_str()}")
        return val


def io_in(port):
    return board.io_ports[port].read(port)


def io_out(port, val):
    board.io_ports[port].write(port, val)


def dbtrace(prev_pc, r, pc):
    # if pc == 0x34c:
    #     print(f"TTIO: pc {prev_pc:x}->{pc:x}")
    if (pc > 0x2000 or prev_pc > 0x2000) and pc < 0x2100:
        s = ""
        # s = regs_str()[1]
        s += regs_stack_str()
        print(f"{prev_pc:04x} -> {pc:04x} {str(mem_dis(pc)):30} {s}")
        if pc in [0x1042, 0x105d]:
            # Verify start of LOAD - BC has pointer to filename
            regs, regs_s = regs_str()
            bcmem = [hex(mem_rd(regs['BC'] + i)) for i in range(10)]
            print("DSK_LOAD_Entry", regs_s, bcmem)
        if pc in [0x107a]:
            bcmem1 = [hex(mem_rd(regs['DE'] + i)) for i in range(10)]
            bcmem2 = [hex(mem_rd(regs['HL'] + i)) for i in range(10)]
            print("DSK_LOAD_CHCMP", regs_s, bcmem1, bcmem2)
        if pc in [0x2000, 0x3000]:
            bcmem1 = [hex(mem_rd(0x3000 + i)) for i in range(32)]
            bcmem2 = [hex(mem_rd(0x4000 + i)) for i in range(32)]
            print("PROGDUMP CODE", bcmem1)
            print("PROGDUMP DATA", bcmem2)
        if 0x2000 <= pc <= 0x2100:
            print('step: ', regs_stack_str())


def check_console():
    # Keyboard / Console input
    if (plist := ch_in_p.poll(0)):
        ch = ch_in.read(1)
        # print("Got poll", plist, ch)
        if ch == "\r":
            print("YES, GOT cr")
        if ch == "\n":
            ch = "\r"  # doesn't expect newline...
        board.sport.queue_string(0.01, ch.decode("UTF-8"))

            
def run_step(prev_pc):
    check_console()
    r, pc = step()
    if 0:
        dbtrace(prev_pc, r, pc)
    return pc


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
    

def run_sim_stepmode():
    global tstart
    use_steps = False
    # use_steps = True
    
    if use_steps:
        print("TODO: ... turn off protection for proms in single step mode?")

    # Start running simulator
    N = 3_550_000
    prev_pc = 0
    tstart = time.time()
    if use_steps:
        for i in range(N):
            prev_pc = run_step(prev_pc)
    else:
        z80emu.run(N)
    tstop = time.time()
    print(f"Ran {N:_} steps in {tstop-tstart:.3f} seconds ({N/(tstop-tstart):_} steps/s)")
    mem = [hex(mem_rd(p)) for p in range(0x0000, 0x0010)]
    print("0x0000", mem)
    mem = [hex(mem_rd(p)) for p in range(0x1000, 0x1010)]
    print("0x1000", mem)
    sys.stdout.flush()

    if use_steps:
        while True:
            if sim_paused:
                time.sleep(1)
                continue
            prev_pc = run_step(prev_pc)
    else:
        # TODO: this has a few issues. Python needs to run sometimes and
        # run_step also polls the console port.
        # Polling once in a while works though.
        # ^C out of the proam doesn't work reliably. Something to do with running
        # inside a C ext? Maybe the PyErr_CheckSignals() call is enough.
        # TODO: (move else where) - delayed print since we don't flush everywhere?. 
        while True:
            if sim_paused:
                time.sleep(1)
                continue
            check_console()
            z80emu.run(1000)
            # z80emu.run(0)


def dump_mem(fname):
    """TODO: this function could be more optimized"""
    was_paused = sim_paused
    print(f"Dumping memory to {fname}. Pausing simulator.")
    sim_pause()
    with open(fname, "wb") as f:
        vals = [mem_rd(addr) for addr in range(0x10000)]
        mem = bytes(vals)
        f.write(mem)
    print(f" - done dumping memory to {fname}. Restoring pause state of simulator.")
    if not was_paused:
        sim_cont()


class PromRegion:
    def __init__(self, fname, start_addr):
        self.fname = fname
        self.start_addr = start_addr
        self.raw_data = open(fname, 'rb').read()
        self.ram_vals = [0] * len(self.raw_data)
        self.is_on = False
        self.turn_on()

    def __len__(self):
        return len(self.raw_data)

    def addr_range(self):
        return range(self.start_addr, self.start_addr + len(self))

    def _update_ramview(self):
        if not self.is_on:
            # Only store new ram values if the prom was off
            self.ram_vals = [mem_rd(addr) for addr in self.addr_range()]

    def turn_on(self):
        """Turn on PROM, hiding RAM. Also protects prom region from writes."""
        self._update_ramview()
        # store prom in emulator's memory
        for addr, val in enumerate(self.raw_data, start=self.start_addr):
            mem_wr(addr, val)
        mem_set_prot(self.start_addr, self.start_addr + len(self) - 1, 1)
        self.is_on = True
        
    def turn_off(self):
        """Turn off PROM, exposing RAM. Unprotects region."""
        # overwrite prom region with RAM data
        for addr, val in enumerate(self.ram_vals, start=self.start_addr):
            mem_wr(addr, val)
        mem_set_prot(self.start_addr, self.start_addr + len(self) -1, 0)
        self.is_on = False

    def save_ram(self, fname):
        print(f"Saving RAM (covered by PROM) from addr {self.start_addr} as {fname}")
        self._update_ramview()
        with open(fname, 'wb') as f:
            f.write(bytes(self.ram_vals))
        
        

class Board:
    btypes = {}
    def __init__(self, config):
        self.config = config
        # Set up I/O address space
        self.io_ports = defaultdict(IOPrint)
        self.proms = []

    def proms_on(self):
        print(" -- NB: Turning on PROM chips")
        for prom in self.proms:
            prom.turn_on()
    def proms_off(self):
        print(" -- NB: Turning OFF PROM chips")
        for prom in self.proms:
            prom.turn_off()


class Board1001(Board):
    def __init__(self, config):
        super().__init__(config)
        self.sport = IOSerialDim1001()
        self.sport.register_ports(self.io_ports)

        # Dim 1001 uses only one prom chip.
        self.proms.append(PromRegion(Path(args.c, config['prom0']), 0x0))
        self.proms_on()

        # TODO: this probably won't work yet.
        # The disk controller appears to be mapped to the same ports
        # as for the one used in the 1003 proms, and the code
        # appears to be fairly similar. There are probably some minor
        # differences that the disk emulator doesn't catch yet.
        print("WARNING: disk support for dim-1001 boards probably don't work yet")
        self.dsk = IODiskController(d_imgs)
        self.dsk.register_ports(self.io_ports)


class Board1003(Board):
    def __init__(self, config):
        super().__init__(config)
        self.sport = IOSerial()
        self.sport.register_ports(self.io_ports)
        self.pport = IOPar()
        self.pport.register_ports(self.io_ports)

        if 0:
            # For now, just ignore the counter/timer. TODO: fix this.
            self.ign = IOIgnore()
            self.ign.register_ports(self.io_ports, IOCTC.ALL)
        else:
            self.ctcdev = IOCTC()
            self.ctcdev.register_ports(self.io_ports)

        self.iop14 = IOP14()
        self.iop14.register_ports(self.io_ports)

        # Dim 1003 uses two proms. The second one is mapped at 0x1000, leaving a region of RAM between the chips.
        self.proms.append(PromRegion(Path(args.c, config['prom0']), 0x0))
        self.proms.append(PromRegion(Path(args.c, config['prom1']), 0x1000))
        self.proms_on()
        
        self.dsk = IODiskController(d_imgs)
        self.dsk.register_ports(self.io_ports)

    
parser = argparse.ArgumentParser()
parser.add_argument("-t", help="Console I/O. Use if input and output are to the same device.")
parser.add_argument("-ti", help="Console input")
parser.add_argument("-to", help="Console output")
parser.add_argument("-script", help="Script to send to the console input.")
# parser.add_argument("--disk", nargs='+')
parser.add_argument("-c", default="run-tst", help="Config directory path (and where disk images are stored)")
parser.add_argument("-ec", help="path to connect embedded python console to")
args = parser.parse_args()

if args.t:
    # Use the same pty for both input and output
    args.ti = args.t
    args.to = args.t

config = read_config(args.c)
print(config)

# TODO: Hack since there is no clean way of failing if a non-existing disk is requested in the controller.
# currently, the emulator just crashes. This adds some default images.
print("Using disk image(s) from", config['disk-images'])
d_imgs = [diskimage.DiskImage.from_file(dname) for dname in config['disk-images']]
while len(d_imgs) < 8:
    dname = pathlib.Path(args.c) / f"disk-{len(d_imgs):02}.img"
    print("Adding empty image", dname)
    d_imgs.append(diskimage.prog_make_test_img(dname))

btypes = {
    'dim-1001' : Board1001,
    'dim-1003' : Board1003
}
btype = btypes[config['board']]
board = btype(config)

set_in_callback(io_in)
set_out_callback(io_out)

if args.script in ["t", "true"]:
    # Add some strings to automate testing
    # NB: no enter after this as it would abort it after the first line
    board.sport.queue_string(0.1, "D" + "1000" + "1100")
    # This one needs a "return" to work.
    # NB: L loads AND runs the program!
    board.sport.queue_string(0.3, "L1foo\r")
    # sport.queue_string(0.3, "S2000F5D5C53D0032D70FCD0E113D0006020E0216001E00210030CD8500F53AD70FCD4911F101CD0000\r")
elif args.script:
    print("Adding script", args.script)
    board.sport.queue_string(0.3, args.script)
# sport.queue_string(0.1, "D" + "1000" + "1010")


# Slightly hacky, but this lets us read from the serial port and put it in the
# queue of the emulator's serial port.
# NB: need to set it to binary + unbuffered, otherwise terminal input will be buffered and not work properly
ch_in = open(args.ti, 'rb', 0)    
ch_in_p = select.poll()
ch_in_p.register(ch_in.fileno(), select.POLLIN)

# If embedded console: 
if args.ec:
    # Give it everything
    console_server = start_pty_console(args.ec, globals())

# track cpm loading
# z80emu.mem_set_track_mask(0xee00, 0xffff, z80emu.TRACK_EXEC)

run_sim_stepmode()
