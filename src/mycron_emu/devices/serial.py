#!/usr/bin/env python

import time
import heapq
from collections import deque
import logging

from mycron_emu.devices.base import IODevice
from mycron_emu import tracing

io_log = logging.getLogger("mycron.io")


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
            tracing.write(f"IOSER  OUT[{port:2x}] = {val:2x},  ser_BC reg was {self.next_reg[self.BC]:2x}: ",
                            pc_offset=tracing.PC_OFFSET_STD_IO)
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
            io_log.warning(f"read from empty serial port {port}. Returning 0 {tracing.pc_disasm_str()}")
            return 0

        io_log.warning(f"({self}): unknown port {port}. Supporting one of {self.BD=} {self.BC=} {tracing.pc_disasm_str()}")
        return 0


class IOSerialDim1001(IOSerial):
    # The serial port for the console on DIM 1001 uses a different UART and different ports.
    # TODO: __init__ might reference parent vals
    BC = 1
    BD = 0
    PORTS = (BC, BD)
