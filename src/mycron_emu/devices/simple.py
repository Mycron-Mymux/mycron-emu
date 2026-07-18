#!/usr/bin/env python

import logging

from mycron_emu.devices.base import IODevice, IOIgnore
from mycron_emu import tracing
from mycron_emu.tracing import PC_OFFSET_STD_IO


io_log = logging.getLogger("mycron.io")

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
        tracing.write(f"IO14_OUT [{port:02x}] = {val:02x}", pc_offset=PC_OFFSET_STD_IO)
        self.board.set_proms_enabled(val & 1)

    def read(self, port):
        tracing.write(f"IO14_INP [{port:02x}] : 0x{self.default_rval:x}", pc_offset=PC_OFFSET_STD_IO)
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
        tracing.write(f"IOCTC OUT [{port:02x}] = {val:02x}", pc_offset=PC_OFFSET_STD_IO)
        if val & 0x1:
            # control
            if val & 0x80:
                io_log.warning("-- NB: wanted interrupt")
    def read(self, port):
        tracing.write(f"IOCTC INP [{port:02x}] : 0x{self.default_rval:x}", pc_offset=PC_OFFSET_STD_IO)
        return self.default_rval


class IOPar(IOIgnore):
    """Parallel I/O"""
    AD = 0x4   # Port A data
    BD = 0x5   # Port B data
    AC = 0x6   # Port A control
    BC = 0x7   # Port B control
    PORTS = (AC, AD, BC, BD)

    def write(self, port, val):
        tracing.write(f"IOPAR OUT [{port:02x}] = {val:02x}", pc_offset=PC_OFFSET_STD_IO)
    def read(self, port):
        tracing.write(f"IOPAR INP [{port:02x}] : 0x{self.default_rval:x}", pc_offset=PC_OFFSET_STD_IO)
        return self.default_rval


